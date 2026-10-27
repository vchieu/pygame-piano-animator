"""normalize.py: a played-in piece becomes one tidy voice per hand on an exact grid."""
import os
import random

import pytest

import normalize as N
from midi_loader import Note, _chunks, _events

CELL = 0.175                     # s: a 16th at 85.7 bpm


def _piece(bars=24, jitter=0.012, drift=0.004, lead=1.7, seed=1, pickup=False):
    """
    A 4/4 piece on a CELL grid, played in: RH melody on most cells, LH bass at each bar's first
    cell (a low note held a bar) and an arpeggio note on the beat; every onset jittered, the
    tempo drifting `drift` slower over the piece. Returns (notes, {id(note): true cell index}).
    """
    rnd = random.Random(seed)
    total = bars * 16
    true, out = {}, []

    def at(cell):
        t = lead + cell * CELL
        return t * (1.0 + drift * cell / total) + rnd.gauss(0, jitter)

    def add(cell, length, pitch, hand):
        s = at(cell)
        e = max(s + 0.05, at(cell + length) - rnd.uniform(0.0, 0.04))
        n = Note(pitch, s, e, 90, 0, hand)
        true[id(n)] = cell
        out.append(n)

    if pickup:
        add(-4, 4, 72, "R")
    for b in range(bars):
        add(b * 16, 6, 40 + (b % 3) * 2, "L")                       # the bass, shorter than the bar
        for beat in range(1, 4):
            add(b * 16 + beat * 4, 4, 52 + beat * 3, "L")
        c = 0
        while c < 16:
            length = rnd.choice((1, 2, 2, 4))
            add(b * 16 + c, min(length, 16 - c), 67 + rnd.randrange(0, 9), "R")
            c += length
    return out, true


def test_recovers_grid_tempo_and_bars():
    notes, true = _piece()
    clean = N.normalize(notes)
    assert clean.bpm == pytest.approx(60 / (CELL * 4), abs=0.6)             # 4 cells to the beat
    assert clean.stats["fit"] > 0.97
    # the first note (the first bass) opens bar 1, so a note of true cell c lands on unit 2c
    assert clean.stats["pickup_units"] == 0
    assert clean.notes[0].start == 0.0
    out = {(n.hand, n.pitch, round(n.start / clean.unit)) for n in clean.notes}
    right = sum(1 for n in notes if (n.hand, n.pitch, 2 * true[id(n)]) in out)
    assert right / len(notes) > 0.97


def test_pickup_is_kept_as_a_pickup():
    notes, _ = _piece(pickup=True)
    clean = N.normalize(notes)
    assert clean.stats["pickup_units"] == 3 * clean.units_per_beat       # one beat of music, three of silence before it
    assert clean.pickup_end == pytest.approx(clean.bar_times[1])


def test_one_voice_per_hand():
    notes, _ = _piece(bars=8)
    single = N.normalize(notes, voices="single")
    for hand in "RL":
        ns = sorted((n for n in single.notes if n.hand == hand), key=lambda n: n.start)
        starts = sorted({n.start for n in ns})
        for a, b in zip(starts, starts[1:]):
            assert max(n.end for n in ns if n.start == a) <= b + 1e-9
    assert single.stats["cut"] > 0
    keep = N.normalize(notes, voices="keep")
    assert keep.stats["cut"] == 0
    assert any(n.end - n.start > keep.unit * keep.units_per_beat for n in keep.notes)   # a held bass survives


def test_chord_shares_one_length():
    notes, _ = _piece(bars=8)
    extra = [Note(n.pitch + 4, n.start + 0.004, n.end + 0.3, 90, 0, "R") for n in notes
             if n.hand == "R"][:20]
    clean = N.normalize(notes + extra)
    by = {}
    for n in clean.notes:
        if n.hand == "R":
            by.setdefault(round(n.start, 6), set()).add(round(n.end, 6))
    assert all(len(v) == 1 for v in by.values())


def test_hand_marks_and_other_fields_survive():
    notes, _ = _piece(bars=6)
    clean = N.normalize(notes)
    assert {n.hand for n in clean.notes} == {"L", "R"}
    assert all(n.track == (0 if n.hand == "R" else 1) for n in clean.notes)
    assert len(clean.notes) == len(notes)


def test_pedal_follows_the_notes():
    notes, _ = _piece(bars=6)
    t = notes[40].start
    clean = N.normalize(notes, controls=[(t, 64, 127), (t + 1.0, 64, 0)])
    assert len(clean.controls) == 2 and clean.controls[0][0] < clean.controls[1][0]
    assert clean.controls[0][0] == pytest.approx(clean.map_time(t))


def test_writer_makes_two_staves(tmp_path):
    notes, _ = _piece(bars=6)
    clean = N.normalize(notes, controls=[(notes[10].start, 64, 127)])
    path = str(tmp_path / "clean.mid")
    assert N.write_midi(path, clean) == len(notes)
    data = open(path, "rb").read()
    header, chunks = _chunks(data)
    tracks = [(t, _events(b)) for t, b in chunks if t == b"MTrk"]
    assert len(tracks) == 3
    ppq = int.from_bytes(header[4:6], "big")
    assert ppq == N.PPQ
    tempo = [p for _, k, p in tracks[0][1] if k == "meta" and p[0] == 0x51]
    assert int.from_bytes(tempo[0][1], "big") == pytest.approx(60_000_000 / clean.bpm, abs=1)
    for (_, ev), ch in zip(tracks[1:], (0, 1)):
        ons = [(t, p) for t, k, p in ev if k == "midi" and p[0] & 0xF0 == 0x90 and p[2] > 0]
        assert ons and all(p[0] & 0x0F == ch for _, p in ons)
        assert all(t % (N.PPQ // clean.units_per_beat) == 0 for t, _ in ons)
        names = [p[1] for _, k, p in ev if k == "meta" and p[0] == 0x03]
        assert names == [b"Right hand" if ch == 0 else b"Left hand"]
        assert any(k == "midi" and p[0] & 0xF0 == 0xB0 for _, k, p in ev)   # the pedal reached both hands


def test_long_note_before_an_ornament_is_tidied():
    """A note held up to a 32nd run that starts between two cells ends on a cell: no second voice."""
    n = lambda p: Note(p, 0.0, 9.0, 90, 0, "R")
    by_u = {112: [(n(72), 125.0)], 125: [(n(76), 126.0)], 126: [(n(74), 127.0)], 127: [(n(72), 128.0)]}
    c = {"cut": 0, "rests": 0, "merged": 0, "tidied": 0}
    out = {u: ends[0] for u, ends, _ in N._voice(by_u, "single", "median", 6, c)}
    assert out[112] == 124 and c["tidied"] == 1                       # a dotted quarter, then a 32nd rest
    assert (out[125], out[126], out[127]) == (126, 127, 128)           # the run itself is untouched
    raw = {u: ends[0] for u, ends, _ in N._voice(by_u, "single", "median", 6, dict(c), tidy=False)}
    assert raw[112] == 125


def test_every_length_is_a_written_value():
    notes, _ = _piece(bars=8)
    clean = N.normalize(notes)
    for n in clean.notes:
        k = round((n.end - n.start) / clean.unit)
        assert k in N.STD_LEN or k > N.STD_LEN[-1]
        assert not (k >= 4 and round(n.end / clean.unit) % 2)


def test_too_few_notes():
    with pytest.raises(ValueError):
        N.find_cell([0.0, 0.3, 0.6])


# ----- the app's integration: loading, the app setting, the editor's export ----------------
def test_load_song_normalizes_when_asked():
    import midi_loader
    from conftest import midi_path
    raw = midi_loader.load_song(midi_path("demo_song.mid"), normalize=False)
    assert not raw.normalized and raw.clean is None
    clean = midi_loader.load_song(midi_path("demo_song.mid"), normalize=True)
    assert clean.normalized and clean.clean is not None
    u = clean.clean.unit                              # every start sits on the cleaned grid
    assert all(abs(n.start / u - round(n.start / u)) < 1e-6 for n in clean.notes)
    assert any(l.startswith("grid:") for l in clean.cleanup)      # the report rides along
    assert midi_loader.maybe_normalize(clean, True) is clean      # already cleaned: left alone


def test_auto_normalize_app_setting_is_honoured():
    import midi_loader
    import pianist
    from conftest import midi_path
    p = midi_path("demo_song.mid")
    pianist.set_app_setting("auto_normalize", True)
    assert midi_loader.load_song(p).normalized         # None follows the app setting
    assert not midi_loader.load_song(p, normalize=False).normalized
    pianist.set_app_setting("auto_normalize", False)
    assert not midi_loader.load_song(p).normalized


def test_load_with_hands_normalizes():
    from conftest import midi_path
    from hands import load_with_hands
    from midi_loader import RIGHT
    song, hs = load_with_hands(midi_path("demo_song.mid"), normalize=True)
    assert song.normalized and hs and RIGHT in hs


def test_a_song_too_short_to_clean_keeps_its_timings():
    import midi_loader
    from midi_loader import LEFT, MidiSong, Note, TrackInfo, RIGHT
    tracks = [TrackInfo(0, "Right", 0, 2, RIGHT)]
    song = MidiSong([Note(60, 0.0, 1.0, 90, 0, RIGHT), Note(53, 2.0, 5.0, 90, 1, LEFT)], tracks, 6.0, [], [])
    assert midi_loader.maybe_normalize(song, True) is song          # a raw song that loads beats none at all
    assert not song.normalized and any("Normalize failed" in l for l in song.cleanup)


def test_writer_fingers_survive_a_round_trip(tmp_path):
    """The editor's markers ("R3"/"L1"/"Rg", bare "R") are written in the form read_markers
    reads, so loading the exported file restores the fingering - a chord's notes included."""
    import midi_loader
    notes, _ = _piece(bars=6)
    notes += [Note(n.pitch + 4, n.start + 0.004, n.end + 0.3, 90, 0, n.hand) for n in notes[:12]]  # chords
    clean = N.normalize(notes)
    fingers = {id(n): (n.hand if i % 11 == 0 else
                       n.hand + ("g" if i % 7 == 0 else str(1 + i % 5)))
               for i, n in enumerate(clean.notes)}
    path = str(tmp_path / "fingered.mid")
    assert N.write_midi(path, clean, fingers=fingers) == len(clean.notes)
    marks = midi_loader.read_markers(path)
    assert len(marks) == len(clean.notes)             # every note kept its own marker (chords included)
    tpu = N.PPQ // clean.units_per_beat
    for n in clean.notes:
        lab = fingers[id(n)]
        f = None if len(lab) == 1 else ("g" if lab[1:] == "g" else int(lab[1:]))
        assert marks[(round(n.start / clean.unit) * tpu, n.pitch)] == (lab[0], f)
    fing = midi_loader.read_fingering(path)           # the int fingers alone (no hands, no glissandi)
    assert sum(1 for v in fing.values() if isinstance(v, int)) == sum(
        1 for lab in fingers.values() if len(lab) > 1 and lab[1] != "g")
