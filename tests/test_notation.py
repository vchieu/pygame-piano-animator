"""Unit tests for notation.py: MuseScore's metrics, the SMuFL glyphs and the note painters."""
import math

import pygame
import pytest

import notation
import staff
from staff import (DOTTED_EIGHTH, DOTTED_HALF, DOTTED_QUARTER, EIGHTH, HALF,
                   QUARTER, SIXTEENTH, WHOLE, note_value)


# --------------------------------------------------------------------------- #
# MuseScore's metrics
# --------------------------------------------------------------------------- #
def test_engraving_defaults_match_musescore():
    # src/engraving/style/styledef.cpp:263-313 and 287-289, 524-526
    assert notation.ENGRAVING == {
        "stemWidth": 0.10,
        "stemLength": 3.5,
        "beamWidth": 0.5,
        "staffLineWidth": 0.11,
        "ledgerLineWidth": 0.16,
        "ledgerLineLength": 0.33,
        "dotNoteDistance": 0.5,
        "dotDotDistance": 0.65,
        "accidentalNoteDistance": 0.25,
        "graceNoteMag": 0.7,
        "graceToMainNoteDist": 0.45,
        "graceToGraceNoteDist": 0.3,
        "stemSlashPosition": 2.0,
        "stemSlashAngle": 40.0,
        "stemSlashThickness": 0.125,
        # the tie's own style (styledef.cpp:616-619, 635-636): how thick its ends and its
        # middle are, the length under which it is drawn flat, and how deep it arcs
        "tieEndpointThickness": 0.05,
        "tieMidpointThickness": 0.21,
        "minTieLength": 1.0,
        "tieMinShoulderHeight": 0.3,
        "tieMaxShoulderHeight": 2.0,
    }
    # ...and what the staff engraves with
    assert notation.STAFF_LINE_SP == 0.11
    assert notation.STEM_SP == 0.10
    assert notation.STEM_LEN_SP == 3.5
    assert notation.BEAM_SP == 0.5
    assert notation.LEDGER_SP == 0.16
    assert notation.LEDGER_EXT_SP == 0.33
    assert notation.GRACE_MAG == 0.7
    assert notation.GRACE_TO_MAIN_SP == 0.45
    assert notation.GRACE_TO_GRACE_SP == 0.3
    # the tie's thicknesses and its flat-short-tie rule
    assert notation.TIE_END_SP == 0.05
    assert notation.TIE_MID_SP == 0.21
    assert notation.MIN_TIE_LENGTH_SP == 1.0
    assert notation.TIE_MIN_SHOULDER_SP == 0.3
    assert notation.TIE_MAX_SHOULDER_SP == 2.0
    # the Bézier's shoulders stand 0.6 of the tie's width apart (slurtielayout.cpp:2714)
    assert notation.TIE_SHOULDER_W == 0.6
    # a slash with no hook to reach for is pulled to 0.66 of stemSlashPosition
    # (TLayout::layoutStemSlash, tlayout.cpp:5393,5408)
    assert notation.STEM_SLASH_HEIGHT_REDUCTION == 0.66
    assert staff.STEM_WIDTH_SPACES == notation.STEM_SP
    # SMuFL: one em is four staff spaces
    assert notation.SP_PER_EM == 4


def test_smufl_anchors_are_lelands():
    # fonts/leland/leland_metadata.json, glyphsWithAnchors - y negated, as MuseScore
    # draws it (engravingfont.cpp:696-752)
    assert notation.stem_up_se_sp() == (1.3, -0.16)
    assert notation.stem_down_nw_sp() == (0.0, 0.168)
    # and the notehead ink boxes measure what Leland says they do
    assert notation.HEAD_BOX["black"][2] == pytest.approx(1.300, abs=0.01)
    assert notation.NOTEHEAD_W_SP == pytest.approx(1.30, abs=0.01)
    assert notation.NOTEHEAD_H_SP == pytest.approx(1.06, abs=0.01)


def test_glyph_codepoints_are_smufl():
    assert notation.GLYPHS["noteheadBlack"] == 0xE0A4
    assert notation.GLYPHS["noteheadHalf"] == 0xE0A3
    assert notation.GLYPHS["noteheadWhole"] == 0xE0A2
    assert notation.GLYPHS["augmentationDot"] == 0xE1E7
    assert notation.GLYPHS["accidentalSharp"] == 0xE262
    assert notation.GLYPHS["flag8thUp"] == 0xE240
    assert notation.GLYPHS["flag16thDown"] == 0xE243
    # the clefs (SMuFL "Clefs"), and the time signature's digits (SMuFL "Time signatures",
    # U+E080-U+E089 - note timeSig4 is U+E084; U+E082 is a 2)
    assert notation.GLYPHS["gClef"] == 0xE050
    assert notation.GLYPHS["fClef"] == 0xE062
    assert notation.GLYPHS["timeSig4"] == 0xE084
    assert [notation.GLYPHS[f"timeSig{d}"] for d in range(10)] == list(range(0xE080, 0xE08A))
    # ...and the tuplets' digits (SMuFL "Tuplets", U+E880-U+E889) - a triplet's "3",
    # in the heavier weight a bracket's number is engraved in
    assert [notation.GLYPHS[f"tuplet{d}"] for d in range(10)] == list(range(0xE880, 0xE88A))


# --------------------------------------------------------------------------- #
# What a duration looks like
# --------------------------------------------------------------------------- #
def test_note_shape_thresholds():
    beat = 0.5
    assert notation.note_shape(2.0, beat) == ("whole", 0, 0)
    assert notation.note_shape(1.5, beat) == ("half", 0, 1)
    assert notation.note_shape(1.0, beat) == ("half", 0, 0)
    assert notation.note_shape(0.75, beat) == ("black", 0, 1)
    assert notation.note_shape(0.5, beat) == ("black", 0, 0)
    assert notation.note_shape(0.3, beat) == ("black", 1, 1)
    assert notation.note_shape(0.25, beat) == ("black", 1, 0)
    assert notation.note_shape(0.125, beat) == ("black", 2, 0)


def test_note_shape_keeps_note_values_classification():
    # the same eight inputs tests/test_staff.py uses, and the same answers
    beat = 0.5
    for duration, value in ((2.0, WHOLE), (1.5, DOTTED_HALF), (1.0, HALF), (0.75, DOTTED_QUARTER),
                            (0.5, QUARTER), (0.3, DOTTED_EIGHTH), (0.25, EIGHTH), (0.125, SIXTEENTH)):
        assert staff.note_value(duration, beat) == value
        head, flags, dots = notation.note_shape(duration, beat)
        # ...and the flags and dots go with them
        assert flags == (2 if value == SIXTEENTH else 1 if value in (EIGHTH, DOTTED_EIGHTH) else 0)
        assert dots == (1 if value in (DOTTED_HALF, DOTTED_QUARTER, DOTTED_EIGHTH) else 0)
        assert head == ("whole" if value == WHOLE else
                        "half" if value in (HALF, DOTTED_HALF) else "black")


def test_note_shape_survives_a_silly_beat():
    # no division by zero, and everything still lands on one of the three heads
    for beat in (0.0, -1.0, 1e-9):
        head, flags, dots = notation.note_shape(0.5, beat)
        assert head in notation.HEADS and 0 <= flags <= 3 and dots <= 2


# --------------------------------------------------------------------------- #
# The font and the glyph cache
# --------------------------------------------------------------------------- #
@pytest.fixture
def screen():
    import pygame
    pygame.init()
    surf = pygame.display.set_mode((900, 400))
    yield surf
    pygame.quit()


def test_the_font_is_there_and_opened_lazily(screen):
    # nothing is loaded until a glyph is asked for
    assert notation._font is None
    assert notation.available() is True
    assert notation._font is not None


def test_glyph_cache_hits_and_misses(screen):
    notation.clear_cache()
    first = notation.glyph("noteheadBlack", 12, (116, 196, 64))
    assert first is not None
    assert first is notation.glyph("noteheadBlack", 12, (116, 196, 64))       # cached
    assert notation.glyph("noteheadBlack", 12, (84, 152, 232)) is not first    # another colour
    assert notation.glyph("noteheadHalf", 12, (116, 196, 64)) is not first     # another glyph
    assert notation.glyph("noSuchGlyph", 12, (0, 0, 0)) is None               # nothing there
    # sp is quantised to a quarter space, so a near miss is the same glyph
    assert notation.glyph("noteheadBlack", 12.05, (116, 196, 64)) is first


def test_every_glyph_renders_at_several_sizes(screen):
    for name in notation.GLYPHS:
        for sp in (4.0, 7.0, 12.0, 40.0):
            g = notation.glyph(name, sp, (20, 20, 30))
            assert g is not None, f"{name} at {sp} sp"
            w, h = g.size
            assert w >= 1 and h >= 1
            # about a staff space per sp, give or take the glyph's own proportions (the
            # biggest thing here is a treble clef: 2.56 sp wide, 7.14 sp tall, so it fills
            # a whole staff and reaches 4.5 sp above its own line)
            assert 0.3 * sp <= w <= 3.5 * sp, f"{name} at {sp} sp is {w} px wide"
            assert 0.3 * sp <= h <= 7.5 * sp, f"{name} at {sp} sp is {h} px tall"
            # the ink is within a few staff spaces of its origin (a flag's hangs 3.3 sp
            # down from it, an accidental's reaches 1.3 sp up, a treble clef's 4.5 sp)
            assert -8 * sp <= g.oy and g.oy + h <= 8 * sp, f"{name} at {sp} sp: oy {g.oy}"
            assert -2 <= g.ox <= w + 2


def test_the_clefs_and_time_signature_digits_have_lelands_shapes(screen):
    # Measured from assets/fonts/Leland.otf with pygame.freetype at 256 px (64 px/sp):
    # each glyph's ink size in sp, and its origin's own box (what notation.CLEF_BOX says).
    expected_sp = {
        "gClef": (2.562, 7.141), "fClef": (2.656, 3.469),
        "timeSig4": (1.734, 1.984), "timeSig2": (1.469, 1.953),
    }
    for name, (w_sp, h_sp) in expected_sp.items():
        for sp in (6.0, 12.0):
            w, h = notation.glyph(name, sp, (20, 20, 30)).size
            assert w == pytest.approx(w_sp * sp, abs=2), f"{name} at {sp} sp: {w} px wide"
            assert h == pytest.approx(h_sp * sp, abs=2), f"{name} at {sp} sp: {h} px tall"

    # a clef's origin sits on the line it names (SMuFL GlyphOrigin), so the ink reaches
    # this far above and below that line
    for name, (w_sp, top, bottom) in notation.CLEF_BOX.items():
        g = notation.glyph(name, 12.0, (20, 20, 30))
        sp = 12.0
        assert g.size[0] == pytest.approx(w_sp * sp, abs=2)
        assert -g.oy == pytest.approx(top * sp, abs=2)                      # above the origin
        assert g.oy + g.size[1] == pytest.approx(-bottom * sp, abs=2)      # below it


def test_a_glyph_is_drawn_in_its_colour(screen):
    surf = pygame.Surface((200, 200), pygame.SRCALPHA)
    g = notation.glyph("noteheadBlack", 40, (116, 196, 64))
    g.draw(surf, 100, 100)
    ink = [(surf.get_at((x, y)).a, surf.get_at((x, y))) for x in range(200) for y in range(200)]
    alpha, colour = max(ink)
    assert alpha >= 250
    # smoothscale's filter moves the white of the mask a shade, so the tint is near exact
    assert all(abs(a - b) <= 2 for a, b in zip(colour[:3], (116, 196, 64)))
    # the ink is above and to the right of the origin (Leland's noteheadBlack box)
    assert g.ox == 0 and g.oy < 0


def test_the_cache_stays_bounded(screen):
    notation.clear_cache()
    for i in range(notation.CACHE_MAX + 40):
        notation.glyph("noteheadBlack", 1.0 + i / 8.0, (10, 20, 30))
    assert len(notation._glyphs) <= notation.CACHE_MAX + 1


# --------------------------------------------------------------------------- #
# The note painters
# --------------------------------------------------------------------------- #
PAPER = (252, 252, 254)


def _ink(surf, bg=(0, 0, 0)):
    """Every pixel that isn't the background, as a set of (x, y)."""
    w, h = surf.get_size()
    return {(x, y) for x in range(w) for y in range(h) if surf.get_at((x, y))[:3] != bg}


def _paper(size):
    surf = pygame.Surface(size)
    surf.fill(PAPER)
    return surf


def test_a_staff_note_is_drawn_the_musescore_way(screen):
    sp, x, y = 12.0, 200, 200
    surf = _paper((400, 400))
    notation.draw_staff_note(surf, x, y, sp, ("black", 0, 0), (20, 20, 30), True,
                             stem=(y, y - notation.STEM_LEN_SP * sp))
    ink = _ink(surf, PAPER)
    # the stem reaches up a stemLength from the notehead's line ...
    assert min(yy for _, yy in ink) == pytest.approx(y - notation.STEM_LEN_SP * sp, abs=2)
    # ...its outer edge on the notehead's stemUpSE anchor - the head's right edge, since the
# head is drawn centred on x ...
    assert max(xx for xx, _ in ink) == pytest.approx(x + notation.NOTEHEAD_W_SP * sp / 2, abs=2)
    # ...and the head is as wide as Leland's black head (1.30 sp)
    assert max(xx for xx, _ in ink) - min(xx for xx, _ in ink) == pytest.approx(
        notation.NOTEHEAD_W_SP * sp, abs=2)
    assert x - min(xx for xx, _ in ink) == pytest.approx(notation.NOTEHEAD_W_SP * sp / 2, abs=2)


def test_a_staff_notes_stem_points_the_way_it_is_told(screen):
    sp, x, y = 12.0, 200, 200
    surf = _paper((400, 400))
    notation.draw_staff_note(surf, x, y, sp, ("black", 0, 0), (20, 20, 30), False,
                             stem=(y, y + notation.STEM_LEN_SP * sp))
    ink = _ink(surf, PAPER)
    assert max(yy for _, yy in ink) == pytest.approx(y + notation.STEM_LEN_SP * sp, abs=2)
    # a stem down hangs off the notehead's left edge (stemDownNW x = 0)
    assert min(xx for xx, _ in ink) == pytest.approx(x - notation.NOTEHEAD_W_SP * sp / 2, abs=2)


def test_a_whole_note_has_no_stem_and_a_half_note_is_hollow(screen):
    sp, y = 12.0, 200
    whole = _paper((400, 400))
    notation.draw_staff_note(whole, 200, y, sp, ("whole", 0, 0), (20, 20, 30), True, stem=None)
    rows = [yy for _, yy in _ink(whole, PAPER)]
    assert max(rows) - min(rows) < sp * 1.5       # nothing above the head

    half = _paper((400, 400))
    notation.draw_staff_note(half, 200, y, sp, ("half", 0, 0), (20, 20, 30), True, stem=None)
    assert half.get_at((200, y))[:3] == PAPER     # a half note is hollow: paper shows through


def test_a_dot_rises_into_the_space_above_a_line(screen):
    sp, x, y = 20.0, 200, 200
    rows = []
    for on_line in (False, True):
        surf = _paper((400, 400))
        notation.draw_staff_note(surf, x, y, sp, ("black", 0, 1), (20, 20, 30), True,
                                 stem=(y, y - 40), on_line=on_line)
        ink = _ink(surf, PAPER)
        # the dot is right of the stem; where it sits tells a line from a space
        rows.append(min(yy for xx, yy in ink if xx > x + notation.NOTEHEAD_W_SP * sp / 2 + 1))
    assert rows[1] == pytest.approx(rows[0] - sp / 2, abs=2)


def test_flags_are_skipped_on_a_beamed_note(screen):
    sp, x, y = 12.0, 200, 200

    def reaches_right(beamed):
        surf = _paper((400, 400))
        notation.draw_staff_note(surf, x, y, sp, ("black", 1, 0), (20, 20, 30), True,
                                 stem=(y, y - 42), beamed=beamed)
        return max(xx for xx, _ in _ink(surf, PAPER)) > x + notation.NOTEHEAD_W_SP * sp + 2

    assert reaches_right(False)          # the flag reaches right of the notehead
    assert not reaches_right(True)       # a beam does that job instead


def test_a_slashed_grace_stroke_sits_a_reduced_position_off_the_tip(screen):
    # an acciaccatura's stroke starts stemSlashPosition off the stem's tip, scaled to the
    # grace (0.7) and pulled to 0.66 of it - no hook to reach for
    # (TLayout::layoutStemSlash, tlayout.cpp:5393,5408)
    sp, x = 12.0, 200
    g_sp = sp * notation.GRACE_MAG
    y_tip = 120
    surf = _paper((400, 400))
    notation._stem_slash(surf, g_sp, x, y_tip, True, (20, 20, 30))
    ink = _ink(surf, PAPER)
    expected = y_tip + notation.STEM_SLASH_POS_SP * g_sp * notation.STEM_SLASH_HEIGHT_REDUCTION
    left = min(xx for xx, _ in ink)
    at_start = [yy for xx, yy in ink if xx <= left + 1]
    assert min(at_start) == pytest.approx(expected, abs=2)      # y0, the stroke's start
    # the stroke leans at stemSlashAngle off the horizontal
    dx = max(xx for xx, _ in ink) - left
    dy = max(yy for _, yy in ink) - min(yy for _, yy in ink)
    assert dy / dx == pytest.approx(math.tan(math.radians(notation.STEM_SLASH_ANGLE)), abs=0.2)


def test_a_beam_slash_crosses_the_beam_two_spaces_long(screen):
    # through a beamed run, the stroke is one for the whole group: two spaces long,
    # crossing the beam's reach at its middle (tlayout.cpp:5423-5434)
    sp = 12.0
    y = 200
    x0, x1 = 100, 160
    surf = _paper((400, 400))
    notation.beam_slash(surf, sp, x0, x1, y, True, (20, 20, 30))
    ink = _ink(surf, PAPER)
    length = math.hypot(max(xx for xx, _ in ink) - min(xx for xx, _ in ink),
                        max(yy for _, yy in ink) - min(yy for _, yy in ink))
    assert length == pytest.approx(2.0 * sp, abs=3)
    assert (min(xx for xx, _ in ink) + max(xx for xx, _ in ink)) / 2 == pytest.approx((x0 + x1) / 2, abs=2)


def test_a_ledger_line_is_musescores_thickness(screen):
    surf = _paper((200, 100))
    notation.ledger_line(surf, 20, 120, 50, 10.0, (80, 80, 95))
    rows = [y for y in range(100) if surf.get_at((60, y))[:3] != PAPER]
    assert len(rows) == max(1, round(notation.LEDGER_SP * 10.0))


def test_a_tie_is_a_crescent_that_swells_to_the_middle(screen):
    # A tie is a filled path, not a glyph: tieEndpointThickness (0.05 sp) at the ends,
    # tieMidpointThickness (0.21 sp) in the middle (tdraw.cpp:3147-3202,
    # SlurTieLayout::computeBezier, slurtielayout.cpp:2677-2763).
    sp, x0, x1, y = 10.0, 100, 300, 200
    surf = _paper((400, 300))
    notation.draw_tie(surf, sp, x0, x1, y, True, (20, 20, 30))
    ink = _ink(surf, PAPER)

    # the ink lies within the two noteheads' x, and arches up (the caller asked `up`)
    assert min(xx for xx, _ in ink) >= x0 - 1
    assert max(xx for xx, _ in ink) <= x1 + 1
    assert max(yy for _, yy in ink) <= y + 1          # nothing below y
    assert min(yy for _, yy in ink) < y - 1           # it reached up

    def thickness(x):
        ys = [yy for xx, yy in ink if xx == x]
        return max(ys) - min(ys) + 1 if ys else 0

    mid = (x0 + x1) // 2
    end = x0 + 3
    # thicker at the middle than near the ends, near MuseScore's own ratio
    assert thickness(mid) > thickness(end)
    assert thickness(mid) / max(1, thickness(end)) > 1.5


def test_a_tie_shorter_than_min_tie_length_is_flat(screen):
    # minTieLength (1.0 sp, styledef.cpp:619): a shorter tie is drawn flat rather than
    # pinched to nothing.
    sp, y = 10.0, 200
    surf = _paper((400, 300))
    notation.draw_tie(surf, sp, 200, 206, y, True, (20, 20, 30))
    ink = _ink(surf, PAPER)
    assert ink
    # a flat band: the same rows in every column, no arc rising away
    rows_per_col = {frozenset(yy for xx, yy in ink if xx == c) for c in {xx for xx, _ in ink}}
    assert len(rows_per_col) == 1


def test_a_ties_arc_depth_follows_its_length(screen):
    # the shoulder is at least tieMinShoulderHeight and grows 0.3 sp per space past one,
    # capped at tieMaxShoulderHeight (slurtielayout.cpp:2701-2702)
    sp, y = 10.0, 250
    ys = []
    for x1 in (150, 250, 400):
        surf = _paper((500, 300))
        notation.draw_tie(surf, sp, 100, x1, y, True, (20, 20, 30))
        ink = _ink(surf, PAPER)
        ys.append(y - min(yy for _, yy in ink))
    assert ys[0] < ys[1] < ys[2] or ys[0] == ys[1] < ys[2]
    assert all(d >= notation.TIE_MIN_SHOULDER_SP * sp - 1 for d in ys)


# --------------------------------------------------------------------------- #
# The tuplets
# --------------------------------------------------------------------------- #
def test_a_tuplet_bracket_is_a_line_with_a_gap_and_legs(screen):
    # `┌── 3 ──┐`: the line runs from the group's first notehead to its last, with
    # a gap at the middle for the number, and a short leg at each end reaching
    # towards the notes - down when the stems point up, up when they point down.
    sp, x0, x1, y = 12.0, 100, 300, 200
    leg = round(notation.TUPLET_LEG_SP * sp)

    surf = _paper((400, 300))
    notation.draw_tuplet_bracket(surf, sp, x0, x1, y, "3", (20, 20, 30), stem_up=True)
    ink = _ink(surf, PAPER)
    assert ink
    # the line starts at both ends of the group's reach ...
    assert any(xx == x0 and yy == y for xx, yy in ink)
    assert any(xx == x1 and yy == y for xx, yy in ink)
    # ...but is broken at the middle, where the number stands in the gap
    row = {xx for xx, yy in ink if yy == y}
    holes = [xx for xx in range(x0 + 1, x1) if xx not in row]
    cx = (x0 + x1) // 2
    assert holes and holes[0] < cx < holes[-1]
    # the number itself is drawn at the middle, on the line
    assert any(abs(xx - cx) <= 8 and abs(yy - y) <= notation.NOTEHEAD_H_SP * sp
               for xx, yy in ink)
    # the legs drop towards the notes, TUPLET_LEG_SP below the line
    assert y + leg in {yy for xx, yy in ink if xx == x0}
    assert y + leg in {yy for xx, yy in ink if xx == x1}

    # stems pointing down: the bracket goes under them, legs rising
    surf = _paper((400, 300))
    notation.draw_tuplet_bracket(surf, sp, x0, x1, y, "3", (20, 20, 30), stem_up=False)
    ink = _ink(surf, PAPER)
    assert y - leg in {yy for xx, yy in ink if xx == x0}
    # ...and no leg drops below the line (its own thickness aside)
    thick = max(1, round(notation.STEM_SP * sp * 1.3)) // 2 + 1
    assert not any(yy > y + thick for xx, yy in ink if xx in (x0, x1))


def test_the_tuplet_number_falls_back_to_the_ui_font_without_the_music_font(no_font, screen):
    # no music font: the bracket is still drawn, and its number in the UI font -
    # the count is what has to be there.
    surf = _paper((400, 300))
    notation.draw_tuplet_bracket(surf, 12.0, 100, 300, 200, "3", (20, 20, 30))
    ink = _ink(surf, PAPER)
    assert any(xx == 100 and yy == 200 for xx, yy in ink)          # the line is drawn
    assert any(abs(xx - 200) <= 10 and abs(yy - 200) <= 12 for xx, yy in ink)   # ...and the 3


def test_the_staff_finds_and_draws_tuplet_groups(screen):
    # A triplet's group comes off the score's own marks: <time-modification>'s
    # number on every note, the start/stop marks on the notes that open and
    # close it - and the bracket is drawn over the group.
    from midi_loader import RIGHT, Note
    kinds = ["start", None, "stop"]
    notes = [Note(p, i * 0.2, i * 0.2 + 0.15, 80, 0, RIGHT, tuplet_num=3, tuplet_type=k)
             for i, (p, k) in enumerate(zip((68, 73, 75), kinds))]  # G#4, C#5, D#5
    info = staff.compute_notes_info(notes, 0.0, 1.0, 600.0, staff.MARGIN_LEFT, 10.0)

    groups = staff.find_tuplet_groups(notes, info)
    assert len(groups) == 1
    grp, num = groups[0]
    assert [n.name for n in grp] == ["G#4", "C#5", "D#5"]
    assert num == "3"
    xs = [info[id(n)]["x"] for n in grp]
    assert max(xs) - min(xs) > 0                     # spread over the group's reach

    surf = _paper((900, 400))
    staff.draw_tuplet_brackets(surf, info, notes, 120.0, 220.0, 10.0)
    ink = _ink(surf, PAPER)
    assert ink                                       # the bracket was drawn
    # over the whole group: from its first notehead's left to its last one's right
    assert min(xx for xx, _ in ink) <= min(xs)
    assert max(xx for xx, _ in ink) >= max(xs)


# --------------------------------------------------------------------------- #
# The clefs and the time signature
# --------------------------------------------------------------------------- #
def test_a_clefs_origin_lands_on_the_line_it_names(screen):
    # SMuFL puts a clef's origin on the staff line it names, so the placement is
    # arithmetic: the ink hangs off that line by exactly what CLEF_BOX says.
    sp, x, line_y = 12.0, 100.0, 200.0
    for name, (w_sp, top, bottom) in notation.CLEF_BOX.items():
        surf = _paper((400, 400))
        assert notation.draw_clef(surf, x, line_y, sp, (20, 20, 30), name) is True
        ink = _ink(surf, PAPER)
        assert min(xx for xx, _ in ink) == pytest.approx(x, abs=2)          # ink's left edge
        assert max(xx for xx, _ in ink) - min(xx for xx, _ in ink) == pytest.approx(w_sp * sp, abs=2)
        assert min(yy for _, yy in ink) == pytest.approx(line_y - top * sp, abs=2)
        assert max(yy for _, yy in ink) == pytest.approx(line_y - bottom * sp, abs=2)

    # a treble clef fills its staff: from G4 its ink reaches 4.47 sp up and 2.67 sp down,
    # over the top line (2 sp above G4) and under the bottom one (2 sp below)
    assert notation.CLEF_BOX["gClef"][1] > 4.0 and notation.CLEF_BOX["gClef"][2] < -2.0
    # a bass clef sits on F3 (mid - s) with its two dots in the spaces above and below it
    assert notation.CLEF_BOX["fClef"][1] == pytest.approx(1.0, abs=0.01)
    assert notation.CLEF_BOX["fClef"][2] == pytest.approx(-2.469, abs=0.01)


def test_the_staff_anchors_its_clefs_on_the_lines_they_name(screen):
    # staff.py draws the treble clef on G4 (mid_y + s) and the bass clef on F3 (mid_y - s)
    sp, x, mid = 12.0, 100.0, 200.0
    for draw, name, ref in ((staff.draw_treble_clef, "gClef", mid + sp),
                            (staff.draw_bass_clef, "fClef", mid - sp)):
        surf = _paper((400, 400))
        draw(surf, x, mid, sp, (20, 20, 30))
        ink = _ink(surf, PAPER)
        _w, top, bottom = notation.CLEF_BOX[name]
        assert min(xx for xx, _ in ink) == pytest.approx(x, abs=3)
        assert min(yy for _, yy in ink) == pytest.approx(ref - top * sp, abs=3)
        assert max(yy for _, yy in ink) == pytest.approx(ref - bottom * sp, abs=3)


def test_a_time_signature_is_a_numeral_over_its_twin(screen):
    # MuseScore writes the numerator on the staff line above the middle one and the
    # denominator on the line below (Clef::drawStaffTimeSig), both centred on the column.
    sp, x, mid = 12.0, 200.0, 200.0
    surf = _paper((400, 400))
    assert notation.draw_time_signature(surf, x, mid, sp, 4, 4, (20, 20, 30)) is True
    ink = _ink(surf, PAPER)
    ys = [yy for _, yy in ink]
    half = notation.glyph("timeSig4", sp, (20, 20, 30)).size[1] / 2.0

    above = [yy for yy in ys if yy < mid]
    below = [yy for yy in ys if yy > mid]
    assert above and below                       # a 4 over a 4, not one block
    assert min(above) == pytest.approx(mid - sp - half, abs=3)
    assert max(above) == pytest.approx(mid - sp + half, abs=3)
    assert min(below) == pytest.approx(mid + sp - half, abs=3)
    assert max(below) == pytest.approx(mid + sp + half, abs=3)
    # and the column is centred on x, half a numeral to either side
    assert min(xx for xx, _ in ink) == pytest.approx(x - notation.TIME_SIG_HALF_W_SP * sp, abs=3)
    assert max(xx for xx, _ in ink) == pytest.approx(x + notation.TIME_SIG_HALF_W_SP * sp, abs=3)


def test_a_two_digit_time_signature_sets_its_digits_side_by_side(screen):
    # SMuFL has no timeSig12 glyph - a 12 is a 1 and a 2 next to each other, which is what
    # draw_time_signature makes of "12".
    sp, x, mid = 12.0, 200.0, 200.0
    surf = _paper((400, 400))
    assert notation.draw_time_signature(surf, x, mid, sp, 12, 8, (20, 20, 30)) is True
    ink = _ink(surf, PAPER)
    width = max(xx for xx, _ in ink) - min(xx for xx, _ in ink)
    one = notation.glyph("timeSig1", sp, (20, 20, 30)).size[0] \
        + notation.glyph("timeSig2", sp, (20, 20, 30)).size[0] + notation.DIGIT_GAP_SP * sp
    assert width == pytest.approx(one, abs=4)
    assert (min(xx for xx, _ in ink) + max(xx for xx, _ in ink)) / 2.0 == pytest.approx(x, abs=3)


# --------------------------------------------------------------------------- #
# With no font at all (a stripped build) every painter still draws
# --------------------------------------------------------------------------- #
@pytest.fixture
def no_font():
    """The music font isn't there (or can't be opened)."""
    notation.clear_cache()
    notation._font = False
    notation._quit_hooked = True
    yield
    notation.clear_cache()


def test_with_no_font_the_module_still_answers(no_font):
    assert notation.available() is False
    assert notation.glyph("noteheadBlack", 12, (0, 0, 0)) is None
    assert notation.stem_up_se_sp() == (1.3, -0.16)      # the metrics don't need the font
    assert notation.note_shape(0.25, 0.5) == ("black", 1, 0)


def test_the_staff_note_falls_back_to_its_ellipse(no_font, screen):
    surf = _paper((300, 300))
    notation.draw_staff_note(surf, 150, 150, 12.0, ("black", 0, 0), (20, 20, 30), True,
                             stem=(150, 110), sharp=True, on_line=True)
    ink = _ink(surf, PAPER)
    assert min(y for _, y in ink) == pytest.approx(110, abs=2)    # the stem's tip
    assert min(x for x, _ in ink) < 140                          # and the accidental beside it


def test_the_clefs_and_time_signature_draw_nothing_without_a_font(no_font, screen):
    # they report False and leave the surface alone: staff.py then draws the hand-drawn
    # clefs and the UI font's "4" it drew before Leland was there
    surf = _paper((300, 300))
    assert notation.draw_clef(surf, 40, 100, 12.0, (20, 20, 30), "gClef") is False
    assert notation.draw_clef(surf, 40, 100, 12.0, (20, 20, 30), "fClef") is False
    assert notation.draw_time_signature(surf, 150, 150, 12.0, 4, 4, (20, 20, 30)) is False
    assert not _ink(surf, PAPER)