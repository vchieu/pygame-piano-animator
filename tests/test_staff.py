"""Unit tests for staff.py: pitch mapping, system paging, cursor playback, and Visualizer integration."""
import os
import pygame
import pytest

from conftest import midi_path
import main
from midi_loader import LEFT, RIGHT, Note, load_midi
import pianist
import staff
from staff import (BASS_MID_STEP, CLEF_BASS, CLEF_TREBLE, DOTTED_EIGHTH,
                   DOTTED_HALF, DOTTED_QUARTER, EIGHTH, HALF, MARGIN_LEFT,
                   MARGIN_RIGHT, MIDDLE_C_STEP, QUARTER, SIXTEENTH,
                   TREBLE_MID_STEP, WHOLE, StaffSystem, StaffView,
                   build_systems, clef_for_note, diatonic_step,
                   get_beat_duration, note_value, note_y)


def test_pitch_diatonic_mapping_and_lines():
    # Treble clef 5 lines: E4 (64), G4 (67), B4 (71), D5 (74), F5 (77)
    treble_lines = [64, 67, 71, 74, 77]
    for p in treble_lines:
        step, sharp = diatonic_step(p)
        assert sharp == 0, f"Pitch {p} should be natural"
        assert step % 2 == 0, f"Pitch {p} step {step} should be an even number (staff line)"
    assert diatonic_step(64)[0] == 30  # E4
    assert diatonic_step(67)[0] == 32  # G4
    assert diatonic_step(71)[0] == 34  # B4 (middle line)
    assert diatonic_step(74)[0] == 36  # D5
    assert diatonic_step(77)[0] == 38  # F5

    # Bass clef 5 lines: G2 (43), B2 (47), D3 (50), F3 (53), A3 (57)
    bass_lines = [43, 47, 50, 53, 57]
    for p in bass_lines:
        step, sharp = diatonic_step(p)
        assert sharp == 0, f"Pitch {p} should be natural"
        assert step % 2 == 0, f"Pitch {p} step {step} should be an even number (staff line)"
    assert diatonic_step(43)[0] == 18  # G2
    assert diatonic_step(47)[0] == 20  # B2
    assert diatonic_step(50)[0] == 22  # D3 (middle line)
    assert diatonic_step(53)[0] == 24  # F3
    assert diatonic_step(57)[0] == 26  # A3

    # Middle C (60)
    c4_step, c4_sharp = diatonic_step(60)
    assert c4_step == MIDDLE_C_STEP == 28
    assert c4_sharp == 0

    # Spaces: F4 (65), A4 (69), C5 (72), E5 (76)
    for p in [65, 69, 72, 76]:
        step, sharp = diatonic_step(p)
        assert sharp == 0
        assert step % 2 == 1, f"Pitch {p} step {step} should be odd (space)"

    # Accidentals (sharps)
    for p in [61, 63, 66, 68, 70]:  # C#, D#, F#, G#, A#
        step, sharp = diatonic_step(p)
        assert sharp == 1, f"Pitch {p} should be sharp"


def test_note_y_calculation():
    s = 7.0
    treble_mid_y = 50.0
    bass_mid_y = 92.0

    # Higher pitch -> smaller y (higher on the staff)
    y_e4 = note_y(30, CLEF_TREBLE, treble_mid_y, bass_mid_y, s)
    y_b4 = note_y(34, CLEF_TREBLE, treble_mid_y, bass_mid_y, s)
    y_f5 = note_y(38, CLEF_TREBLE, treble_mid_y, bass_mid_y, s)
    assert y_f5 < y_b4 < y_e4
    assert y_b4 == treble_mid_y
    assert y_e4 - y_f5 == 4.0 * s  # 4 spaces between bottom and top lines


def test_clef_for_note():
    n_right_high = Note(pitch=72, start=0, end=1, velocity=80, track=0, hand=RIGHT)
    n_right_low = Note(pitch=50, start=0, end=1, velocity=80, track=0, hand=RIGHT)
    n_left_low = Note(pitch=48, start=0, end=1, velocity=80, track=0, hand=LEFT)
    n_left_high = Note(pitch=72, start=0, end=1, velocity=80, track=0, hand=LEFT)

    assert clef_for_note(n_right_high) == CLEF_TREBLE
    assert clef_for_note(n_right_low) == CLEF_BASS
    assert clef_for_note(n_left_low) == CLEF_BASS
    assert clef_for_note(n_left_high) == CLEF_TREBLE


def test_system_slicing_and_measures():
    song = load_midi(midi_path("demo_song.mid"))
    systems = build_systems(song)
    assert len(systems) >= 3

    # Every system has non-empty measures
    for sys in systems:
        assert len(sys.measures) >= 1
        assert sys.duration > 0
        assert sys.start_t < sys.end_t

    # Systems are strictly consecutive
    for i in range(len(systems) - 1):
        assert abs(systems[i].end_t - systems[i + 1].start_t) < 1e-4

    # All notes are assigned to systems without lost notes
    total_sys_notes = sum(len(sys.notes) for sys in systems)
    assert total_sys_notes == len(song.notes)


def test_empty_song_and_synthetic_systems():
    # Empty / None
    assert build_systems(None) == []

    class DummySong:
        duration = 15.0
        bar_times = []
        notes = []

    dummy = DummySong()
    systems = build_systems(dummy)
    assert len(systems) >= 1
    assert systems[0].start_t == 0.0
    assert systems[-1].end_t >= 15.0


def test_cursor_progression_and_paging():
    song = load_midi(midi_path("demo_song.mid"))
    sv = StaffView(song)
    rect = pygame.Rect(0, 34, 1280, 140)

    # Lead-in (t < 0): cursor at left margin of system 0
    sys_0 = sv.system_for_time(-1.0)
    assert sys_0.index == 0
    cx_leadin = sv.cursor_x(-1.0, rect)
    assert cx_leadin == rect.x + MARGIN_LEFT

    # At start t = 0.0
    cx_0 = sv.cursor_x(0.0, rect)
    assert cx_0 == rect.x + MARGIN_LEFT

    # As t advances within system 0, cursor moves smoothly to the right
    cx_mid = sv.cursor_x(sys_0.start_t + sys_0.duration * 0.5, rect)
    usable_w = rect.w - MARGIN_LEFT - MARGIN_RIGHT
    expected_mid = rect.x + MARGIN_LEFT + 0.5 * usable_w
    assert abs(cx_mid - expected_mid) < 1e-3

    # Just before the end of system 0
    t_near_end = sys_0.end_t - 0.01
    assert sv.system_for_time(t_near_end).index == 0
    cx_near_end = sv.cursor_x(t_near_end, rect)
    assert cx_near_end > expected_mid

    # At boundary t = sys_0.end_t: flips to system 1 and cursor resets to the left
    t_flip = sys_0.end_t
    sys_1 = sv.system_for_time(t_flip)
    assert sys_1.index == 1
    cx_flip = sv.cursor_x(t_flip, rect)
    assert abs(cx_flip - (rect.x + MARGIN_LEFT)) < 1e-3

    # Seeking back returns to system 0
    sys_seek = sv.system_for_time(2.0)
    assert sys_seek.index == 0
    cx_seek = sv.cursor_x(2.0, rect)
    assert rect.x + MARGIN_LEFT < cx_seek < rect.right - MARGIN_RIGHT


def test_staff_view_draw(screen):
    song = load_midi(midi_path("demo_song.mid"))
    sv = StaffView(song)
    rect = pygame.Rect(0, 34, screen.get_width(), 140)

    # Drawing at different playback times and opacities
    sv.draw(screen, rect, 0.0, opacity=1.0)
    sv.draw(screen, rect, 5.0, active_notes=[song.notes[0]], opacity=0.85)
    sv.draw(screen, rect, 15.0, opacity=0.60)
    sv.draw(screen, rect, 15.0, opacity=0.0)

    # Drawing with empty song
    sv_empty = StaffView(None)
    sv_empty.draw(screen, rect, 0.0)


def test_note_value_classification():
    beat_len = 0.5  # 120 BPM
    assert note_value(2.0, beat_len) == WHOLE           # 4 beats
    assert note_value(1.5, beat_len) == DOTTED_HALF     # 3 beats
    assert note_value(1.0, beat_len) == HALF            # 2 beats
    assert note_value(0.75, beat_len) == DOTTED_QUARTER # 1.5 beats
    assert note_value(0.5, beat_len) == QUARTER         # 1 beat
    assert note_value(0.3, beat_len) == DOTTED_EIGHTH   # 0.6 beats
    assert note_value(0.25, beat_len) == EIGHTH         # 0.5 beats
    assert note_value(0.125, beat_len) == SIXTEENTH     # 0.25 beats


def test_get_beat_duration_helper():
    class MockSong:
        beat_times = [0.0, 0.5, 1.0, 1.5, 2.0]
        bar_times = [0.0, 2.0]

    song = MockSong()
    assert abs(get_beat_duration(song, 0.25) - 0.5) < 1e-4
    assert get_beat_duration(None) == 0.5


def test_visualizer_layout_and_hotkey(screen):
    app = main.App(screen, sound=False)
    app.play(midi_path("demo_song.mid"))
    vis = app.mode
    assert isinstance(vis, main.Visualizer)

    # Initial state: staff enabled
    assert vis.show_staff is True
    assert vis.staff_rect.h >= 120
    assert vis.fall_rect.top == vis.staff_rect.bottom

    # Render initial frame with staff
    vis.render()

    # Toggle off with 'S' key
    app.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_s, mod=0))
    assert vis.show_staff is False
    assert vis.staff_rect.h == 0
    assert vis.fall_rect.top == vis.bar_rect.bottom + (main.WAVE_H if vis.audio else 0)

    vis.render()

    # Toggle on with 'S' key
    app.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_s, mod=0))
    assert vis.show_staff is True
    assert vis.staff_rect.h >= 120
    assert vis.fall_rect.top == vis.staff_rect.bottom

    # Shift + S cycles staff opacity [1.0 -> 0.85 -> 0.60 -> 1.0]
    vis.staff_opacity = 1.0
    app.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_s, mod=pygame.KMOD_SHIFT))
    assert abs(vis.staff_opacity - 0.85) < 1e-3

    app.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_s, mod=pygame.KMOD_SHIFT))
    assert abs(vis.staff_opacity - 0.60) < 1e-3

    app.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_s, mod=pygame.KMOD_SHIFT))
    assert abs(vis.staff_opacity - 1.0) < 1e-3

    # Key controls list includes 'S'
    controls = dict(vis._key_controls())
    assert "S" in controls
    assert "sheet music" in controls["S"].lower()


def test_note_clashing_offset():
    # Two notes at same time t=0.0 in same clef with adjacent pitches (C4=60, D4=62 -> steps 28 and 29)
    n1 = Note(pitch=60, start=0.0, end=1.0, velocity=80, track=0, hand=RIGHT)
    n2 = Note(pitch=62, start=0.01, end=1.0, velocity=80, track=0, hand=RIGHT)  # |start diff| < 0.03
    s = 7.0
    usable_w = 1000.0
    margin_left = 85.0

    info = staff.compute_notes_info([n1, n2], 0.0, 5.0, usable_w, margin_left, s)
    x1 = info[id(n1)]["x"]
    x2 = info[id(n2)]["x"]
    nw = round(s * 0.75)
    # n2 should be offset to the right relative to n1
    assert x2 >= x1 + 1.8 * nw


def test_octave_transposition_8va_8vb_15ma_15mb():
    n_high_8va = Note(pitch=81, start=0.0, end=1.0, velocity=80, track=0, hand=RIGHT)   # A5 (pitch 81)
    n_high_15ma = Note(pitch=93, start=0.0, end=1.0, velocity=80, track=0, hand=RIGHT)  # A6 (pitch 93)
    n_low_8vb = Note(pitch=43, start=0.0, end=1.0, velocity=80, track=0, hand=LEFT)    # G2 (pitch 43)
    n_low_15mb = Note(pitch=31, start=0.0, end=1.0, velocity=80, track=0, hand=LEFT)   # G1 (pitch 31)
    n_normal = Note(pitch=60, start=0.0, end=1.0, velocity=80, track=0, hand=RIGHT)   # C4

    step_draw, shift, label = staff.get_octave_transposition(n_high_8va, CLEF_TREBLE)
    assert shift == -1 and label == "8va" and step_draw == staff.diatonic_step(81)[0] - 7

    step_draw, shift, label = staff.get_octave_transposition(n_high_15ma, CLEF_TREBLE)
    assert shift == -2 and label == "15ma" and step_draw == staff.diatonic_step(93)[0] - 14

    step_draw, shift, label = staff.get_octave_transposition(n_low_8vb, CLEF_BASS)
    assert shift == 1 and label == "8vb" and step_draw == staff.diatonic_step(43)[0] + 7

    step_draw, shift, label = staff.get_octave_transposition(n_low_15mb, CLEF_BASS)
    assert shift == 2 and label == "15mb" and step_draw == staff.diatonic_step(31)[0] + 14

    step_draw, shift, label = staff.get_octave_transposition(n_normal, CLEF_TREBLE)
    assert shift == 0 and label == "" and step_draw == staff.diatonic_step(60)[0]


def test_chord_notes_line_up_vertically():
    # A C-E-G-C triad in the treble: no neighbour is closer than a third, so no two
    # noteheads clash and the whole chord stays on one vertical line (not a staircase).
    s = 7.0
    chord = [Note(pitch=p, start=0.0, end=1.0, velocity=80, track=0, hand=RIGHT)
             for p in (60, 64, 67, 72)]                # C4 E4 G4 C5 -> steps 28 30 32 35
    info = staff.compute_notes_info(chord, 0.0, 5.0, 1000.0, staff.MARGIN_LEFT, s)

    xs = [info[id(n)]["x"] for n in chord]
    assert len(set(xs)) == 1, f"A chord should share one x, got {xs}"
    assert xs[0] == info[id(chord[0])]["base_x"], "The chord must sit at its own time's x"


def test_only_second_intervals_step_aside():
    # C4 + D4 (a second) + F4: the second can't share a notehead with the note below it,
    # but the sixth above C4 is clear, so only D4 moves.
    s = 7.0
    nw = round(s * 0.75)
    notes = [Note(pitch=p, start=0.0, end=1.0, velocity=80, track=0, hand=RIGHT)
             for p in (60, 62, 65)]
    info = staff.compute_notes_info(notes, 0.0, 5.0, 1000.0, staff.MARGIN_LEFT, s)

    x_c4, x_d4, x_f4 = (info[id(n)]["x"] for n in notes)
    assert x_d4 >= x_c4 + 1.8 * nw, "A second interval steps aside"
    assert x_f4 == x_c4, "The rest of the chord stays on the chord's x"


def test_unison_notes_also_step_aside():
    s = 7.0
    nw = round(s * 0.75)
    n1 = Note(pitch=60, start=0.0, end=1.0, velocity=80, track=0, hand=RIGHT)
    n2 = Note(pitch=60, start=0.01, end=1.0, velocity=80, track=1, hand=RIGHT)
    info = staff.compute_notes_info([n1, n2], 0.0, 5.0, 1000.0, staff.MARGIN_LEFT, s)
    assert info[id(n2)]["x"] >= info[id(n1)]["x"] + 1.8 * nw


def test_time_sequential_notes_keep_minimum_spacing():
    # A note right on the heels of the previous one, and a sharp of its own on the left:
    # the two must not run into each other.
    s = 7.0
    nw = round(s * 0.75)
    n1 = Note(pitch=60, start=0.0, end=0.4, velocity=80, track=0, hand=RIGHT)
    n2 = Note(pitch=61, start=0.05, end=0.45, velocity=80, track=0, hand=RIGHT)   # C#4
    info = staff.compute_notes_info([n1, n2], 0.0, 5.0, 1000.0, staff.MARGIN_LEFT, s)

    x1, x2 = info[id(n1)]["x"], info[id(n2)]["x"]
    assert x2 > x1, "Notes too close in time are spread apart"
    # the second note's sharp sits 0.8 * s to the left of its head: it must clear the first
    assert x2 - 0.8 * s - nw > x1 + nw

    # Notes with plenty of room between them keep their time-proportional x
    far1 = Note(pitch=60, start=0.0, end=0.4, velocity=80, track=0, hand=RIGHT)
    far2 = Note(pitch=60, start=2.0, end=2.4, velocity=80, track=0, hand=RIGHT)
    far = staff.compute_notes_info([far1, far2], 0.0, 5.0, 1000.0, staff.MARGIN_LEFT, s)
    assert far[id(far2)]["x"] == far[id(far2)]["base_x"]


def test_notes_never_past_the_system_edge():
    s = 7.0
    notes = [Note(pitch=p, start=i * 0.02, end=i * 0.02 + 0.2, velocity=80, track=0, hand=RIGHT)
             for i, p in enumerate([60, 62, 64, 61, 65, 67, 69, 71, 72, 74])]
    margin_left, usable_w = staff.MARGIN_LEFT, 400.0
    info = staff.compute_notes_info(notes, 0.0, 5.0, usable_w, margin_left, s)
    assert all(info[id(n)]["x"] <= margin_left + usable_w + 1e-6 for n in notes)


def test_staff_view_zoom_settings():
    song = load_midi(midi_path("demo_song.mid"))

    # target_dur: how many seconds of music share one system
    close = build_systems(song, 4.0)       # zoomed in: fewer seconds per system
    wide = build_systems(song, 20.0)       # zoomed out: more
    assert len(close) > len(wide)
    assert close[0].duration < wide[0].duration
    # the ends of the song still meet, whichever way it's zoomed
    assert close[-1].end_t == wide[-1].end_t == song.duration
    # out-of-range values are brought back inside the supported range
    assert len(build_systems(song, 100.0)) == len(wide)
    assert len(build_systems(song, 0.1)) >= len(close)

    sv = StaffView(song, target_dur=6.0, scale=1.25)
    assert sv.target_dur == 6.0 and sv.scale == 1.25
    assert len(sv.systems) == len(build_systems(song, 6.0))
    assert sv._system_starts == [sys.start_t for sys in sv.systems]

    # set_song takes the zoom too, and keeps it
    sv.set_song(song, target_dur=8.0, scale=0.8)
    assert sv.target_dur == 8.0 and sv.scale == 0.8
    assert len(sv.systems) == len(build_systems(song, 8.0))

    # nonsense and out-of-range zoom values are clamped
    odd = StaffView(song, target_dur=1000.0, scale=99.0)
    assert odd.target_dur == staff.MAX_TARGET_DUR and odd.scale == staff.MAX_SCALE
    low = StaffView(song, target_dur=0.0, scale=0.0)
    assert low.target_dur == staff.MIN_TARGET_DUR and low.scale == staff.MIN_SCALE

    # the cursor still follows the systems, at any zoom
    rect = pygame.Rect(0, 34, 1280, 140)
    usable_w = rect.w - MARGIN_LEFT - MARGIN_RIGHT
    sys0 = sv.system_for_time(0.0)
    for frac in (0.0, 0.25, 0.5, 0.75):
        got = sv.cursor_x(sys0.start_t + sys0.duration * frac, rect)
        assert abs(got - (rect.x + MARGIN_LEFT + frac * usable_w)) < 1e-3


def test_staff_view_draws_at_every_zoom(screen):
    song = load_midi(midi_path("demo_song.mid"))
    rect = pygame.Rect(0, 34, screen.get_width(), 140)
    for target_dur in (3.0, 10.0, 20.0):
        for scale in (staff.MIN_SCALE, 1.0, staff.MAX_SCALE):
            sv = StaffView(song, target_dur=target_dur, scale=scale)
            sv.draw(screen, rect, 3.0, active_notes=[song.notes[0]], opacity=0.9)


def test_visualizer_staff_zoom_shortcuts_and_wheel(screen):
    app = main.App(screen, sound=False)
    app.play(midi_path("demo_song.mid"))
    vis = app.mode

    # starts at the defaults, inside the supported range
    assert 3.0 <= vis.staff_target_dur <= 20.0
    assert 0.6 <= vis.staff_scale <= 1.5
    assert vis.staff_view.target_dur == vis.staff_target_dur
    assert vis.staff_view.scale == vis.staff_scale

    # ] zooms in (fewer seconds per system), [ zooms out again
    start_dur = vis.staff_target_dur
    app.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RIGHTBRACKET, mod=0))
    assert vis.staff_target_dur == start_dur - 1.0
    assert vis.staff_view.target_dur == vis.staff_target_dur
    app.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_LEFTBRACKET, mod=0))
    assert vis.staff_target_dur == start_dur

    # clamped at both ends
    vis.set_staff_target_dur(staff.MIN_TARGET_DUR)
    app.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RIGHTBRACKET, mod=0))
    assert vis.staff_target_dur == staff.MIN_TARGET_DUR
    vis.set_staff_target_dur(999.0)
    assert vis.staff_target_dur == staff.MAX_TARGET_DUR
    vis.set_staff_target_dur(-5.0)
    assert vis.staff_target_dur == staff.MIN_TARGET_DUR
    vis.set_staff_target_dur(10.0)
    vis.set_staff_scale(9.0)
    assert vis.staff_scale == staff.MAX_SCALE
    vis.set_staff_scale(0.1)
    assert vis.staff_scale == staff.MIN_SCALE
    vis.set_staff_scale(1.0)

    # with Shift held the staff's own size is scaled instead of its spacing
    vis.set_staff_scale(1.0)
    app.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RIGHTBRACKET, mod=pygame.KMOD_SHIFT))
    assert vis.staff_scale > 1.0 and vis.staff_view.scale == vis.staff_scale
    app.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_LEFTBRACKET, mod=pygame.KMOD_SHIFT))
    assert abs(vis.staff_scale - 1.0) < 1e-6

    vis.render()

    # Ctrl + wheel over the staff: how much music a system holds
    pygame.mouse.set_pos(vis.staff_rect.center)
    vis.set_staff_target_dur(10.0)
    app.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=-1, mod=pygame.KMOD_CTRL))
    assert vis.staff_target_dur == 9.0
    app.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=1, mod=pygame.KMOD_CTRL))
    assert vis.staff_target_dur == 10.0

    # Shift + wheel over the staff: the staff's size
    vis.set_staff_scale(1.0)
    app.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=-1, mod=pygame.KMOD_SHIFT))
    assert vis.staff_scale > 1.0
    app.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=1, mod=pygame.KMOD_SHIFT))
    assert abs(vis.staff_scale - 1.0) < 1e-6

    # away from the staff the wheel is left to everything else
    pygame.mouse.set_pos((vis.staff_rect.centerx, vis.fall_rect.centery))
    before = (vis.staff_target_dur, vis.staff_scale)
    app.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=-1, mod=pygame.KMOD_CTRL))
    assert (vis.staff_target_dur, vis.staff_scale) == before

    # both zoom levels are remembered
    vis.set_staff_target_dur(7.0)
    vis.set_staff_scale(1.2)
    assert pianist.app_setting("staff_target_dur") == 7.0
    assert abs(pianist.app_setting("staff_scale") - 1.2) < 1e-6

    # and the shortcut list mentions them
    controls = dict(vis._key_controls())
    assert any("zoom" in d.lower() for d in controls.values())


def test_staff_height_dragging_and_clamping(screen):
    app = main.App(screen, sound=False)
    app.play(midi_path("demo_song.mid"))
    vis = app.mode

    # Default initial staff_height
    assert 90 <= vis.staff_height <= 280

    # Test MOUSEBUTTONDOWN at bottom edge of staff_rect
    bottom_y = vis.staff_rect.bottom
    app.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=(200, bottom_y)))
    assert vis.dragging_staff_height is True

    # Test MOUSEMOTION to drag height larger
    app.handle_event(pygame.event.Event(pygame.MOUSEMOTION, pos=(200, vis.staff_rect.top + 200)))
    assert vis.staff_height == 200.0
    assert vis.staff_rect.h == 200

    # Test clamping when dragging too large
    app.handle_event(pygame.event.Event(pygame.MOUSEMOTION, pos=(200, vis.staff_rect.top + 500)))
    assert vis.staff_height == 280.0
    assert vis.staff_rect.h == 280

    # Test clamping when dragging too small
    app.handle_event(pygame.event.Event(pygame.MOUSEMOTION, pos=(200, vis.staff_rect.top + 30)))
    assert vis.staff_height == 90.0
    assert vis.staff_rect.h == 90

    # MOUSEBUTTONUP resets dragging state
    app.handle_event(pygame.event.Event(pygame.MOUSEBUTTONUP, button=1, pos=(200, 100)))
    assert vis.dragging_staff_height is False
