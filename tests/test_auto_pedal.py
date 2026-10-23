"""
tests/test_auto_pedal.py - Unit tests for automatic sustain pedal generation.
"""
import pygame
import pytest

import auto_pedal
import main
import pianist
from midi_loader import LEFT, RIGHT, MidiSong, Note, TrackInfo
from staff import StaffView, draw_pedal_brackets


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
        Note(48, 4.0, 6.0, 80, 0, LEFT),
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


def test_set_pedal_mode_and_raw_controls_preservation():
    notes = [Note(48, 0.0, 2.0, 80, 0, LEFT), Note(48, 2.0, 4.0, 80, 0, LEFT)]
    tracks = [TrackInfo(0, "Track 0", 0, 2, LEFT)]
    raw = [(0.5, 64, 127), (1.5, 64, 0)]
    song = MidiSong(notes, tracks, 4.0, [0.0, 2.0, 4.0], [], controls=raw)

    assert song.pedal_mode == "score"
    assert song.controls == [(0.5, 64, 127), (1.5, 64, 0)]

    song.set_pedal_mode("auto")
    assert song.pedal_mode == "auto"
    assert len(song.controls) == 4
    assert len(song.pedal_spans) == 2
    assert song.raw_controls == raw             # intact

    song.set_pedal_mode("score")
    assert song.pedal_mode == "score"
    assert song.controls == [(0.5, 64, 127), (1.5, 64, 0)]
    assert song.pedal_spans == []


def test_song_with_no_bar_times_handles_auto_mode():
    notes = [Note(60, 0.0, 2.0, 80, 0, RIGHT)]
    tracks = [TrackInfo(0, "Track 0", 0, 1, RIGHT)]
    song = MidiSong(notes, tracks, 2.0, [], [])

    song.set_pedal_mode("auto")
    assert song.pedal_mode == "score"            # Fallback to score
    assert song.controls == []
    assert song.pedal_spans == []


def test_draw_pedal_brackets_helper(screen):
    surf = pygame.Surface((400, 100))
    surf.fill((255, 255, 255))
    spans = [(0.0, 2.0), (2.03, 4.0)]
    x_of = lambda t: 50.0 + t * 50.0            # 0.0->50, 2.0->150, 4.0->250
    draw_pedal_brackets(surf, spans, 0.0, 4.0, x_of, 80.0, 10.0)

    # Check that pixels were drawn near y=80
    dark_pixels = sum(1 for x in range(50, 250) if surf.get_at((x, 80))[:3] != (255, 255, 255))
    assert dark_pixels > 100


def test_staff_view_draw_with_auto_pedal(screen):
    notes = [Note(48, 0.0, 2.0, 80, 0, LEFT), Note(48, 2.0, 4.0, 80, 0, LEFT)]
    tracks = [TrackInfo(0, "Track 0", 0, 2, LEFT)]
    song = MidiSong(notes, tracks, 4.0, [0.0, 2.0, 4.0], [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0])
    song.set_pedal_mode("auto")

    v = StaffView(song)
    surf = pygame.Surface((800, 200))
    v.draw(surf, surf.get_rect(), 0.5)

    # Renders without error, and pedal spans are present
    assert song.pedal_mode == "auto"
    assert len(song.pedal_spans) == 2


def test_settings_screen_pedal_mode_toggle(screen):
    from app_settings import SettingsScreen
    pianist.set_app_setting("pedal_mode", "score")

    app = main.App(screen, sound=False)
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
