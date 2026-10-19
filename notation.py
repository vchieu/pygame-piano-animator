"""
notation.py - Drawing notes the way MuseScore draws them.

SMuFL (Standard Music Font Layout) is a set of music symbols that live in an
ordinary font as characters: noteheads, flags, accidentals, augmentation dots
and so on. This module draws them out of Leland, the music font MuseScore
engraves with by default (bundled in assets/fonts, SIL Open Font License 1.1,
the licence beside it), at MuseScore's metrics:

- sizes are in staff spaces (sp), as MuseScore's style defaults are
  (`ENGRAVING`, from src/engraving/style/styledef.cpp);
- a glyph is placed by its SMuFL anchor, not by arithmetic - a stem starts at
  the notehead's `stemUpSE` / `stemDownNW` anchor and a flag at its own
  `stemUpNW` / `stemDownSW` (Note::stemUpSE, note.cpp:1270-1286, and
  TDraw::draw(const ShadowNote*), tdraw.cpp:2598-2695, which draws
  accidental -> notehead -> dots -> stem + flag in that order).

The grand staff draws through here. The falling notes over the keyboard and
the editor's piano roll keep their own plain blocks: they are bars of time
as much as notes, and the app's own look is what they are for.

The font is opened lazily, when the first glyph is asked for: importing this
module (or starting the app with no font at all) is harmless, and every
painter falls back to the plain shapes it drew before Leland was there, so a
stripped build still works.
"""
from __future__ import annotations

import pygame

import paths
from common import mix

# --------------------------------------------------------------------------- #
# MuseScore's metrics
# --------------------------------------------------------------------------- #
# SMuFL: one em (the font's size) is four staff spaces. Everything below is in sp.
SP_PER_EM = 4

# MuseScore's style defaults (src/engraving/style/styledef.cpp:263-313, and the
# same engravingDefaults in Leland's own metadata, fonts/leland/leland_metadata.json).
ENGRAVING = {
    "stemWidth": 0.10,                 # styledef.cpp:267  (stemThickness)
    "stemLength": 3.5,                 # styledef.cpp:268
    "beamWidth": 0.5,                  # styledef.cpp:303  (beamThickness)
    "staffLineWidth": 0.11,            # styledef.cpp:284  (staffLineThickness)
    "ledgerLineWidth": 0.16,           # styledef.cpp:285  (legerLineThickness)
    "ledgerLineLength": 0.33,          # styledef.cpp:286  (legerLineExtension)
    "dotNoteDistance": 0.5,            # styledef.cpp:311
    "dotDotDistance": 0.65,            # styledef.cpp:313
    "accidentalNoteDistance": 0.25,    # styledef.cpp (accidentalNoteDistance)
}
STAFF_LINE_SP = ENGRAVING["staffLineWidth"]
STEM_SP = ENGRAVING["stemWidth"]
STEM_LEN_SP = ENGRAVING["stemLength"]
BEAM_SP = ENGRAVING["beamWidth"]
LEDGER_SP = ENGRAVING["ledgerLineWidth"]
LEDGER_EXT_SP = ENGRAVING["ledgerLineLength"]
DOT_DIST_SP = ENGRAVING["dotNoteDistance"]
DOT_GAP_SP = ENGRAVING["dotDotDistance"]
ACC_DIST_SP = ENGRAVING["accidentalNoteDistance"]

# The noteheads Leland draws, and each one's ink box in sp (bBoxSW..bBoxNE, y up,
# from fonts/leland/leland_metadata.json): black and half 1.300 x 1.060 sp, whole
# a little wider and taller.
HEADS = {"whole": "noteheadWhole", "half": "noteheadHalf", "black": "noteheadBlack"}
HEAD_BOX = {                     # head -> (left, top, right, bottom) in sp, y up
    "whole": (0.0, 0.544, 1.492, -0.536),
    "half": (0.0, 0.528, 1.300, -0.532),
    "black": (0.0, 0.528, 1.300, -0.532),
}
NOTEHEAD_W_SP = HEAD_BOX["black"][2]                                   # 1.300 sp
NOTEHEAD_H_SP = HEAD_BOX["black"][1] - HEAD_BOX["black"][3]           # 1.060 sp

# SMuFL codepoints (a public standard; MuseScore's SymIds for the same glyphs).
GLYPHS = {
    "noteheadWhole": 0xE0A2,
    "noteheadHalf": 0xE0A3,
    "noteheadBlack": 0xE0A4,
    "accidentalSharp": 0xE262,
    "augmentationDot": 0xE1E7,
    "flag8thUp": 0xE240,
    "flag8thDown": 0xE241,
    "flag16thUp": 0xE242,
    "flag16thDown": 0xE243,
    "flag32ndUp": 0xE244,
    "flag32ndDown": 0xE245,
}
# A stem's flags, by how many the note has: the 16th-note glyph carries both of
# them, as in MuseScore, where a stem has a single Hook (hook.cpp:symIdForHookIndex).
FLAGS = {0: None, 1: "8th", 2: "16th", 3: "32nd"}

# Leland's SMuFL anchors (fonts/leland/leland_metadata.json, glyphsWithAnchors),
# with the y negated as MuseScore does - it draws y down, SMuFL counts y up
# (engravingfont.cpp:696-752).
STEM_UP_SE = (1.3, -0.16)          # noteheadBlack.stemUpSE
STEM_DOWN_NW = (0.0, 0.168)        # noteheadBlack.stemDownNW
FLAG_UP_NW = (0.0, 0.008)          # flag8thUp.stemUpNW
FLAG_DOWN_SW = (0.0, -0.004)       # flag8thDown.stemDownSW

# --------------------------------------------------------------------------- #
# The font and the glyph cache
# --------------------------------------------------------------------------- #
FONT_FILE = "Leland.otf"
REF_SP = 64.0            # every mask is rendered at this size and scaled down (64 px/sp)
SP_STEP = 0.25           # sp is quantised to quarter-space steps, to bound the cache
CACHE_MAX = 256          # ...and the cache is emptied outright past this many

_font = None             # pygame.freetype.Font, opened on the first glyph (False: missing)
_quit_hooked = False     # pygame.quit() has been told to drop the font and its glyphs
_masks = {}              # name -> (surface, ox, oy): the white ink at REF_SP, and where
                         #   its origin sits in it (y down) in sp
_glyphs = {}             # (name, sp, color) -> Glyph


class Glyph:
    """One glyph in one colour at one size: its ink, and where the origin sits in it."""

    __slots__ = ("surface", "ox", "oy")

    def __init__(self, surface, ox, oy):
        self.surface = surface
        self.ox = ox
        self.oy = oy

    def draw(self, surf, x, y):
        """Blit the glyph with its origin at (x, y)."""
        surf.blit(self.surface, (round(x + self.ox), round(y + self.oy)))

    @property
    def size(self):
        return self.surface.get_size()


def _load_font():
    """Open Leland once; None if it isn't there (a stripped build)."""
    global _font, _quit_hooked
    if _font is None:
        if not _quit_hooked:
            # a font kept past pygame.quit() crashes when it's used: drop it then
            # (pygame calls a quit function once, so it's registered again whenever
            # the cache starts over - as common.ui_font does for the typeface)
            pygame.register_quit(clear_cache)
            _quit_hooked = True
        try:
            _font = pygame.freetype.Font(paths.resource("assets", "fonts", FONT_FILE),
                                         int(REF_SP * SP_PER_EM))
        except (pygame.error, OSError):
            _font = False                    # tried and missing: don't try again
    return _font or None


def available() -> bool:
    """Is the music font there? (Every painter falls back to plain shapes when it isn't.)"""
    return _load_font() is not None


def clear_cache():
    """Forget the font and every glyph drawn from it (pygame.quit()'s hook)."""
    global _font, _quit_hooked
    _masks.clear()
    _glyphs.clear()
    _font = None
    _quit_hooked = False


def _mask(name):
    """
    A glyph's white ink at REF_SP and where its origin sits in it (y down, in sp),
    both measured from the font so every size below is a plain scale of this.
    None when the font or the glyph is missing.
    """
    mask = _masks.get(name)
    if mask is not None:
        return mask if mask != "missing" else None
    font = _load_font()
    cp = GLYPHS.get(name)
    if font is None or cp is None:
        _masks[name] = "missing"
        return None
    surface, rect = font.render(chr(cp), (255, 255, 255))
    if surface.get_width() < 1 or surface.get_height() < 1:
        _masks[name] = "missing"
        return None
    # rect.x is the ink's left edge from the origin; rect.y is how far the ink's top
    # reaches *up*, so the surface (y down) hangs rect.y below the origin: -rect.y.
    mask = _masks[name] = (surface, rect.x / REF_SP, -rect.y / REF_SP)
    return mask


def glyph(name, sp, color):
    """A glyph in `color` at `sp` staff spaces (sp is rounded to a quarter space).
    None when there is no font, or no such glyph in it."""
    sp = max(0.05, round(sp / SP_STEP) * SP_STEP)
    key = (name, sp, tuple(color))
    g = _glyphs.get(key)
    if g is not None:
        return g if g != "missing" else None
    mask = _mask(name)
    if mask is None:
        _glyphs[key] = "missing"
        return None
    surface, ox_sp, oy_sp = mask
    size = (max(1, round(surface.get_width() * sp / REF_SP)),
            max(1, round(surface.get_height() * sp / REF_SP)))
    small = pygame.transform.smoothscale(surface, size)
    small.fill(color, special_flags=pygame.BLEND_RGBA_MULT)
    if len(_glyphs) > CACHE_MAX:
        _glyphs.clear()
    g = _glyphs[key] = Glyph(small, ox_sp * sp, oy_sp * sp)
    return g


def stem_up_se_sp():
    """Leland's noteheadBlack.stemUpSE anchor in sp, y down like MuseScore's."""
    return STEM_UP_SE


def stem_down_nw_sp():
    """Leland's noteheadBlack.stemDownNW anchor in sp, y down like MuseScore's."""
    return STEM_DOWN_NW


# --------------------------------------------------------------------------- #
# What a duration looks like
# --------------------------------------------------------------------------- #
def note_shape(duration, beat_len=0.5):
    """
    A duration's shape: (head, flags, dots) - "black" / "half" / "whole", how many
    flags on its stem, how many dots. The thresholds are the ones staff.note_value
    has always used (staff.py:196-214), which is what lays the staff out.
    """
    beats = duration / max(0.001, beat_len)
    if beats >= 3.2:
        return "whole", 0, 0
    if beats >= 2.4:
        return "half", 0, 1
    if beats >= 1.6:
        return "half", 0, 0
    if beats >= 1.2:
        return "black", 0, 1
    if beats >= 0.7:
        return "black", 0, 0
    if beats >= 0.55:
        return "black", 1, 1
    if beats >= 0.35:
        return "black", 1, 0
    return "black", 2, 0


# --------------------------------------------------------------------------- #
# Drawing
# --------------------------------------------------------------------------- #
def _stem(surf, color, x_anchor, y_from, y_to, sp, up):
    """The stem, from a notehead anchor to a tip: MuseScore draws it with a solid pen and
    flat caps, its width stemWidth and its outer edge on the anchor (tdraw.cpp:2850-2922)."""
    w = max(1, round(STEM_SP * sp))
    x = round(x_anchor - (w if up else 0) / 2.0)
    pygame.draw.line(surf, color, (x, round(y_from)), (x, round(y_to)), w)


def _flag(surf, sp, x_stem, y_tip, count, up, color):
    """A stem's flags, hung off its tip by the flag's own stemUpNW / stemDownSW anchor."""
    kind = FLAGS.get(count)
    if not kind:
        return
    g = glyph(f"flag{kind}{'Up' if up else 'Down'}", sp, color)
    if g is None:
        return
    w = max(1, round(STEM_SP * sp))
    _, ay = FLAG_UP_NW if up else FLAG_DOWN_SW
    g.draw(surf, x_stem + (w / 2.0 if up else -w / 2.0), y_tip + ay * sp)


def _dots(surf, sp, x_head_right, y, count, color):
    """The augmentation dots, the first one dotNoteDistance right of the notehead."""
    g = glyph("augmentationDot", sp, color)
    if g is None:
        return
    x = x_head_right + DOT_DIST_SP * sp
    for i in range(count):
        g.draw(surf, x + i * DOT_GAP_SP * sp, y)


def draw_staff_note(surf, x, y, sp, shape, color, stem_up, sounding=False,
                    stem=None, beamed=False, sharp=False, on_line=False):
    """
    A note on the staff, MuseScore's way: the accidental, the notehead, the dots and
    the stem with its flags (TDraw::draw(const ShadowNote*), tdraw.cpp:2598-2695).

    `x, y` is where the notehead sits: its centre, on its line or space.
    `stem` is (from_y, to_y), the stem's ends - the caller works them out, so the
    notes of a chord share one stem and a beamed note's stem reaches its beam; None
    leaves the stem, and its flags, out (a whole note, or a chord's other notes).
    `beamed`: this note's flags are a beam's, which the caller draws.
    `sharp`: draw the accidental left of the head. `on_line`: the note is on a line,
    so its dot rises into the space above (tdraw.cpp:2648). A sounding note is drawn
    lighter, as the staff has always drawn it.

    Without the font this draws the plain ellipses it drew before Leland.
    """
    if sounding:
        color = mix(color, (255, 255, 255), 0.5)
    head, flags, dots = shape
    g = glyph(HEADS[head], sp, color)
    if g is None:
        _fallback_staff_note(surf, x, y, sp, shape, color, stem_up, stem, sharp, on_line)
        return

    box = HEAD_BOX[head]
    ox = x - (box[2] - box[0]) * sp / 2.0 - box[0] * sp      # the head is centred on (x, y)
    oy = y - (box[1] + box[3]) * sp / 2.0

    if sharp:                                               # the accidental, left of the head
        acc = glyph("accidentalSharp", sp, color)
        if acc is not None:
            acc.draw(surf, ox - (box[2] - box[0]) * sp - ACC_DIST_SP * sp, oy)

    g.draw(surf, ox, oy)

    if dots:                                                # on a line, the dot rises a half space
        _dots(surf, sp, ox + box[2] * sp, oy - (sp / 2 if on_line else 0), dots, color)

    if stem is not None:
        ax, ay = STEM_UP_SE if stem_up else STEM_DOWN_NW
        _stem(surf, color, ox + ax * sp, oy + ay * sp, stem[1], sp, stem_up)
        if flags and not beamed:
            _flag(surf, sp, ox + ax * sp, stem[1], flags, stem_up, color)


def _fallback_staff_note(surf, x, y, sp, shape, color, stem_up, stem, sharp, on_line):
    """A staff note as it was drawn before the music font: an ellipse, a line, a dot."""
    head, flags, dots = shape
    hw = round(sp * (0.65 if head == "black" else 0.75))
    hh = round(sp * 0.53)
    box = pygame.Rect(round(x - hw), round(y - hh), hw * 2, hh * 2)
    if sharp:
        ax, ay, w, h = x - hw - round(sp * 0.8), y, sp * 0.3, sp * 0.45
        pygame.draw.line(surf, color, (round(ax - w), round(ay - h)), (round(ax - w), round(ay + h)), 1)
        pygame.draw.line(surf, color, (round(ax + w), round(ay - h)), (round(ax + w), round(ay + h)), 1)
        pygame.draw.line(surf, color, (round(ax - w * 2), round(ay - h * 0.4)), (round(ax + w * 2), round(ay - h)), 2)
        pygame.draw.line(surf, color, (round(ax - w * 2), round(ay + h * 0.2)), (round(ax + w * 2), round(ay - h * 0.2)), 2)
    if head == "black":
        pygame.draw.ellipse(surf, color, box)
        pygame.draw.ellipse(surf, mix(color, (0, 0, 0), 0.4), box, 1)
    else:
        th = max(1, int(sp * 0.14))
        pygame.draw.ellipse(surf, mix(color, (0, 0, 0), 0.3), box)
        inner = box.inflate(-th * 2, -th * 2)
        if inner.w > 0 and inner.h > 0:
            pygame.draw.ellipse(surf, color, inner)
    if dots:
        pygame.draw.circle(surf, mix(color, (0, 0, 0), 0.2),
                           (round(x + hw + max(2, round(sp * 0.35))),
                            round(y - (sp * 0.5 if on_line else 0))), max(1, int(sp * 0.2)))
    if stem is not None:
        sx = round(x + hw - 1) if stem_up else round(x - hw + 1)
        pygame.draw.line(surf, color, (sx, round(stem[0])), (sx, round(stem[1])),
                         max(1, round(STEM_SP * sp)))
        if flags:
            fy = stem[1] + (1 if stem_up else -1) * sp * 1.2
            pygame.draw.line(surf, color, (sx, round(stem[1])), (round(sx + sp * 0.9), round(fy)),
                             max(1, round(sp * 0.2)))


def ledger_line(surf, x0, x1, y, sp, color):
    """A ledger line: MuseScore's thickness (ledgerLineWidth), flat-capped (tdraw.cpp:2185-2201)."""
    pygame.draw.line(surf, color, (round(x0), round(y)), (round(x1), round(y)),
                     max(1, round(LEDGER_SP * sp)))