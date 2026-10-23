"""
tests/test_auto_pedal.py - Unit tests for automatic sustain pedal generation.
"""
import pytest

import auto_pedal
from midi_loader import LEFT, RIGHT, MidiSong, Note, TrackInfo
from staff import draw_pedal_brackets


def test_split_lengths():
    # 4.0 QL (whole note): dur=2.0s, quarter=0.5s => dur >= quarter => d1 = 0.5/4 = 0.125s
    d1, d2 = auto_pedal.split_lengths(2.0, 0.5)
    assert pytest.approx(d1) == 0.125
    assert pytest.approx(d2) == 1.875

    # 1.0 QL (quarter note): dur=0.5s, quarter=0.5s => d1 = 0.125s, d2 = 0.375s
    d1, d2 = auto_pedal.split_lengths(0.5, 0.5)
    assert pytest.approx(d1) == 0.125
    assert pytest.approx(d2) == 0.375

    # 0.5 QL (eighth note): dur=0.25s, quarter=0.5s => dur < quarter => d1 = 0.125s, d2 = 0.125s
    d1, d2 = auto_pedal.split_lengths(0.25, 0.5)
    assert pytest.approx(d1) == 0.125
    assert pytest.approx(d2) == 0.125

    # 0.25 QL (sixteenth note): dur=0.125s, quarter=0.5s => d1 = 0.0625s, d2 = 0.0625s
    d1, d2 = auto_pedal.split_lengths(0.125, 0.5)
    assert pytest.approx(d1) == 0.0625
    assert pytest.approx(d2) == 0.0625


def test_lh_notes_filter():
    notes = [
        Note(60, 0.0, 1.0, 80, 0, RIGHT),
        Note(48, 0.0, 1.0, 80, 0, LEFT),
        Note(72, 0.5, 1.0, 80, 0, RIGHT),
        Note(55, 0.0, 1.0, 80, 0, RIGHT),  # B3, bass area
        Note(40, 0.5, 1.5, 80, 0, RIGHT),  # G1, bass area
    ]
    lh = auto_pedal.lh_notes(notes)
    assert len(lh) == 3, f"Expected 3 LH notes, got {len(lh)}"
    pitches = {n.pitch for n in lh}
    assert 48 in pitches  # LEFT A2
    assert 55 in pitches  # B3 (bass area, RIGHT hand)
    assert 40 in pitches  # G1 (bass area, RIGHT hand)


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
        Note(48, 0.0, 1.0, 80, 0, LEFT),   # A2 (pitches %12=0) - BASS
        Note(60, 0.0, 1.0, 80, 0, RIGHT),  # C4 (pitches %12=8)
        Note(64, 0.0, 1.0, 80, 0, RIGHT),  # E4 (pitches %12=4)
        Note(67, 0.0, 1.0, 80, 0, RIGHT),  # G4 (pitches %12=7)
    ]
    # With win=1.0, tol=0.1, t0=0.0, the signature detects bass notes only
    # Note(48) is LEFT hand → included as bass
    # Notes(60, 64, 67) are RIGHT hand with pitch >= 60 → excluded from bass
    sig = auto_pedal.harmony_signature(notes, t0=0.0, win=1.0, tol=0.1)
    assert sig is not None, "Harmony signature should not be None"
    bass_pc, pcs = sig
    assert bass_pc == 0, f"Expected bass pitch class 0 (C), got {bass_pc}"
    assert pcs == frozenset({0}), f"Expected pitch classes {{0}}, got {pcs}"


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
        Note(48, 1.0, 1.5, 80, 0, LEFT),    # A2 at 1.0
        Note(60, 1.0, 2.0, 80, 0, RIGHT),   # C4 at 1.0
        Note(64, 1.5, 2.5, 80, 0, LEFT),    # E4 at 1.5
    ]
    measure = auto_pedal.PedalMeasure(start=1.0, end=2.5, number=0)
    anchor = auto_pedal.pick_anchor(notes, measure)
    assert anchor is not None
    assert anchor.start == 1.0  # Earliest onset
    assert anchor.pitch == 48  # Priority to left-hand / lower pitch


def test_two_measure_song_auto_pedal_events():
    # Measure 1: [0.0, 2.0), Measure 2: [2.0, 4.0)
    # Quarter = 0.5s (120 bpm)
    # Measure 2 anchor at 2.0s with duration 2.0s => d1 = 0.125s => t_rel = 2.125s
    notes = [
        Note(60, 0.0, 2.0, 80, 0, LEFT),
        Note(64, 2.0, 4.0, 80, 0, LEFT),
    ]
    tracks = [TrackInfo(0, "Track 0", 0, 2, LEFT)]
    song = MidiSong(notes, tracks, 4.0, [0.0, 2.0, 4.0], [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0])
    song.quarter_times = [(0.0, 0.5)]

    events, spans = auto_pedal.build(song, gap_s=0.03)
    assert spans == [(0.0, 2.125), (2.155, 4.0)]

    # Expected sequence:
    # 0.0: DOWN (127) for m1
    # 2.125: UP (0) for m1 release
    # 2.155: DOWN (127) for m2 press (after 30ms gap)
    # 4.0: UP (0) for m2 release
    assert len(events) == 4
    assert events[0] == (0.0, 64, 127)
    assert events[1] == (2.125, 64, 0)
    assert events[2] == (2.155, 64, 127)
    assert events[3] == (4.0, 64, 0)


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
    assert spans == [(1.0, 3.125), (3.155, 5.0)]
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
    # Span 1 covers Measure 1 AND Measure 2 continuously!
    # It releases at Measure 3's anchor + d1 = 4.125s
    assert len(spans) == 2
    assert spans[0] == (0.0, 4.125)
    assert spans[1] == (4.155, 6.0)


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
    assert spans == [(0.0, 8.0)], spans


def test_repeated_chord_each_bar_keeps_one_span():
    notes = [Note(48, b * 2.0, b * 2.0 + 2.0, 80, 0, LEFT) for b in range(4)]
    events, spans = auto_pedal.build(_song(notes, [0.0, 2.0, 4.0, 6.0, 8.0], 8.0), max_hold_bars=99)
    assert spans == [(0.0, 8.0)], spans


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


def test_early_lh_attack_new_chord_anchors_on_that_note():
    # the LH plays the new bass 40 ms before the bar line and holds it; nothing else starts in bar 2
    notes = [Note(48, 0.0, 2.0, 80, 0, LEFT), Note(55, 1.96, 4.0, 80, 0, LEFT)]
    events, spans = auto_pedal.build(_song(notes, [0.0, 2.0, 4.0], 4.0))
    assert len(spans) == 2, spans
    assert spans[0][1] < 2.2, spans           # released right after the early bass note, not later


def test_anchor_is_bass_not_melody_when_rh_starts_first():
    notes = [Note(48, 0.0, 2.0, 80, 0, LEFT), Note(55, 2.0, 4.0, 80, 0, LEFT),
             Note(72, 2.0, 2.1, 80, 0, RIGHT)]      # short melody note on the same beat
    events, spans = auto_pedal.build(_song(notes, [0.0, 2.0, 4.0], 4.0))
    assert spans[0][1] == pytest.approx(2.125)       # d1 from the 2 s bass note, not the 0.1 s melody note


def test_short_bass_note_repress_before_it_ends():
    # new bass is staccato (0.06 s): the pedal must be down again before it ends
    notes = [Note(48, 0.0, 2.0, 80, 0, LEFT), Note(55, 2.0, 2.06, 80, 0, LEFT)]
    events, spans = auto_pedal.build(_song(notes, [0.0, 2.0, 4.0], 4.0), gap_s=0.03)
    assert len(spans) == 2
    assert spans[1][0] < 2.06, spans


def test_different_bass_between_measures():
    # Two measures with different bass notes (C major vs G major)
    notes = [
        Note(48, 0.0, 2.0, 80, 0, LEFT),   # A2 (C major, bass pitch class 0)
        Note(55, 2.0, 4.0, 80, 0, LEFT),   # B3 (G major, bass pitch class 7)
    ]
    tracks = [TrackInfo(0, "Track 0", 0, 2, LEFT)]
    song = MidiSong(notes, tracks, 4.0, [0.0, 2.0, 4.0], [])
    song.quarter_times = [(0.0, 0.5)]

    events, spans = auto_pedal.build(song, gap_s=0.03)
    
    # Should have two separate spans because bass changed from C to G
    assert len(spans) == 2, f"Expected 2 spans for different harmony, got {len(spans)}"
    assert spans[0] == (0.0, 2.125), f"Expected first span (0.0, 2.125), got {spans[0]}"
    assert spans[1] == (2.155, 4.0), f"Expected second span (2.155, 4.0), got {spans[1]}"


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

    events, spans = auto_pedal.build(song, gap_s=0.03)
    
    # The early attack at -0.04 should be detected as a valid attack for measure 1
    # Since there's only 1 measure with LH attack, pedal starts at 0.0
    assert len(spans) == 1, f"Expected 1 span, got {len(spans)}"
    assert spans[0] == (0.0, 3.0), f"Expected span (0.0, 3.0), got {spans[0]}"


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
    
    # Short note at 0.0 ends before the d1 cut point, so pedal still releases at measure end
    assert len(spans) == 1, f"Expected 1 span, got {len(spans)}"
    # Span covers [0.0, 2.0] since d1 was clamped
    assert spans[0] == (0.0, 2.0), f"Expected span (0.0, 2.0), got {spans[0]}"


def test_final_measure_pedal_end():
    # Test that the final measure's pedal ends correctly
    notes = [
        Note(48, 0.0, 2.0, 80, 0, LEFT),
        Note(55, 2.0, 4.0, 80, 0, LEFT),   # a different bass, so there are 2 spans
    ]
    tracks = [TrackInfo(0, "Track 0", 0, 2, LEFT)]
    song = MidiSong(notes, tracks, 4.0, [0.0, 2.0, 4.0], [])
    song.quarter_times = [(0.0, 0.5)]

    events, spans = auto_pedal.build(song, gap_s=0.03)
    
    # The final span should end close to the song duration
    assert len(spans) == 2, f"Expected 2 spans, got {len(spans)}"
    assert spans[1][1] <= 4.3, f"Expected final span to end <= 4.3s, got {spans[1][1]}"


def test_draw_pedal_brackets_helper():
    import pygame
    surf = pygame.Surface((400, 100))
    surf.fill((255, 255, 255))
    spans = [(0.0, 2.0), (2.03, 4.0)]
    x_of = lambda t: 50.0 + t * 50.0            # 0.0->50, 2.0->150, 4.0->250
    draw_pedal_brackets(surf, spans, 0.0, 4.0, x_of, 80.0, 10.0)

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
        Note(48, 0.0, 2.0, 80, 0, LEFT),
        Note(48, 2.0, 4.0, 80, 0, LEFT),
    ]
    tracks = [TrackInfo(0, "Track 0", 0, 2, LEFT)]
    song = MidiSong(notes, tracks, 4.0, [0.0, 2.0, 4.0], [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0])
    song.set_pedal_mode("auto")

    v = StaffView(song)
    surf = pygame.Surface((800, 200))
    v.draw(surf, surf.get_rect(), 0.5)

    # Renders without error
    assert song.pedal_mode == "auto"
    # With same harmony (both notes pitch 48, bass PC = 0), pedal is held continuously
    # across the measure boundary, so only 1 span
    assert len(song.pedal_spans) == 1
    assert song.pedal_spans[0] == (0.0, 4.0)


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
    assert st.pedal.label == "Auto by measure"

    st.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=st.pedal.rect.center))
    assert pianist.app_setting("pedal_mode") == "score"
