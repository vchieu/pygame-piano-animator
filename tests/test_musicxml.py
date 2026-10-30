"""
Reading and writing MusicXML scores (musicxml_loader.py), against small scores
the tests write themselves - no local data needed, except for the one tuplet
check that reads the real MIDIs/Khong-mon-dang-ho-doi.mxl.
"""
import os
import zipfile

import pytest

from conftest import midi_path
from midi_loader import LEFT, RIGHT, load_song

from musicxml_loader import (is_musicxml, load_musicxml, read_root, save_musicxml,
                             play_order, _kids, _local)

# --------------------------------------------------------------------------- #
# A tiny score builder
# --------------------------------------------------------------------------- #

HEADER = ('<?xml version="1.0" encoding="UTF-8"?>\n'
          '<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 3.1 Partwise//EN" '
          '"http://www.musicxml.org/dtds/partwise.dtd">\n'
          '<score-partwise version="3.1">\n'
          '<part-list><score-part id="P1"><part-name>Piano</part-name></score-part></part-list>\n'
          '<part id="P1">\n')


def note(step="C", octave=4, dur=4, type_="quarter", alter=0, staff=None, voice=None,
         chord=False, rest=False, grace=False, dot=False, finger=None, tie=None,
         tuplet=None):
    """One <note> element's XML. `tuplet` is (num, kind): the <time-modification>'s
    <actual-notes> (with <normal-notes> 2), and the <notations><tuplet type> mark -
    "start", "stop", or None for a note in the middle of the group."""
    bits = ["<note>"]
    if grace:
        bits.append("<grace/>")
    if chord:
        bits.append("<chord/>")
    if rest:
        bits.append("<rest/>")
    else:
        bits.append(f"<pitch><step>{step}</step>")
        if alter:
            bits.append(f"<alter>{alter}</alter>")
        bits.append(f"<octave>{octave}</octave></pitch>")
    if not grace:
        bits.append(f"<duration>{dur}</duration>")
    if tie:
        bits.append(f'<tie type="{tie}"/>')
    if voice is not None:
        bits.append(f"<voice>{voice}</voice>")
    bits.append(f"<type>{type_}</type>")
    if dot:
        bits.append("<dot/>")
    if tuplet is not None:
        num, kind = tuplet
        bits.append(f"<time-modification><actual-notes>{num}</actual-notes>"
                    "<normal-notes>2</normal-notes></time-modification>")
    if staff is not None:
        bits.append(f"<staff>{staff}</staff>")
    if tie or finger or (tuplet and tuplet[1]):
        bits.append("<notations>")
        if tie:
            bits.append(f'<tied type="{tie}"/>')
        if finger:
            bits.append(f"<technical><fingering>{finger}</fingering></technical>")
        if tuplet and tuplet[1]:
            bits.append(f'<tuplet type="{tuplet[1]}"/>')
        bits.append("</notations>")
    bits.append("</note>")
    return "".join(bits)


def measure(body, number=1, attributes=None, barlines="", direction=""):
    a = attributes if attributes is not None else "<attributes><divisions>4</divisions></attributes>"
    return (f'<measure number="{number}">{a}{direction}{body}{barlines}</measure>')


def score(measures_xml, parts=1):
    out = [HEADER]
    if parts > 1:
        # a two-part score: rebuild header with two score-parts and two <part>s
        pass
    out.append(measures_xml)
    out.append("</part>\n</score-partwise>\n")
    return "".join(out)


def write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def one_part(tmp_path, measures, name="score.xml"):
    return write(tmp_path, name, score(measures))


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #

def test_score_partwise_with_two_staves_splits_hands(tmp_path):
    body = (note("C", 5, 4, "quarter", staff=1)
            + note("C", 3, 4, "quarter", staff=2))
    p = one_part(tmp_path, measure(body))
    song = load_musicxml(p)
    assert len(song.notes) == 2
    hi = next(n for n in song.notes if n.pitch == 72)
    lo = next(n for n in song.notes if n.pitch == 48)
    assert hi.hand == RIGHT and lo.hand == LEFT


def test_divisions_and_tempo_make_seconds(tmp_path):
    direction = '<direction><sound tempo="60"/></direction>'
    body = note("C", 4, 4, "quarter") + note("D", 4, 4, "quarter") \
        + note("E", 4, 4, "quarter") + note("F", 4, 4, "quarter")
    p = one_part(tmp_path, measure(body, direction=direction))
    song = load_musicxml(p)
    starts = sorted(n.start for n in song.notes)
    assert starts == [0.0, 1.0, 2.0, 3.0]           # divisions 4, 60 bpm: a quarter is 1 s
    assert song.bar_times[0] == pytest.approx(0.0)
    assert song.bar_times[-1] == pytest.approx(4.0)  # one 4/4 bar is 4 s


def test_beat_grid_has_no_gap_at_the_bar_line(tmp_path):
    # Two 4/4 bars at 60 bpm, divisions 4: a beat is a second. The beat grid must
    # run without a gap across the bar line, or the staff reads a double-length
    # beat there and draws the bar as if it did not fill its four beats.
    direction = '<direction><sound tempo="60"/></direction>'
    m1 = measure(note("C", 4, 4, "quarter") * 4, number=1, direction=direction)
    m2 = measure(note("D", 4, 4, "quarter") * 4, number=2)
    p = one_part(tmp_path, m1 + m2)
    song = load_musicxml(p)
    beats = [round(b, 6) for b in song.beat_times]
    assert beats == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
    gaps = {round(song.beat_times[i + 1] - song.beat_times[i], 6)
            for i in range(len(song.beat_times) - 1)}
    assert gaps == {1.0}                             # every beat the same length


def test_repeated_note_touching_end_to_end_is_not_cut(tmp_path):
    # The same pitch twice, back to back: the second note starts exactly where the
    # first ends. The score's arithmetic leaves a rounding error there, and the
    # two must still come out as two full quarters, not one cut short.
    direction = '<direction><sound tempo="60"/></direction>'
    body = note("C", 4, 4, "quarter") + note("C", 4, 4, "quarter") \
        + note("E", 4, 4, "quarter") + note("C", 4, 4, "quarter")
    p = one_part(tmp_path, measure(body, direction=direction))
    song = load_musicxml(p)
    cs = sorted((n for n in song.notes if n.pitch == 60), key=lambda n: n.start)
    assert len(cs) == 3
    assert all(n.duration == pytest.approx(1.0) for n in cs)
    assert song.cleanup == []                        # nothing was ended early


def test_chord_and_backup_voices(tmp_path):
    # voice 1: a C-E chord (E is a <chord/> sharing C's start); a <backup> then
    # voice 2 plays G an octave below, at the same time.
    body = (note("C", 5, 8, "half", voice=1)
            + note("E", 5, 8, "half", voice=1, chord=True)
            + "<backup><duration>8</duration></backup>"
            + note("G", 3, 8, "half", voice=2))
    p = one_part(tmp_path, measure(body))
    song = load_musicxml(p)
    assert len(song.notes) == 3
    c = next(n for n in song.notes if n.pitch == 72)
    e = next(n for n in song.notes if n.pitch == 76)
    g = next(n for n in song.notes if n.pitch == 55)
    assert c.start == pytest.approx(e.start)         # the chord shares its start
    assert g.start == pytest.approx(c.start)         # the backup restarted the cursor


def test_ties_are_kept_separate(tmp_path):
    direction = '<direction><sound tempo="60"/></direction>'
    m1 = measure(note("C", 4, 4, "quarter", tie="start"), number=1, direction=direction)
    m2 = measure(note("C", 4, 4, "quarter", tie="stop"), number=2)
    p = one_part(tmp_path, m1 + m2)
    song = load_musicxml(p)
    c = [n for n in song.notes if n.pitch == 60]
    assert len(c) == 2
    assert len(song.xml_ties) == 1


def test_tuplets_are_read(tmp_path):
    # A triplet (liên ba): <time-modification><actual-notes> says three written
    # notes take the time of two - it is on every note of the group - and
    # <notations><tuplet type="start"> / type="stop" mark the notes that open
    # and close it. The note in the middle carries the number but no mark, and a
    # note outside any tuplet keeps the defaults (None, None).
    body = (note("G", 4, 4, "quarter", tuplet=(3, "start"))
            + note("C", 5, 4, "quarter", tuplet=(3, None))
            + note("D", 5, 4, "quarter", tuplet=(3, "stop"))
            + note("E", 5, 4, "quarter"))
    p = one_part(tmp_path, measure(body))
    song = load_musicxml(p)
    assert [n.tuplet_num for n in song.notes] == [3, 3, 3, None]
    assert [n.tuplet_type for n in song.notes] == ["start", None, "stop", None]


def test_a_tuplets_marks_survive_a_tie(tmp_path):
    direction = '<direction><sound tempo="60"/></direction>'
    m1 = measure(note("C", 4, 4, "quarter", tie="start"), number=1, direction=direction)
    m2 = measure(note("C", 4, 4, "quarter", tie="stop", tuplet=(3, "stop")), number=2)
    p = one_part(tmp_path, m1 + m2)
    song = load_musicxml(p)
    c = [n for n in song.notes if n.pitch == 60]
    assert len(c) == 2
    assert c[1].tuplet_type == "stop"


def test_tuplets_in_khong_mon_dang_ho_doi_measure_51():
    # The real score: bar 51 holds a triplet of G#4, C#5, D#5 - the first note
    # opens the group, the last closes it, the middle one only carries the
    # number. The bar is played twice (a repeat), so the group comes out twice.
    song = load_musicxml(midi_path("Khong-mon-dang-ho-doi.mxl"))
    tuplets = sorted((n for n in song.notes if getattr(n, "tuplet_num", None) is not None),
                     key=lambda n: n.start)
    assert len(tuplets) == 12                        # four triplet groups, bar 51 among them
    assert all(n.tuplet_num == 3 for n in tuplets)
    assert [n.tuplet_type for n in tuplets] == ["start", None, "stop"] * 4
    # ...and bar 51's own group is the first: G#4, C#5, D#5
    assert [n.name for n in tuplets[:3]] == ["G#4", "C#5", "D#5"]


def test_grace_note_is_short(tmp_path):
    body = note("D", 4, 4, type_="quarter", grace=True) + note("C", 4, 4, "quarter")
    p = one_part(tmp_path, measure(body))
    song = load_musicxml(p)
    grace = next(n for n in song.notes if n.pitch == 62)
    main = next(n for n in song.notes if n.pitch == 60)
    assert grace.duration < 0.2                      # a grace note is written, not measured
    assert main.start == pytest.approx(0.0)          # and it doesn't shift the quarter after it


def test_a_run_of_graces_comes_out_one_after_another(tmp_path):
    # Two graces written on one cursor (a grace carries no <duration>) that lead
    # into a half note: they must play in turn into that note, not strike together
    # as one grace chord. See 50-nam-ve-sau.mxl bar 20, where MuseScore prints them
    # as a little run above the beat.
    direction = '<direction><sound tempo="60"/></direction>'
    m1 = measure(note("C", 4, 4, "quarter") + note("C", 4, 4, "quarter")
                 + note("C", 4, 4, "quarter") + note("C", 4, 4, "quarter"),
                 number=1, direction=direction)
    m2 = measure(note("D", 4, 4, "quarter", grace=True)
                 + note("E", 4, 4, "quarter", grace=True)
                 + note("G", 4, 8, "half"), number=2)
    p = one_part(tmp_path, m1 + m2)
    song = load_musicxml(p)
    d, e = (next(n for n in song.notes if n.pitch == p_) for p_ in (62, 64))
    g = next(n for n in song.notes if n.pitch == 67)
    assert d.start < e.start < g.start              # a run, in written order
    assert e.end == pytest.approx(g.start)          # it leads straight into the half note
    assert d.end == pytest.approx(e.start)          # and the two graces touch
    assert g.duration == pytest.approx(2.0)         # the main note keeps its own length
    assert song.cleanup == []                       # nothing shifted or cut


def test_a_grace_at_the_start_does_not_shift_the_song(tmp_path):
    # No room to reach back into: the run is squeezed into the start instead of
    # pushing the whole song later.
    body = (note("D", 4, 4, "quarter", grace=True)
            + note("E", 4, 4, "quarter", grace=True)
            + note("G", 4, 8, "half"))
    p = one_part(tmp_path, measure(body))
    song = load_musicxml(p)
    g = next(n for n in song.notes if n.pitch == 67)
    assert min(n.start for n in song.notes) == pytest.approx(0.0)
    assert g.start == pytest.approx(0.0)
    assert song.cleanup == []


def test_mxl_zip_is_read(tmp_path):
    body = note("C", 4, 4, "quarter") + note("D", 4, 4, "quarter")
    xml = score(measure(body)).encode("utf-8")
    container = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                 '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                 '<rootfiles><rootfile full-path="score.xml" '
                 'media-type="application/vnd.recordare.musicxml+xml"/></rootfiles></container>')
    path = str(tmp_path / "score.mxl")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("META-INF/container.xml", container)
        z.writestr("score.xml", xml)
    song = load_musicxml(path)
    assert [n.pitch for n in song.notes] == [60, 62]
    assert song.duration == pytest.approx(1.0)


def test_alter_keeps_the_written_spelling(tmp_path):
    # a written Bb (step B, alter -1) sounds as 70 but stays spelled Bb
    body = note("B", 4, 4, "quarter", alter=-1) + note("B", 3, 4, "quarter", alter=-1)
    p = one_part(tmp_path, measure(body))
    song = load_musicxml(p)
    hi = next(n for n in song.notes if n.pitch == 70)
    lo = next(n for n in song.notes if n.pitch == 58)
    assert hi.alter == -1 and lo.alter == -1
    assert hi.step == 4 * 7 + "CDEFGAB".index("B")   # octave 4, step B


def test_the_key_signature_is_read(tmp_path):
    # <key><fifths>4</fifths> is E major: the staff draws the four sharps at the head of
    # every system and leaves out the accidentals the key already covers.
    attributes = ("<attributes><divisions>4</divisions>"
                  "<key><fifths>4</fifths><mode>major</mode></key></attributes>")
    body = (note("F", 4, 4, "quarter", alter=1) + note("C", 4, 4, "quarter", alter=1)
            + note("D", 4, 4, "quarter", alter=1) + note("A", 4, 4, "quarter"))
    p = one_part(tmp_path, measure(body, attributes=attributes))
    song = load_musicxml(p)
    assert song.key_fifths == 4
    assert song.key_changes == []          # the first key is not a change
    assert song.key_at(0.0) == 4

    # a flat key: <fifths>-2</fifths> is Bb major
    flat = ("<attributes><divisions>4</divisions>"
            "<key><fifths>-2</fifths><mode>major</mode></key></attributes>")
    p = one_part(tmp_path, measure(note("B", 4, 4, "quarter", alter=-1), attributes=flat),
                 name="flat.xml")
    song = load_musicxml(p)
    assert song.key_fifths == -2

    # a score without a key signature has none
    p = one_part(tmp_path, measure(note("C", 4, 4, "quarter")), name="plain.xml")
    assert load_musicxml(p).key_fifths == 0


def test_a_key_change_is_read_as_a_change(tmp_path):
    # A score that changes its key: the first key is the score's, the second one a change
    # at the time it happens.
    first = ("<attributes><divisions>4</divisions>"
             "<key><fifths>4</fifths><mode>major</mode></key></attributes>")
    second = ("<attributes><key><fifths>-2</fifths><mode>major</mode></key></attributes>")
    m1 = measure(note("C", 4, 4, "quarter") * 4, number=1, attributes=first)
    m2 = measure(note("D", 4, 4, "quarter") * 4, number=2, attributes=second)
    p = one_part(tmp_path, m1 + m2)
    song = load_musicxml(p)
    assert song.key_fifths == 4
    assert len(song.key_changes) == 1
    t, fifths = song.key_changes[0]
    assert fifths == -2
    assert t == pytest.approx(2.0)        # the second bar starts there (120 bpm)
    assert song.key_at(0.0) == 4
    assert song.key_at(t + 0.1) == -2


def test_fingering_from_file_is_kept(tmp_path):
    body = (note("C", 4, 4, "quarter", finger=3)
            + note("D", 4, 4, "quarter"))
    p = one_part(tmp_path, measure(body))
    song = load_musicxml(p, fingering="file")
    c = next(n for n in song.notes if n.pitch == 60)
    assert c.finger == 3
    assert song.cleanup and any("kept 1 printed fingering" in line for line in song.cleanup)


def test_fingering_modes_on_one_score(tmp_path):
    # a playable written 3, then an unplayable written octave 4-5
    body = (note("C", 4, 4, "quarter", finger=3)
            + note("C", 4, 4, "quarter", finger=4)
            + note("C", 5, 4, "quarter", finger=5, chord=True))
    p = one_part(tmp_path, measure(body))

    auto = load_musicxml(p, fingering="auto")
    assert all(n.finger is None for n in auto.notes)

    # "file": the marks stay exactly as the score wrote them, playable or not
    file = load_musicxml(p, fingering="file")
    thirds = [n for n in file.notes if n.pitch == 60]
    assert sorted(n.finger for n in thirds) == [3, 4]
    assert next(n for n in file.notes if n.pitch == 72).finger == 5

    # "fill": the marks are kept too (their correction, if any, is the planner's)
    fill = load_musicxml(p, fingering="fill")
    ft = [n for n in fill.notes if n.pitch == 60]
    assert sorted(n.finger for n in ft) == [3, 4]


def test_hands_modes(tmp_path):
    # a high note written on staff 2: following the file it is the left hand,
    # working the hands out it is the right (its register says so).
    body = note("C", 6, 4, "quarter", staff=2) + note("C", 5, 4, "quarter", staff=1)
    p = one_part(tmp_path, measure(body))
    by_file = load_musicxml(p, hands_from_file=True)
    assert next(n for n in by_file.notes if n.pitch == 84).hand == LEFT
    by_auto = load_musicxml(p, hands_from_file=False)
    assert next(n for n in by_auto.notes if n.pitch == 84).hand == RIGHT


def test_volta_repeats_are_played(tmp_path):
    # bar 1 opens a repeat, bar 2 closes it (twice); bar 2 carries the 1st
    # ending and bar 3 the 2nd, so the bars come out 1, 2, 1, 3
    m1 = measure(note("C", 4, 4, "quarter") * 4, number=1,
                 barlines='<barline location="left"><repeat direction="forward"/></barline>')
    m2 = measure(note("D", 4, 4, "quarter") * 4, number=2,
                 barlines='<barline location="right"><repeat direction="backward" times="2"/>'
                          '<ending number="1" type="start"/></barline>')
    m3 = measure(note("E", 4, 4, "quarter") * 4, number=3,
                 barlines='<barline location="left"><ending number="1" type="discontinue"/></barline>')
    p = one_part(tmp_path, m1 + m2 + m3)
    song = load_musicxml(p)
    # 4 played bars of 4 quarters, default 120 bpm: 2 s a bar, 8 s total
    assert len(song.bar_times) == 5                  # 4 played bars: 5 bar lines
    assert song.duration == pytest.approx(8.0)
    # the 1st ending (bar 2, D) sounds only on the first pass, the 2nd (bar 3,
    # E) only on the last
    ds = sorted(n.start for n in song.notes if n.pitch == 62)
    assert ds == [pytest.approx(x) for x in (2.0, 2.5, 3.0, 3.5)]
    es = [n.start for n in song.notes if n.pitch == 64]
    assert es == [pytest.approx(x) for x in (6.0, 6.5, 7.0, 7.5)]
    # bar 1 plays twice (0.0 and 4.0)
    cs = sorted(n.start for n in song.notes if n.pitch == 60)
    assert cs == [pytest.approx(x) for x in (0.0, 0.5, 1.0, 1.5, 4.0, 4.5, 5.0, 5.5)]


def test_play_order_directly():
    from musicxml_loader import _Measure
    import xml.etree.ElementTree as ET
    def mk(num, bl=""):
        return _Measure(ET.fromstring(f'<measure number="{num}">{bl}</measure>'))
    fwd = '<barline location="left"><repeat direction="forward"/></barline>'
    back = '<barline location="right"><repeat direction="backward" times="2"/></barline>'
    # a repeat with no volta: bars 1, 2, 1, 2, then 3
    measures = [mk(1, fwd), mk(2, back), mk(3)]
    assert play_order(measures, []) == [0, 1, 0, 1, 2]
    # the same with a 1st ending on bar 2 and a 2nd on bar 3: 1, 2, 1, 3
    e1 = '<barline location="right"><repeat direction="backward" times="2"/>' \
         '<ending number="1" type="start"/></barline>'
    e2 = '<barline location="left"><ending number="1" type="discontinue"/></barline>'
    measures2 = [mk(1, fwd), mk(2, e1), mk(3, e2)]
    assert play_order(measures2, []) == [0, 1, 0, 2]


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #

def test_musicxml_round_trip(tmp_path):
    body = (note("C", 4, 4, "quarter", finger=3)
            + note("D", 4, 4, "quarter", finger=2)
            + note("E", 4, 4, "quarter"))
    p = one_part(tmp_path, measure(body))
    song = load_musicxml(p, fingering="file")
    fingers = {id(n): n.finger for n in song.notes}
    # re-finger the first note by hand
    first = next(n for n in song.notes if n.pitch == 60)
    fingers[id(first)] = 1
    out = str(tmp_path / "out.musicxml")
    marked = save_musicxml(p, out, song.notes, fingers)
    assert marked == 2                               # the 3 (now 1) and the 2
    back = load_musicxml(out, fingering="file")
    assert len(back.notes) == len(song.notes)
    assert next(n for n in back.notes if n.pitch == 60).finger == 1
    assert next(n for n in back.notes if n.pitch == 62).finger == 2
    assert next(n for n in back.notes if n.pitch == 64).finger is None


def test_mxl_export_is_a_zip(tmp_path):
    body = note("C", 4, 4, "quarter", finger=1) + note("D", 4, 4, "quarter")
    p = one_part(tmp_path, measure(body))
    song = load_musicxml(p, fingering="file")
    fingers = {id(n): n.finger for n in song.notes}
    out = str(tmp_path / "out.mxl")
    save_musicxml(p, out, song.notes, fingers)
    assert zipfile.is_zipfile(out)
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        assert any(n.endswith(".xml") for n in names)
        root = read_root(out)
    assert _local(root.tag) == "score-partwise"


def test_save_adds_staff_from_the_hand(tmp_path):
    body = note("C", 5, 4, "quarter") + note("C", 3, 4, "quarter", staff=2)
    p = one_part(tmp_path, measure(body))
    song = load_musicxml(p, hands_from_file=True)
    out = str(tmp_path / "handed.xml")
    save_musicxml(p, out, song.notes, {})
    root = read_root(out)
    staffs = {}
    for part in _kids(root, "part"):
        for m in _kids(part, "measure"):
            for c in m:
                if _local(c.tag) == "note":
                    s = next((x.text for x in c if _local(x.tag) == "staff"), None)
                    staffs[s] = staffs.get(s, 0) + 1
    assert staffs.get("1") == 1 and staffs.get("2") == 1


def test_load_song_dispatches_by_extension(tmp_path):
    assert is_musicxml("a.xml") and is_musicxml("a.musicxml") and is_musicxml("a.mxl")
    assert not is_musicxml("a.mid")
    body = note("C", 4, 4, "quarter") + note("D", 4, 4, "quarter")
    p = one_part(tmp_path, measure(body))
    song = load_song(p)
    assert [n.pitch for n in song.notes] == [60, 62]


def test_musicxml_rest_parsing(tmp_path):
    body = note("C", 4, 4, "quarter") + note(rest=True, dur=4, type_="quarter")
    p = one_part(tmp_path, measure(body))
    song = load_musicxml(p)
    assert len(song.notes) == 2
    assert not song.notes[0].is_rest
    assert song.notes[1].is_rest
    assert song.notes[1].printed == "quarter"
