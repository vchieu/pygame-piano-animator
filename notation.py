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

import math

import pygame

try:
    import pygame.freetype    # `import pygame` alone does not load it in every pygame
except ImportError:           # build: without it the music font simply never opens,
    pass                      # and every painter falls back to its plain shapes

import paths
from common import mix, ui_font

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
    "graceNoteMag": 0.7,               # styledef.cpp:524 - grace notes' size
    "graceToMainNoteDist": 0.45,       # styledef.cpp:525 - grace -> note it leans on
    "graceToGraceNoteDist": 0.3,       # styledef.cpp:526 - one grace -> the next
    "stemSlashPosition": 2.0,          # styledef.cpp:287 - acciaccatura's stroke
    "stemSlashAngle": 40.0,            # styledef.cpp:288 (degrees off horizontal)
    "stemSlashThickness": 0.125,       # styledef.cpp:289
    "tieEndpointThickness": 0.05,      # styledef.cpp:616 (tieEndWidth) - a tie's ends
    "tieMidpointThickness": 0.21,      # styledef.cpp:617 (tieMidWidth) - its swell
    "minTieLength": 1.0,               # styledef.cpp:619 - shorter than this, a tie is flat
    "tieMinShoulderHeight": 0.3,       # styledef.cpp:635 - how far the arc rises
    "tieMaxShoulderHeight": 2.0,       # styledef.cpp:636
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
# The accidental a written <alter> asks for: -2 a double flat .. +2 a double sharp,
# 0 a natural (a score spells one out where a bar's earlier note changed the pitch).
ACCIDENTAL_GLYPHS = {-2: "accidentalDoubleFlat", -1: "accidentalFlat", 0: "accidentalNatural",
                     1: "accidentalSharp", 2: "accidentalDoubleSharp"}
GRACE_MAG = ENGRAVING["graceNoteMag"]
GRACE_TO_MAIN_SP = ENGRAVING["graceToMainNoteDist"]
GRACE_TO_GRACE_SP = ENGRAVING["graceToGraceNoteDist"]
STEM_SLASH_POS_SP = ENGRAVING["stemSlashPosition"]
STEM_SLASH_ANGLE = ENGRAVING["stemSlashAngle"]
STEM_SLASH_THICKNESS_SP = ENGRAVING["stemSlashThickness"]
TIE_END_SP = ENGRAVING["tieEndpointThickness"]
TIE_MID_SP = ENGRAVING["tieMidpointThickness"]
MIN_TIE_LENGTH_SP = ENGRAVING["minTieLength"]
TIE_MIN_SHOULDER_SP = ENGRAVING["tieMinShoulderHeight"]
TIE_MAX_SHOULDER_SP = ENGRAVING["tieMaxShoulderHeight"]
# SlurTieLayout::computeBezier pulls the Bézier's control points inboard by
# shoulderW, so the arc's shoulders (its widest part) stand 0.6 of the tie's
# width apart (slurtielayout.cpp:2714,2717-2718).
TIE_SHOULDER_W = 0.6
# TLayout::layoutStemSlash pulls a slash with no hook to reach for to 0.66 of
# stemSlashPosition, closer to the stem's tip (tlayout.cpp:5393,5408).
STEM_SLASH_HEIGHT_REDUCTION = 0.66
# A tuplet's bracket: how far its legs reach towards the notes, and the white the
# line keeps either side of the number standing in the gap at its middle.
TUPLET_LEG_SP = 0.4
TUPLET_GAP_SP = 0.35

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

# SMuFL puts a clef's origin (GlyphOrigin) on the staff line the clef names, so placing
# one is arithmetic rather than a fudge: gClef on G4 (the treble staff's second line from
# the bottom, mid_y + s), fClef on F3 (the bass staff's second line from the top,
# mid_y - s). Each clef's ink, measured from Leland at 256 px: (width, top, bottom) in
# sp relative to the origin, y up - a treble clef reaches 4.469 sp above its line and
# 2.672 sp below it, so it fills the whole treble staff and a little more.
CLEF_BOX = {                    # clef -> (ink width, top, bottom) in sp, y up
    "gClef": (2.562, 4.469, -2.672),
    "fClef": (2.656, 1.000, -2.469),
}
# A time signature's digits are centred on their own line: a numerator on the staff line
# above the middle one, a denominator on the line below, each filling the two and a bit
# spaces either side of it (Leland's digits are 1.30-1.73 sp wide and 1.95-2.03 sp high).
TIME_SIG_HALF_W_SP = 0.87                               # half the widest digit (a 6)
DIGIT_GAP_SP = 0.06                                     # a two-digit number's gap

# SMuFL codepoints (a public standard; MuseScore's SymIds for the same glyphs).
GLYPHS = {
    "noteheadWhole": 0xE0A2,
    "noteheadHalf": 0xE0A3,
    "noteheadBlack": 0xE0A4,
    "accidentalFlat": 0xE260,
    "accidentalNatural": 0xE261,
    "accidentalSharp": 0xE262,
    "accidentalDoubleSharp": 0xE263,
    "accidentalDoubleFlat": 0xE264,
    "augmentationDot": 0xE1E7,
    "flag8thUp": 0xE240,
    "flag8thDown": 0xE241,
    "flag16thUp": 0xE242,
    "flag16thDown": 0xE243,
    "flag32ndUp": 0xE244,
    "flag32ndDown": 0xE245,
    "gClef": 0xE050,
    "fClef": 0xE062,
    # The time signature's digits (SMuFL "Time signatures", U+E080-U+E089). There is
    # no timeSig12 glyph: a 12 is a 1 and a 2 side by side (SMuFL gives them as a ligature).
    "timeSig0": 0xE080,
    "timeSig1": 0xE081,
    "timeSig2": 0xE082,
    "timeSig3": 0xE083,
    "timeSig4": 0xE084,
    "timeSig5": 0xE085,
    "timeSig6": 0xE086,
    "timeSig7": 0xE087,
    "timeSig8": 0xE088,
    "timeSig9": 0xE089,
    # The tuplets' digits (SMuFL "Tuplets", U+E880-U+E889): a triplet's "3" over
    # its bracket, in the tuplets' own weight - heavier than a time signature's.
    "tuplet0": 0xE880,
    "tuplet1": 0xE881,
    "tuplet2": 0xE882,
    "tuplet3": 0xE883,
    "tuplet4": 0xE884,
    "tuplet5": 0xE885,
    "tuplet6": 0xE886,
    "tuplet7": 0xE887,
    "tuplet8": 0xE888,
    "tuplet9": 0xE889,
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

    def draw_centred(self, surf, x, y):
        """Blit the glyph with its ink's centre at (x, y) - for a self-centred glyph
        (a time signature's digit) rather than one placed by its SMuFL origin."""
        w, h = self.size
        self.draw(surf, x - (self.ox + w / 2.0), y - (self.oy + h / 2.0))

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
            freetype = getattr(pygame, "freetype", None)     # a build without it: no font
            if freetype is None:
                raise OSError("pygame.freetype is not available")
            freetype.init()             # in case the app never called pygame.init()
            _font = freetype.Font(paths.resource("assets", "fonts", FONT_FILE),
                                  int(REF_SP * SP_PER_EM))
        except (pygame.error, OSError, RuntimeError, AttributeError):
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
def draw_clef(surf, x, line_y, sp, color, name) -> bool:
    """
    A clef, placed the way SMuFL means it: its origin on the staff line the clef names
    (`line_y` - G4 for a treble clef, F3 for a bass one) and its ink's left edge at `x`.

    False when there is no font, or no such glyph in it: the caller then draws the plain
    shape it drew before Leland was there.
    """
    g = glyph(name, sp, color)
    if g is None:
        return False
    g.draw(surf, x - g.ox, line_y)
    return True


def _digit_row(surf, digits, x, y, sp):
    """A row of already-loaded time-signature digits, centred on (x, y)."""
    widths = [g.size[0] for g in digits]
    gap = DIGIT_GAP_SP * sp
    left = x - (sum(widths) + gap * (len(digits) - 1)) / 2.0
    for g, w in zip(digits, widths):
        g.draw_centred(surf, left + w / 2.0, y)
        left += w + gap


def draw_time_signature(surf, x, mid_y, sp, num, den, color) -> bool:
    """
    A time signature, as MuseScore writes it (Clef::drawStaffTimeSig, clef.cpp): the
    numerator centred on the staff line above the middle one and the denominator on the
    line below, the whole column centred on `x` at the staff's `mid_y`.

    False when there is no font, or no such digit in it: the caller then draws the plain
    shape it drew before Leland was there.
    """
    rows = []
    for value in (num, den):
        digits = [glyph(f"timeSig{d}", sp, color) for d in str(value)]
        if any(g is None for g in digits):
            return False
        rows.append(digits)
    for digits, line_y in zip(rows, (mid_y - sp, mid_y + sp)):
        _digit_row(surf, digits, x, line_y, sp)
    return True


def _stem(surf, color, x_anchor, y_from, y_to, sp, up):
    """The stem, from a notehead anchor to a tip: MuseScore draws it with a solid pen and
    flat caps, its width stemWidth and its outer edge on the anchor (tdraw.cpp:2850-2922)."""
    w = max(1, round(STEM_SP * sp))
    x = round(x_anchor - (w if up else 0) / 2.0)
    pygame.draw.line(surf, color, (x, round(y_from)), (x, round(y_to)), w)


def _stem_slash(surf, sp, x_stem, y_tip, up, color):
    """
    An acciaccatura's stroke across its stem, in place of a flag.

    MuseScore lays the StemSlash off at stemSlashAngle from the horizontal, starting
    stemSlashPosition below the stem's tip (measured from the tip, so a stem pointing
    down gets it above) and reaching a notehead's width across, a stem's width less
    (TLayout::layoutStemSlash, tlayout.cpp:5391-5446). `sp` is already the grace's own
    size (graceNoteMag times the staff's), so MuseScore's `mag` factor is in it.

    A slash with no hook to reach for is pulled closer to the tip - heightReduction,
    0.66 of stemSlashPosition (tlayout.cpp:5393,5408) - which is the acciaccatura this
    draws, every grace note we slash being flagless.
    """
    l, _t, r, _b = HEAD_BOX["black"]
    half = (r - l) * sp * 0.5
    stem_w = STEM_SP * sp
    pos = STEM_SLASH_POS_SP * sp * STEM_SLASH_HEIGHT_REDUCTION
    dx = (r - l - STEM_SP) * sp
    dy = dx * math.tan(math.radians(STEM_SLASH_ANGLE))
    # the stem's far edge - the notehead's right for an up stem, its own for a down one
    far = x_stem if up else x_stem + stem_w
    x0, x1 = far - half, far + half - stem_w
    y0 = y_tip + pos if up else y_tip - pos
    y1 = y0 - dy if up else y0 + dy
    w = max(1, round(STEM_SLASH_THICKNESS_SP * sp))
    pygame.draw.line(surf, color, (round(x0), round(y0)), (round(x1), round(y1)), w)


def beam_slash(surf, sp, x0, x1, y, up, color):
    """
    An acciaccatura's stroke drawn across a beam, once for the whole group.

    A run of grace notes is beamed together at graceNoteMag, and MuseScore puts a
    single stroke through that beam rather than one on each stem: the `beam` branch of
    TLayout::layoutStemSlash (tlayout.cpp:5423-5434) draws it at stemSlashAngle from the
    horizontal, 2 spaces long (a little longer when the angle between it and the beam is
    obtuse), starting from the first stem. `x0..x1` is the beam's reach, and the stroke
    crosses it at its middle.
    """
    length = 2.0 * sp
    ang = math.radians(STEM_SLASH_ANGLE)
    dx = length * math.cos(ang)
    dy = length * math.sin(ang)
    cx = (x0 + x1) / 2.0
    xa, xb = cx - dx / 2.0, cx + dx / 2.0
    ya, yb = y + (dy / 2.0 if up else -dy / 2.0), y - (dy / 2.0 if up else -dy / 2.0)
    w = max(1, round(STEM_SLASH_THICKNESS_SP * sp))
    pygame.draw.line(surf, color, (round(xa), round(ya)), (round(xb), round(yb)), w)


def _bezier_point(p0, p1, p2, p3, t):
    """A cubic Bézier at `t` in 0..1, the four points a tuple of floats (x, y)."""
    u = 1.0 - t
    a, b, c, d = u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t
    return (a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0],
            a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1])


def draw_tie(surf, sp, x0, x1, y, up, color):
    """
    A tie between two noteheads, MuseScore's way: not a font glyph but a **filled
    path**, a crescent whose edges swell to tieMidpointThickness (0.21 sp) at the
    middle and pinch to tieEndpointThickness (0.05 sp) at the ends, the whole arc
    bowing away from the noteheads by the shoulder (TDraw::draw(const TieSegment*),
    tdraw.cpp:3147-3202; SlurTieLayout::computeBezier, slurtielayout.cpp:2677-2763,
    and computeMidThickness, :3223-3256).

    `x0..x1` are the two noteheads' x and `y` is where the tie sits, one of them at each
    end (a tie is a plain line here - the two notes share a pitch and a staff, so their
    heads are the same height). `up` puts the arc above `y` - the default for a note whose
    stem points down, so the tie goes opposite the stem (SlurTieLayout::computeUp,
    slurtielayout.cpp:3067-3170: `item->setUp(!primaryChord->up())`). A tie shorter than
    minTieLength (1.0 sp) is drawn flat rather than pinched to nothing.

    This is drawn with polygons, so it needs no music font and has no fallback path of its
    own - the same shape is the shape, font or no font.
    """
    x0, x1 = sorted((float(x0), float(x1)))
    y = float(y)
    dir_ = -1.0 if up else 1.0
    end_t = TIE_END_SP * sp
    mid_t = TIE_MID_SP * sp

    # A tie too short to arc is a plain band of tieEndpointThickness - MuseScore draws
    # it flat rather than let the shoulders fold into each other.
    if (x1 - x0) < MIN_TIE_LENGTH_SP * sp:
        half = max(1, round(end_t * 0.5))
        pygame.draw.rect(surf, color,
                         pygame.Rect(round(x0), round(y) - half,
                                     max(1, round(x1 - x0)), half * 2))
        return

    # The arc's depth: at least tieMinShoulderHeight, rising 0.3 sp per space the tie
    # is longer than one, capped at tieMaxShoulderHeight (slurtielayout.cpp:2701-2702).
    length_sp = (x1 - x0) / max(1e-6, sp)
    shoulder = TIE_MIN_SHOULDER_SP + 0.3 * math.sqrt(abs(length_sp - 1.0))
    shoulder = max(TIE_MIN_SHOULDER_SP, min(TIE_MAX_SHOULDER_SP, shoulder)) * sp

    # One cubic for the tie's centre line, its shoulders (the widest part) shoulderW
    # (0.6) of the width apart (slurtielayout.cpp:2714,2717-2718), the whole thing
    # bowed `shoulder` away from the noteheads on the side `up` asks for.
    cx0 = x0 + (x1 - x0) * (1.0 - TIE_SHOULDER_W) * 0.5
    cx1 = cx0 + (x1 - x0) * TIE_SHOULDER_W
    p0 = (x0, y)
    p1 = (cx0, y + dir_ * shoulder)
    p2 = (cx1, y + dir_ * shoulder)
    p3 = (x1, y)

    # The crescent: the thickness swells from tieEndpointThickness at the two ends to
    # tieMidpointThickness at the middle (the half-thickness rides a sine, so it is the
    # ends and the mid that are pinned and the swell is the roundest line between them),
    # spread either side of the centre line.
    upper, lower = [], []
    steps = max(12, min(64, int(abs(x1 - x0))))
    for i in range(steps + 1):
        u = i / steps
        px, py = _bezier_point(p0, p1, p2, p3, u)
        half = (end_t + (mid_t - end_t) * math.sin(math.pi * u)) * 0.5
        upper.append((px, py - half))
        lower.append((px, py + half))

    pygame.draw.polygon(surf, color, upper + lower[::-1])


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


def flag_extent(sp, count, up):
    """
    The ink a stem's flags cover, as (left, top, right, bottom) in pixels from the point
    `_flag` hangs them on - the stem's anchor and the stem's tip. None when the note has
    no flags, or the font or the glyph is missing.

    A flag is part of a note's tail and reaches out beside the notehead, so `staff.py`
    counts it when it decides which way a stem points. The box follows the glyph rather
    than a guessed size, so it is right for whichever music font is installed.
    """
    kind = FLAGS.get(count)
    if not kind:
        return None
    g = glyph(f"flag{kind}{'Up' if up else 'Down'}", sp, (255, 255, 255))
    if g is None:
        return None
    w, h = g.size
    _, ay = FLAG_UP_NW if up else FLAG_DOWN_SW
    dx = max(1, round(STEM_SP * sp)) / 2.0 * (1 if up else -1)
    return (g.ox + dx, g.oy + ay * sp, g.ox + w + dx, g.oy + h + ay * sp)


def _dots(surf, sp, x_head_right, y, count, color):
    """The augmentation dots, the first one dotNoteDistance right of the notehead."""
    g = glyph("augmentationDot", sp, color)
    if g is None:
        return
    x = x_head_right + DOT_DIST_SP * sp
    for i in range(count):
        g.draw(surf, x + i * DOT_GAP_SP * sp, y)


def draw_staff_note(surf, x, y, sp, shape, color, stem_up, sounding=False,
                    stem=None, beamed=False, sharp=False, on_line=False,
                    grace=False, accidental=None):
    """
    A note on the staff, MuseScore's way: the accidental, the notehead, the dots and
    the stem with its flags (TDraw::draw(const ShadowNote*), tdraw.cpp:2598-2695).

    `x, y` is where the notehead sits: its centre, on its line or space.
    `stem` is (from_y, to_y), the stem's ends - the caller works them out, so the
    notes of a chord share one stem and a beamed note's stem reaches its beam; None
    leaves the stem, and its flags, out (a whole note, or a chord's other notes).
    `beamed`: this note's flags are a beam's, which the caller draws.
    `sharp`: draw a sharp left of the head (kept for callers that still pass it);
    `accidental`, when given, is the semitones to draw instead - +1 a sharp, -1 a
    flat, 0 a natural, +2 / -2 the doubles - what a MusicXML score's own spelling
    asks for. `on_line`: the note is on a line, so its dot rises into the space
    above (tdraw.cpp:2648). A sounding note is drawn lighter, as the staff has
    always drawn it.
    `grace`: a grace note drawn at graceNoteMag by the caller; a flagless one is an
    acciaccatura, and gets MuseScore's stroke across its stem instead of a flag.

    Without the font this draws the plain ellipses it drew before Leland.
    """
    if accidental is None:
        accidental = 1 if sharp else None
    if sounding:
        color = mix(color, (255, 255, 255), 0.5)
    head, flags, dots = shape
    g = glyph(HEADS[head], sp, color)
    if g is None:
        _fallback_staff_note(surf, x, y, sp, shape, color, stem_up, stem,
                             accidental is not None, on_line, accidental)
        return

    box = HEAD_BOX[head]
    ox = x - (box[2] - box[0]) * sp / 2.0 - box[0] * sp      # the head is centred on (x, y)
    oy = y - (box[1] + box[3]) * sp / 2.0

    if accidental is not None:                              # the accidental, left of the head
        acc = glyph(ACCIDENTAL_GLYPHS.get(accidental, "accidentalSharp"), sp, color)
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
        elif grace and not beamed:
            _stem_slash(surf, sp, ox + ax * sp, stem[1], stem_up, color)


def _fallback_staff_note(surf, x, y, sp, shape, color, stem_up, stem, sharp, on_line,
                         accidental=None):
    """A staff note as it was drawn before the music font: an ellipse, a line, a dot."""
    head, flags, dots = shape
    hw = round(sp * (0.65 if head == "black" else 0.75))
    hh = round(sp * 0.53)
    box = pygame.Rect(round(x - hw), round(y - hh), hw * 2, hh * 2)
    if accidental is None:
        accidental = 1 if sharp else None
    if accidental:                                  # a plain sign, left of the head
        ax, w, h = x - hw - round(sp * 0.8), sp * 0.3, sp * 0.45
        if accidental > 0:                          # a sharp (the doubles: two of them)
            for k in range(abs(accidental)):
                ax -= k * w * 1.4
                pygame.draw.line(surf, color, (round(ax - w), round(y - h)), (round(ax - w), round(y + h)), 1)
                pygame.draw.line(surf, color, (round(ax + w), round(y - h)), (round(ax + w), round(y + h)), 1)
                pygame.draw.line(surf, color, (round(ax - w * 2), round(y - h * 0.4)), (round(ax + w * 2), round(y - h)), 2)
                pygame.draw.line(surf, color, (round(ax - w * 2), round(y + h * 0.2)), (round(ax + w * 2), round(y - h * 0.2)), 2)
        else:                                       # a flat (or, at 0, a natural)
            for k in range(abs(accidental) or 1):
                ax -= k * w * 1.4
                if accidental == 0:                 # natural: two uprights and two arms
                    pygame.draw.line(surf, color, (round(ax - w), round(y - h)), (round(ax - w), round(y + h)), 1)
                    pygame.draw.line(surf, color, (round(ax + w), round(y - h * 0.7)), (round(ax + w), round(y + h * 0.7)), 1)
                    pygame.draw.line(surf, color, (round(ax - w), round(y + h * 0.3)), (round(ax + w), round(y - h * 0.1)), 2)
                    pygame.draw.line(surf, color, (round(ax - w), round(y + h * 0.6)), (round(ax + w), round(y + h * 0.2)), 2)
                else:                           # flat: an upright with a rounded belly
                    pygame.draw.line(surf, color, (round(ax - w), round(y - h)), (round(ax - w), round(y + h)), 2)
                    pygame.draw.arc(surf, color,
                                    pygame.Rect(round(ax - w), round(y), round(2 * w), round(h * 1.4)),
                                    math.pi, 2 * math.pi, 2)
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


def _tuplet_number_glyphs(num_str, sp, color):
    """The glyphs of a tuplet's number, one per digit: the tuplets' own digits (the
    weight a bracket's number is engraved in), the time signature's as the second
    choice. None when neither is there, so the caller draws the number in the UI
    font instead - the count is what has to be there, with or without the music font."""
    glyphs = []
    for d in num_str:
        g = glyph(f"tuplet{d}", sp, color)
        if g is None:
            g = glyph(f"timeSig{d}", sp, color)
        glyphs.append(g)
    return glyphs if all(g is not None for g in glyphs) else None


def draw_tuplet_bracket(surf, s, x0, x1, y, num_str, color, stem_up=True):
    """
    A tuplet's bracket: the line over (or under) a triplet's notes with the number
    in the gap at its middle - `┌── 3 ──┐`, with a short leg at each end reaching
    towards the notes.

    `x0`..`x1` is the group's reach (its first and last notehead) and `y` where the
    bracket's line stands - clear of the ink on the side the group's stems point to.
    `stem_up` says which side that is: with the stems up the bracket stands above
    the notes and its legs drop towards them, with them down it stands below and
    the legs rise. The number is centred between the two ends, drawn with the
    tuplets' own digits (SMuFL U+E880-U+E889), falling back to the time
    signature's digits and then the UI font.
    """
    x0, x1 = sorted((float(x0), float(x1)))
    y = float(y)
    text = str(num_str)
    line_w = max(1, round(STEM_SP * s * 1.3))

    # The number's own width, so the line knows where its gap is.
    glyphs = _tuplet_number_glyphs(text, s, color)
    if glyphs is not None:
        widths = [g.size[0] for g in glyphs]
        num_w = sum(widths) + DIGIT_GAP_SP * s * (len(widths) - 1)
    else:
        widths = None
        font = ui_font(max(9, int(s * 1.35)), bold=True)
        num_w = font.size(text)[0]

    # The line, in the two stretches either side of the number's gap.
    cx = (x0 + x1) / 2.0
    gap = num_w / 2.0 + TUPLET_GAP_SP * s
    if cx - gap > x0:
        pygame.draw.line(surf, color, (round(x0), round(y)), (round(cx - gap), round(y)), line_w)
    if cx + gap < x1:
        pygame.draw.line(surf, color, (round(cx + gap), round(y)), (round(x1), round(y)), line_w)

    # The legs, one at each end, reaching towards the notes: down when the stems
    # point up (the bracket stands above them), up when they point down.
    dy = round(TUPLET_LEG_SP * s if stem_up else -TUPLET_LEG_SP * s)
    for x in (x0, x1):
        pygame.draw.line(surf, color, (round(x), round(y)), (round(x), round(y + dy)), line_w)

    # The number itself, centred in the gap.
    if glyphs is not None:
        gap_px = DIGIT_GAP_SP * s
        left = cx - num_w / 2.0
        for g, w in zip(glyphs, widths):
            g.draw_centred(surf, left + w / 2.0, y)
            left += w + gap_px
    else:
        img = font.render(text, True, color)
        surf.blit(img, img.get_rect(center=(round(cx), round(y))))