"""Unit tests for notation.py: MuseScore's metrics, the SMuFL glyphs and the note painters."""
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
    # src/engraving/style/styledef.cpp:263-313, and Leland's own engravingDefaults
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
    }
    # ...and what the staff engraves with
    assert notation.STAFF_LINE_SP == 0.11
    assert notation.STEM_SP == 0.10
    assert notation.STEM_LEN_SP == 3.5
    assert notation.BEAM_SP == 0.5
    assert notation.LEDGER_SP == 0.16
    assert notation.LEDGER_EXT_SP == 0.33
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
            # about a staff space per sp, give or take the glyph's own proportions
            assert 0.3 * sp <= w <= 3.5 * sp, f"{name} at {sp} sp is {w} px wide"
            assert 0.3 * sp <= h <= 5.0 * sp, f"{name} at {sp} sp is {h} px tall"
            # the ink is within a few staff spaces of its origin (a flag's hangs 3.3 sp
            # down from it, an accidental's reaches 1.3 sp up)
            assert -4 * sp <= g.oy and g.oy + h <= 4 * sp, f"{name} at {sp} sp: oy {g.oy}"
            assert -2 <= g.ox <= w + 2


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


def test_a_ledger_line_is_musescores_thickness(screen):
    surf = _paper((200, 100))
    notation.ledger_line(surf, 20, 120, 50, 10.0, (80, 80, 95))
    rows = [y for y in range(100) if surf.get_at((60, y))[:3] != PAPER]
    assert len(rows) == max(1, round(notation.LEDGER_SP * 10.0))


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