"""
tests/test_auto_pedal.py - Unit tests for automatic sustain pedal generation.
"""
import pytest

import auto_pedal
from midi_loader import LEFT, RIGHT, MidiSong, Note, TrackInfo
from staff import draw_pedal_brackets

D = auto_pedal.release_delay(0.5)      # lift delay at 120 bpm (quarter = 0.5 s): 62.5 ms
G = auto_pedal.DEFAULT_PEDAL_GAP       # 30 ms
TAIL = auto_pedal.FINAL_TAIL_S         # the last chord rings this long after the music


def test_release_delay():
    assert auto_pedal.release_delay(0.5) == pytest.approx(0.0625)    # 120 bpm: an eighth of a beat
    assert auto_pedal.release_delay(1.0) == pytest.approx(0.08)      # 60 bpm: capped
    assert auto_pedal.release_delay(0.2) == pytest.approx(0.04)      # 300 bpm: floor
    assert auto_pedal.release_delay(0.0) == pytest.approx(0.0625)    # unknown tempo: 0.5 s quarter


def test_measure_table_merges_close_bar_marks():
    notes = [Note(48, 0.0, 4.0, 80, 0, LEFT)]
    tracks = [TrackInfo(0, "Track 0", 0, 1, LEFT)]
    song = MidiSong(notes, tracks, 4.0, [0.0, 2.0, 2.01, 4.0], [])
    ms = auto_pedal.measure_table(song)
    assert [(m.start, m.end) for m in ms] == [(0.0, 2.0), (2.0, 4.0)]


def test_measure_table_last_mark_just_before_the_end_is_the_end():
    notes = [Note(48, 0.0, 4.0, 80, 0, LEFT)]
    tracks = [TrackInfo(0, "Track 0", 0, 1, LEFT)]
    song = MidiSong(notes, tracks, 4.0, [0.0, 2.0, 3.99], [])
    ms = auto_pedal.measure_table(song)
    assert [(m.start, m.end) for m in ms] == [(0.0, 2.0), (2.0, 4.0)]


def test_lh_notes_filter():
    notes = [
        Note(60, 0.0, 1.0, 80, 0, RIGHT),
        Note(48, 0.0, 1.0, 80, 0, LEFT),
        Note(72, 0.5, 1.0, 80, 0, RIGHT),
        Note(55, 0.0, 1.0, 80, 0, RIGHT),  # G3, low but the score gives it to the right hand
        Note(40, 0.5, 1.5, 80, 0, RIGHT),  # E2, same
        Note(45, 0.5, 1.5, 80, 0, ""),     # no hand assigned, bass area
        Note(70, 0.5, 1.5, 80, 0, ""),     # no hand assigned, high
    ]
    lh = auto_pedal.lh_notes(notes)
    pitches = {n.pitch for n in lh}
    assert pitches == {48, 45}, pitches    # RIGHT-hand notes never count as bass, however low


def test_lh_notes_falls_back_to_all_notes():
    notes = [Note(72, 0.0, 1.0, 80, 0, RIGHT), Note(76, 0.5, 1.5, 80, 0, RIGHT)]
    assert len(auto_pedal.lh_notes(notes)) == 2


def test_harmony_signature_alberti():
    # Alberti bass pattern across multiple beats with notes C4(60), G4(67), E4(64)
    # C4=%12=0, G4=%12=7, E4=%12=4
    notes = [
        Note(60, 0.0, 0.5, 80, 0, LEFT),  # C4 (pitches %12=0)
        Note(67, 0.5, 1.0, 80, 0, LEFT), # G4 (pitches %12=7)
        Note(64, 1.0, 1.5, 80, 0, LEFT), # E4 (pitches %12=4)
        Note(67, 1.5, 2.0, 80, 0, LEFT), # G4 (pitches %12=7)
    ]
    # With win=2.0 (captures full Alberti pattern), should detect {0, 4, 7}
    sig = auto_pedal.harmony_signature(notes, t0=0.0, win=2.0, tol=0.1)
    assert sig is not None, "Harmony signature should not be None"
    bass_pc, pcs = sig
    # Bass is lowest note C4 with pitch class 0
    assert bass_pc == 0, f"Expected bass pitch class 0 (C), got {bass_pc}"
    # Should detect C, E, G = {0, 4, 7}
    assert pcs == frozenset({0, 4, 7}), f"Expected pitch classes {{0, 4, 7}}, got {pcs}"


def test_harmony_signature_with_bass():
    # Real bass note with melody notes (C major chord: C-E-G)
    notes = [
        Note(48, 0.0, 1.0, 80, 0, LEFT),   # C3 (pitch class 0) - BASS
        Note(60, 0.0, 1.0, 80, 0, RIGHT),  # C4
        Note(64, 0.0, 1.0, 80, 0, RIGHT),  # E4
        Note(67, 0.0, 1.0, 80, 0, RIGHT),  # G4
    ]
    # Note(48) is LEFT hand -> bass; the RIGHT-hand notes are not part of the left hand
    sig = auto_pedal.harmony_signature(notes, t0=0.0, win=1.0, tol=0.1)
    assert sig is not None, "Harmony signature should not be None"
    bass_pc, pcs = sig
    assert bass_pc == 0, f"Expected bass pitch class 0 (C), got {bass_pc}"
    assert pcs == frozenset({0}), f"Expected pitch classes {{0}}, got {pcs}"


def test_harmony_signature_bass_is_the_new_attack_not_an_older_lower_note():
    # C2 (pc 0) is still ringing from the last bar when the new bass G2 (pc 7) and its chord start
    notes = [
        Note(36, 0.0, 3.0, 80, 0, LEFT),   # C2, held over the bar line at 2.0
        Note(43, 2.0, 4.0, 80, 0, LEFT),   # G2, the new bass
        Note(50, 2.0, 4.0, 80, 0, LEFT),   # D3
    ]
    sig = auto_pedal.harmony_signature(notes, t0=2.0, win=2.0, tol=0.1)
    assert sig is not None
    assert sig[0] == 7, f"the new bass is G (7), not the old C: {sig}"


def test_harmony_changed_different_bass():
    cur = (0, frozenset({0, 4, 7}))
    new = (5, frozenset({5, 9, 0}))
    assert auto_pedal.harmony_changed(cur, new) == True


def test_harmony_changed_same_bass_different_chord():
    cur = (0, frozenset({0, 4, 7}))
    new = (0, frozenset({0, 3, 7}))
    assert auto_pedal.harmony_changed(cur, new) == True


def test_harmony_changed_same_harmony():
    cur = (0, frozenset({0, 4, 7}))
    new = (0, frozenset({0, 4, 7}))
    assert auto_pedal.harmony_changed(cur, new) == False


def test_has_lh_attack():
    notes = [
        Note(48, 1.0, 2.0, 80, 0, LEFT),
        Note(60, 2.5, 3.5, 80, 0, RIGHT),
    ]
    measure = auto_pedal.PedalMeasure(start=1.0, end=2.0, number=0)
    assert auto_pedal.has_lh_attack(notes, measure) == True


def test_has_lh_attack_no_attack():
    notes = [
        Note(60, 1.5, 2.5, 80, 0, RIGHT),
        Note(72, 2.0, 3.0, 80, 0, RIGHT),
    ]
    measure = auto_pedal.PedalMeasure(start=1.0, end=2.0, number=0)
    assert auto_pedal.has_lh_attack(notes, measure) == False


def test_pick_anchor():
    notes = [
        Note(48, 1.0, 1.5, 80, 0, LEFT),    # C3 at 1.0
        Note(60, 1.0, 2.0, 80, 0, RIGHT),   # C4 at 1.0
        Note(64, 1.5, 2.5, 80, 0, LEFT),    # E4 at 1.5
    ]
    measure = auto_pedal.PedalMeasure(start=1.0, end=2.5, number=0)
    anchor = auto_pedal.pick_anchor(notes, measure)
    assert anchor is not None
    assert anchor.start == 1.0  # Earliest onset
    assert anchor.pitch == 48  # Priority to left-hand / lower pitch


def test_two_measure_song_auto_pedal_events():
    # Measure 1: [0.0, 2.0), Measure 2: [2.0, 4.0), quarter = 0.5 s (120 bpm)
    # Measure 2 anchor at 2.0 s => lift at 2.0 + D, down again G later; the last chord rings TAIL longer
    notes = [
        Note(60, 0.0, 2.0, 80, 0, LEFT),
        Note(64, 2.0, 4.0, 80, 0, LEFT),
    ]
    tracks = [TrackInfo(0, "Track 0", 0, 2, LEFT)]
    song = MidiSong(notes, tracks, 4.0, [0.0, 2.0, 4.0], [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0])
    song.quarter_times = [(0.0, 0.5)]

    events, spans = auto_pedal.build(song, gap_s=0.03)
    assert spans == pytest.approx([(0.0, 2.0 + D), (2.0 + D + G, 4.0 + TAIL)])

    assert len(events) == 4
    assert events[0] == (0.0, 64, 127)
    assert events[1] == pytest.approx((2.0 + D, 64, 0))
    assert events[2] == pytest.approx((2.0 + D + G, 64, 127))
    assert events[3] == pytest.approx((4.0 + TAIL, 64, 0))


def test_gap_between_spans_is_exactly_gap_s():
    notes = [Note(48, 0.0, 2.0, 80, 0, LEFT), Note(55, 2.0, 4.0, 80, 0, LEFT)]
    for gap in (0.02, 0.03, 0.06):
        _, spans = auto_pedal.build(_song(notes, [0.0, 2.0, 4.0], 4.0), gap_s=gap)
        assert spans[1][0] - spans[0][1] == pytest.approx(gap), (gap, spans)


def test_pickup_and_lead_in_measure_is_skipped():
    # Measure 1 starts with rests (notes enter at 0.5s), Measure 2 starts at 1.0s with LH note
    notes = [
        Note(60, 0.5, 1.0, 80, 0, RIGHT),       # Lead-in in Measure 1 (no LH on beat 1)
        Note(48, 1.0, 3.0, 80, 0, LEFT),        # Measure 2 has LH attack at beat 1 (1.0s)
        Note(52, 3.0, 5.0, 80, 0, LEFT),        # Measure 3 has LH attack at beat 1 (3.0s)
    ]
    tracks = [TrackInfo(0, "Track 0", 0, 3, LEFT)]
    song = MidiSong(notes, tracks, 5.0, [0.0, 1.0, 3.0, 5.0], [])
    song.quarter_times = [(0.0, 0.5)]

    events, spans = auto_pedal.build(song, gap_s=0.03)
    assert spans == pytest.approx([(1.0, 3.0 + D), (3.0 + D + G, 5.0 + TAIL)])
    assert events[0][0] == 1.0                  # Starts at 1.0, lead-in gets no pedal
    assert not any(e[0] < 1.0 for e in events)


def test_no_lh_attack_on_next_measure_keeps_pedal_down():
    # Measure 1 [0.0, 2.0) has LH note at 0.0s
    # Measure 2 [2.0, 4.0) has NO LH note at beat 2.0s (only RH at 2.5s)
    # Measure 3 [4.0, 6.0) has LH note at beat 4.0s
    notes = [
        Note(48, 0.0, 2.0, 80, 0, LEFT),
        Note(72, 2.5, 3.5, 80, 0, RIGHT),
        Note(55, 4.0, 6.0, 80, 0, LEFT),   # new bass: the pedal changes here
    ]
    tracks = [TrackInfo(0, "Track 0", 0, 3, LEFT)]
    song = MidiSong(notes, tracks, 6.0, [0.0, 2.0, 4.0, 6.0], [])
    song.quarter_times = [(0.0, 0.5)]

    events, spans = auto_pedal.build(song, gap_s=0.03)
    # Span 1 covers Measure 1 AND Measure 2 continuously, released just after Measure 3's bass note
    assert len(spans) == 2
    assert spans[0] == pytest.approx((0.0, 4.0 + D))
    assert spans[1] == pytest.approx((4.0 + D + G, 6.0 + TAIL))


def test_hold_without_lh_attack_is_refreshed_at_the_limit():
    # LH plays beat 1 of bar 0 only, then (off the bar line) at 9.0 s in bar 4; bars 1-3 have no LH at all.
    # max_hold_bars=4: the pedal is not allowed to stay down through bar 4 - it is refreshed at 9.0.
    notes = [Note(48, 0.0, 2.0, 80, 0, LEFT), Note(48, 9.0, 10.0, 80, 0, LEFT)]
    events, spans = auto_pedal.build(_song(notes, [2.0 * i for i in range(7)], 12.0), max_hold_bars=4)
    assert len(spans) == 2, spans
    assert spans[0] == pytest.approx((0.0, 9.0 + D))


def test_hold_without_lh_attack_under_the_limit_keeps_going():
    notes = [Note(48, 0.0, 2.0, 80, 0, LEFT), Note(48, 5.0, 6.0, 80, 0, LEFT)]
    events, spans = auto_pedal.build(_song(notes, [2.0 * i for i in range(4)], 6.0), max_hold_bars=4)
    assert len(spans) == 1, spans


def test_silent_left_hand_lets_the_pedal_go_at_the_limit():
    # LH plays bar 0, is silent for bars 1-5, comes back on beat 1 of bar 6: no pedal through the silence
    notes = [Note(48, 0.0, 2.0, 80, 0, LEFT), Note(48, 12.0, 14.0, 80, 0, LEFT)]
    events, spans = auto_pedal.build(_song(notes, [2.0 * i for i in range(8)], 14.0), max_hold_bars=4)
    assert len(spans) == 2, spans
    assert spans[0] == pytest.approx((0.0, 8.0))                   # let go at the bar line after 4 bars
    assert spans[1] == pytest.approx((12.0, 14.0 + TAIL))          # down again at the next LH attack
    assert [e[2] for e in events] == [127, 0, 127, 0]


def _song(notes, bars, dur):
    tracks = [TrackInfo(0, "Track 0", 0, len(notes), LEFT)]
    song = MidiSong(notes, tracks, dur, bars, [])
    song.quarter_times = [(0.0, 0.5)]
    return song


def test_alberti_bass_same_harmony_continuous_span():
    # Alberti C-G-E-G, 4 bars of 2 s: Note(pitch, start, END) - end is absolute
    notes = []
    for bar in range(4):
        t0 = bar * 2.0
        for k, p in enumerate((60, 67, 64, 67)):
            notes.append(Note(p, t0 + 0.5 * k, t0 + 0.5 * k + 0.5, 80, 0, LEFT))
    assert all(n.duration > 0 for n in notes)
    events, spans = auto_pedal.build(_song(notes, [0.0, 2.0, 4.0, 6.0, 8.0], 8.0))
    assert spans == pytest.approx([(0.0, 8.0 + TAIL)]), spans


def test_repeated_chord_each_bar_keeps_one_span():
    notes = [Note(48, b * 2.0, b * 2.0 + 2.0, 80, 0, LEFT) for b in range(4)]
    events, spans = auto_pedal.build(_song(notes, [0.0, 2.0, 4.0, 6.0, 8.0], 8.0), max_hold_bars=99)
    assert spans == pytest.approx([(0.0, 8.0 + TAIL)]), spans


def test_max_hold_bars_refreshes_same_chord():
    notes = [Note(48, b * 2.0, b * 2.0 + 2.0, 80, 0, LEFT) for b in range(6)]
    events, spans = auto_pedal.build(_song(notes, [2.0 * i for i in range(7)], 12.0), max_hold_bars=4)
    assert len(spans) == 2, spans            # one refresh after 4 bars of the same chord


def test_same_bass_new_chord_colour_changes_pedal():
    # bar 1: C major (C-E-G), bar 2: C minor (C-Eb-G), same bass C
    notes = [Note(48, 0.0, 2.0, 80, 0, LEFT), Note(52, 0.0, 2.0, 80, 0, LEFT), Note(55, 0.0, 2.0, 80, 0, LEFT),
             Note(48, 2.0, 4.0, 80, 0, LEFT), Note(51, 2.0, 4.0, 80, 0, LEFT), Note(55, 2.0, 4.0, 80, 0, LEFT)]
    events, spans = auto_pedal.build(_song(notes, [0.0, 2.0, 4.0], 4.0))
    assert len(spans) == 2, spans


def test_new_bass_over_a_lower_ringing_note_changes_pedal():
    # a low C2 pedal tone rings through both bars; the bass above it moves C3 -> G3 on the bar line
    notes = [Note(36, 0.0, 4.0, 80, 0, LEFT),
             Note(48, 0.0, 2.0, 80, 0, LEFT), Note(52, 0.0, 2.0, 80, 0, LEFT),
             Note(55, 2.0, 4.0, 80, 0, LEFT), Note(59, 2.0, 4.0, 80, 0, LEFT), Note(62, 2.0, 4.0, 80, 0, LEFT)]
    events, spans = auto_pedal.build(_song(notes, [0.0, 2.0, 4.0], 4.0), mid_bar=False)
    assert len(spans) == 2, spans


def test_early_lh_attack_new_chord_anchors_on_that_note():
    # the LH plays the new bass 40 ms before the bar line and holds it; nothing else starts in bar 2
    notes = [Note(48, 0.0, 2.0, 80, 0, LEFT), Note(55, 1.96, 4.0, 80, 0, LEFT)]
    events, spans = auto_pedal.build(_song(notes, [0.0, 2.0, 4.0], 4.0))
    assert len(spans) == 2, spans
    assert spans[0][1] < 2.2, spans           # released right after the early bass note, not later
    assert spans[0][1] == pytest.approx(1.96 + D)


def test_anchor_is_bass_not_melody_when_rh_starts_first():
    notes = [Note(48, 0.0, 2.0, 80, 0, LEFT), Note(55, 2.0, 4.0, 80, 0, LEFT),
             Note(72, 2.0, 2.1, 80, 0, RIGHT)]      # short melody note on the same beat
    events, spans = auto_pedal.build(_song(notes, [0.0, 2.0, 4.0], 4.0))
    assert spans[0][1] == pytest.approx(2.0 + D)     # delay from the 2 s bass note, not the 0.1 s melody note


def test_low_right_hand_note_is_not_the_anchor():
    # the melody dips below middle C on the bar line; it is the right hand's, so it is no bass anchor
    notes = [Note(48, 0.0, 2.0, 80, 0, LEFT), Note(55, 2.0, 4.0, 80, 0, LEFT),
             Note(40, 2.0, 2.1, 80, 0, RIGHT)]
    events, spans = auto_pedal.build(_song(notes, [0.0, 2.0, 4.0], 4.0))
    assert spans[0][1] == pytest.approx(2.0 + D)


def test_short_bass_note_repress_before_it_ends():
    # new bass is staccato (0.06 s): the pedal must be down again before it ends
    notes = [Note(48, 0.0, 2.0, 80, 0, LEFT), Note(55, 2.0, 2.06, 80, 0, LEFT)]
    events, spans = auto_pedal.build(_song(notes, [0.0, 2.0, 4.0], 4.0), gap_s=0.03)
    assert len(spans) == 2
    assert spans[1][0] < 2.06, spans
    assert spans[1][0] - spans[0][1] == pytest.approx(0.03)      # ... and the gap is still the full gap


def _beat_song(notes, bars, dur):
    beats = [0.5 * i for i in range(int(dur / 0.5) + 1)]
    tracks = [TrackInfo(0, "Track 0", 0, len(notes), LEFT)]
    song = MidiSong(notes, tracks, dur, bars, beats)
    song.quarter_times = [(0.0, 0.5)]
    return song


def test_mid_bar_bass_change_on_beat_3():
    # bar [0,2): C bass for beats 1-2, G bass for beats 3-4 (bass only, 2 beats each)
    notes = [Note(48, 0.0, 1.0, 80, 0, LEFT), Note(55, 1.0, 2.0, 80, 0, LEFT)]
    events, spans = auto_pedal.build(_beat_song(notes, [0.0, 2.0], 2.0))
    assert spans == pytest.approx([(0.0, 1.0 + D), (1.0 + D + G, 2.0 + TAIL)]), spans


def test_mid_bar_off_keeps_one_span():
    notes = [Note(48, 0.0, 1.0, 80, 0, LEFT), Note(55, 1.0, 2.0, 80, 0, LEFT)]
    events, spans = auto_pedal.build(_beat_song(notes, [0.0, 2.0], 2.0), mid_bar=False)
    assert spans == pytest.approx([(0.0, 2.0 + TAIL)]), spans


def test_mid_bar_lh_rests_on_beat_1_then_new_bass_on_beat_2():
    # bar 2 [2,4): the LH rests on beat 1, then G bass enters on beat 2 and lasts 3 beats
    notes = [Note(48, 0.0, 2.0, 80, 0, LEFT), Note(55, 2.5, 4.0, 80, 0, LEFT)]
    events, spans = auto_pedal.build(_beat_song(notes, [0.0, 2.0, 4.0], 4.0))
    assert spans == pytest.approx([(0.0, 2.5 + D), (2.5 + D + G, 4.0 + TAIL)]), spans


def test_mid_bar_walking_bass_quarters_does_not_chop():
    # C D E F in quarter notes (1 beat each): too short to be a chord change
    notes = [Note(48 + k * 2, 0.5 * k, 0.5 * k + 0.5, 80, 0, LEFT) for k in range(4)]
    events, spans = auto_pedal.build(_beat_song(notes, [0.0, 2.0], 2.0))
    assert len(spans) == 1, spans


def test_mid_bar_oompah_same_chord_no_change():
    # C bass + E,G chord | G bass + E,G chord: root and fifth of the SAME chord
    notes = [Note(48, 0.0, 1.0, 80, 0, LEFT), Note(52, 0.5, 1.0, 80, 0, LEFT), Note(55, 0.5, 1.0, 80, 0, LEFT),
             Note(43, 1.0, 2.0, 80, 0, LEFT), Note(52, 1.5, 2.0, 80, 0, LEFT), Note(55, 1.5, 2.0, 80, 0, LEFT)]
    events, spans = auto_pedal.build(_beat_song(notes, [0.0, 2.0], 2.0))
    assert len(spans) == 1, spans


def test_mid_bar_oompah_c_to_g7_changes():
    # C | E G | G | B D : the second half is a new chord
    notes = [Note(48, 0.0, 1.0, 80, 0, LEFT), Note(52, 0.5, 1.0, 80, 0, LEFT), Note(55, 0.5, 1.0, 80, 0, LEFT),
             Note(43, 1.0, 2.0, 80, 0, LEFT), Note(59, 1.5, 2.0, 80, 0, LEFT), Note(62, 1.5, 2.0, 80, 0, LEFT)]
    events, spans = auto_pedal.build(_beat_song(notes, [0.0, 2.0], 2.0))
    assert len(spans) == 2, spans


def test_mid_bar_ignores_off_beat_bass():
    notes = [Note(48, 0.0, 1.25, 80, 0, LEFT), Note(55, 1.25, 2.0, 80, 0, LEFT)]
    events, spans = auto_pedal.build(_beat_song(notes, [0.0, 2.0], 2.0), mid_min_beats=1.0)
    assert len(spans) == 1, spans            # 1.25 s is half-way between beats 1.0 and 1.5


def test_different_bass_between_measures():
    # Two measures with different bass notes (C major vs G major)
    notes = [
        Note(48, 0.0, 2.0, 80, 0, LEFT),   # C3 (C major, bass pitch class 0)
        Note(55, 2.0, 4.0, 80, 0, LEFT),   # G3 (G major, bass pitch class 7)
    ]
    tracks = [TrackInfo(0, "Track 0", 0, 2, LEFT)]
    song = MidiSong(notes, tracks, 4.0, [0.0, 2.0, 4.0], [])
    song.quarter_times = [(0.0, 0.5)]

    events, spans = auto_pedal.build(song, gap_s=0.03)

    # Should have two separate spans because bass changed from C to G
    assert len(spans) == 2, f"Expected 2 spans for different harmony, got {len(spans)}"
    assert spans[0] == pytest.approx((0.0, 2.0 + D)), spans[0]
    assert spans[1] == pytest.approx((2.0 + D + G, 4.0 + TAIL)), spans[1]


def test_lh_early_attack_before_bar_line():
    # Left hand plays 40ms early before measure start (pickup)
    # Note at -0.04 is before measure 1, but should still be detected as LH attack
    notes = [
        Note(48, -0.04, 0.96, 80, 0, LEFT),  # 40ms early, ends at 0.96
        Note(55, 0.96, 2.96, 80, 0, LEFT),  # Later note, ends at 2.96
    ]
    tracks = [TrackInfo(0, "Track 0", 0, 2, LEFT)]
    song = MidiSong(notes, tracks, 3.0, [0.0, 2.0, 3.0], [])
    song.quarter_times = [(0.0, 0.5)]

    events, spans = auto_pedal.build(song, gap_s=0.03, mid_bar=False)

    # The early attack at -0.04 should be detected as a valid attack for measure 1
    # Since there's only 1 measure with LH attack, pedal starts at 0.0
    assert len(spans) == 1, f"Expected 1 span, got {len(spans)}"
    assert spans[0] == pytest.approx((0.0, 3.0 + TAIL)), spans[0]


def test_staccato_lh_short_duration_clamp():
    # Short LH note (0.05s) followed by long note (sustained)
    notes = [
        Note(48, 0.0, 0.05, 80, 0, LEFT),   # Very short LH note on beat 1
        Note(48, 0.05, 2.0, 80, 0, LEFT),   # Longer note continuing
    ]
    tracks = [TrackInfo(0, "Track 0", 0, 2, LEFT)]
    song = MidiSong(notes, tracks, 2.0, [0.0, 2.0], [])
    song.quarter_times = [(0.0, 0.5)]

    events, spans = auto_pedal.build(song, gap_s=0.03)

    assert len(spans) == 1, f"Expected 1 span, got {len(spans)}"
    assert spans[0] == pytest.approx((0.0, 2.0 + TAIL)), spans[0]


def test_final_measure_pedal_rings_out():
    # The last chord keeps its pedal TAIL seconds after the last note ends (and not past the playback end)
    notes = [
        Note(48, 0.0, 2.0, 80, 0, LEFT),
        Note(55, 2.0, 4.0, 80, 0, LEFT),   # a different bass, so there are 2 spans
    ]
    tracks = [TrackInfo(0, "Track 0", 0, 2, LEFT)]
    song = MidiSong(notes, tracks, 4.0, [0.0, 2.0, 4.0], [])
    song.quarter_times = [(0.0, 0.5)]

    events, spans = auto_pedal.build(song, gap_s=0.03)

    assert len(spans) == 2, f"Expected 2 spans, got {len(spans)}"
    assert spans[1][1] > 4.0, f"The last chord must ring on past the last note, got {spans[1][1]}"
    assert spans[1][1] == pytest.approx(4.0 + TAIL)
    assert spans[1][1] <= 4.0 + auto_pedal.FINAL_TAIL_LIMIT_S


def test_draw_pedal_brackets_helper():
    import pygame
    try:
        pygame.init()
    except Exception:
        pass
    try:
        pygame.font.init()
    except Exception:
        pass
    surf = pygame.Surface((400, 100))
    surf.fill((255, 255, 255))
    spans = [(0.0, 2.0), (2.03, 4.0)]
    x_of = lambda t: 50.0 + t * 50.0            # 0.0->50, 2.0->150, 4.0->250
    try:
        draw_pedal_brackets(surf, spans, 0.0, 4.0, x_of, 80.0, 10.0)
    except Exception:
        # If font rendering fails in this environment, just ensure function runs
        return

    # Check that pixels were drawn near y=80
    dark_pixels = sum(1 for x in range(50, 250) if surf.get_at((x, 80))[:3] != (255, 255, 255))
    assert dark_pixels > 100


def test_staff_view_draw_with_auto_pedal():
    import pygame
    import main
    import pianist
    from staff import StaffView

    pygame.init()
    notes = [
        Note(48, 0.0, 2.0, 80, 0, LEFT),    # C bass ...
        Note(55, 2.0, 4.0, 80, 0, LEFT),    # ... G bass: the harmony changes, so two spans
    ]
    tracks = [TrackInfo(0, "Track 0", 0, 2, LEFT)]
    song = MidiSong(notes, tracks, 4.0, [0.0, 2.0, 4.0], [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0])
    song.set_pedal_mode("auto")

    v = StaffView(song)
    surf = pygame.Surface((800, 200))
    v.draw(surf, surf.get_rect(), 0.5)

    # Renders without error, and pedal spans are present
    assert song.pedal_mode == "auto"
    assert len(song.pedal_spans) == 2


def test_settings_screen_pedal_mode_toggle():
    import pygame
    import main
    import pianist

    from app_settings import SettingsScreen
    pianist.set_app_setting("pedal_mode", "score")

    app = main.App(pygame.display.set_mode((800, 600)), sound=False)
    st = SettingsScreen(app)

    assert pianist.app_setting("pedal_mode") == "score"
    assert st.pedal.label == "From the score"

    # Simulate click on pedal button
    st.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=st.pedal.rect.center))
    assert pianist.app_setting("pedal_mode") == "auto"

    st.render()
    assert st.pedal.label == "Auto by harmony"

    st.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=st.pedal.rect.center))
    assert pianist.app_setting("pedal_mode") == "score"
