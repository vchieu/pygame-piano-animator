"""Unit tests for staff.py: pitch mapping, system paging, cursor playback, and Visualizer integration."""
import os
import pygame
import pytest

from conftest import midi_path
import main
from midi_loader import LEFT, RIGHT, Note, load_midi
import notation
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
    n_high_8va = Note(pitch=88, start=0.0, end=1.0, velocity=80, track=0, hand=RIGHT)   # E6 (pitch 88)
    n_high_15ma = Note(pitch=100, start=0.0, end=1.0, velocity=80, track=0, hand=RIGHT) # E7 (pitch 100)
    n_low_8vb = Note(pitch=28, start=0.0, end=1.0, velocity=80, track=0, hand=LEFT)    # E1 (pitch 28)
    n_low_15mb = Note(pitch=16, start=0.0, end=1.0, velocity=80, track=0, hand=LEFT)   # E0 (pitch 16)
    n_normal_bass = Note(pitch=43, start=0.0, end=1.0, velocity=80, track=0, hand=LEFT) # G2 (pitch 43)
    n_normal = Note(pitch=60, start=0.0, end=1.0, velocity=80, track=0, hand=RIGHT)   # C4

    step_draw, shift, label = staff.get_octave_transposition(n_high_8va, CLEF_TREBLE)
    assert shift == -1 and label == "8va" and step_draw == staff.diatonic_step(88)[0] - 7

    step_draw, shift, label = staff.get_octave_transposition(n_high_15ma, CLEF_TREBLE)
    assert shift == -2 and label == "15ma" and step_draw == staff.diatonic_step(100)[0] - 14

    step_draw, shift, label = staff.get_octave_transposition(n_low_8vb, CLEF_BASS)
    assert shift == 1 and label == "8vb" and step_draw == staff.diatonic_step(28)[0] + 7

    step_draw, shift, label = staff.get_octave_transposition(n_low_15mb, CLEF_BASS)
    assert shift == 2 and label == "15mb" and step_draw == staff.diatonic_step(16)[0] + 14

    # G2 (pitch 43) is comfortably on 2 ledger lines below bass clef: no 8vb needed
    step_draw, shift, label = staff.get_octave_transposition(n_normal_bass, CLEF_BASS)
    assert shift == 0 and label == "" and step_draw == staff.diatonic_step(43)[0]

    step_draw, shift, label = staff.get_octave_transposition(n_normal, CLEF_TREBLE)
    assert shift == 0 and label == "" and step_draw == staff.diatonic_step(60)[0]


def test_octave_chord_does_not_collapse_in_cluster():
    # An octave chord in bass clef: C1 (pitch 24, 8vb threshold) + C2 (pitch 36).
    # Transposing C1 up 8vb would move it to step 14 (C2), collapsing the chord into unison.
    # compute_notes_info revokes C1's 8vb so the octave retains its vertical span.
    s = 7.0
    c1 = Note(pitch=24, start=0.0, end=1.0, velocity=80, track=0, hand=LEFT)
    c2 = Note(pitch=36, start=0.0, end=1.0, velocity=80, track=0, hand=LEFT)
    info = staff.compute_notes_info([c1, c2], 0.0, 5.0, 1000.0, staff.MARGIN_LEFT, s)

    i1, i2 = info[id(c1)], info[id(c2)]
    assert i1["step_draw"] != i2["step_draw"], "Octave chord notes must not collapse onto the same step"
    assert i1["x"] == i2["x"], "Octave chord notes stay on the same x line"
    assert i1["octave_shift"] == 0 and i1["label"] == ""


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
    # A note a real step along in the beat from the previous one, with a sharp of its own on
    # the left: the two must not run into each other.
    s = 7.0
    nw = round(s * 0.75)
    beat = 0.5
    n1 = Note(pitch=60, start=0.0, end=0.4, velocity=80, track=0, hand=RIGHT)
    n2 = Note(pitch=61, start=0.3, end=0.7, velocity=80, track=0, hand=RIGHT)   # C#4, a sixteenth later
    info = staff.compute_notes_info([n1, n2], 0.0, 5.0, 1000.0, staff.MARGIN_LEFT, s, beat)

    x1, x2 = info[id(n1)]["x"], info[id(n2)]["x"]
    assert x2 > x1, "Notes too close in time are spread apart"
    # the second note's sharp sits 0.8 * s to the left of its head: it must clear the first
    assert x2 - 0.8 * s - nw > x1 + nw

    # Notes with plenty of room between them keep their time-proportional x
    far1 = Note(pitch=60, start=0.0, end=0.4, velocity=80, track=0, hand=RIGHT)
    far2 = Note(pitch=60, start=2.0, end=2.4, velocity=80, track=0, hand=RIGHT)
    far = staff.compute_notes_info([far1, far2], 0.0, 5.0, 1000.0, staff.MARGIN_LEFT, s)
    assert far[id(far2)]["x"] == far[id(far2)]["base_x"]


def test_notes_a_hair_apart_are_one_chord():
    # A chord in a piano roll often comes back with its notes a few tens of milliseconds
    # apart instead of at exactly one moment. That is one chord, so it shares one x
    # (and therefore one stem), not a little cascade of separate notes.
    s = 7.0
    beat = 0.909
    c5 = Note(pitch=72, start=14.545, end=15.907, velocity=80, track=0, hand=RIGHT)
    e5 = Note(pitch=76, start=14.610, end=15.905, velocity=80, track=0, hand=RIGHT)
    b5 = Note(pitch=83, start=14.676, end=15.909, velocity=80, track=0, hand=RIGHT)
    info = staff.compute_notes_info([c5, e5, b5], 14.545, 3.636, 1000.0, staff.MARGIN_LEFT, s, beat)

    xs = [info[id(n)]["x"] for n in (c5, e5, b5)]
    assert len(set(xs)) == 1, f"A staggered chord shares one x, got {xs}"
    assert xs[0] == info[id(c5)]["base_x"], "The chord sits at the moment its first note sounds"

    # the spread that counts as a chord is a fraction of the beat, not a fixed slice of time
    assert staff.chord_spread(beat) < 0.25 * beat
    assert staff.chord_spread(0.25) >= 0.035      # a slow piece still gets a floor


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


def _beam_groups_of(song):
    """Chay group_beams that cua StaffView.draw, lay ra de kiem tra."""
    out = []
    for sys0 in staff.StaffView(song).systems:
        notes = sorted(sys0.notes, key=lambda n: (n.start, n.pitch))
        b_len = staff.get_beat_duration(song)
        clusters = staff.group_chords(notes, b_len)
        info = {}
        for tc in clusters:
            for n in tc:
                c = staff.clef_for_note(n)
                sd, _sh, _lb = staff.get_octave_transposition(n, c)
                info[id(n)] = {"clef": c, "step_draw": sd}
        mo = {}
        for m in sys0.measures:
            for n in sys0.notes:
                if m.start_t <= n.start < m.end_t:
                    mo[id(n)] = m
        out += [(clef, bg, sys0)
                for clef, bg in staff.group_beams(clusters, info,
                                                  lambda t: staff.get_beat_duration(song, t), mo)]
    return out


def test_a_beam_never_reaches_across_unrelated_notes():
    # A beam gathers the short notes of one beat. It must stop at a longer note, a rest, a
    # bar line or the next beat, or it would drag along notes that have nothing to do with
    # each other.
    song = load_midi(midi_path("demo_song.mid"))
    groups = _beam_groups_of(song)
    assert groups, "the test song should contain at least one beam"

    for clef, bg, sys0 in groups:
        assert 2 <= len(bg) <= staff.MAX_BEAM_CHORDS, f"beam of {len(bg)} notes"
        hand = bg[0][0].hand
        for sub in bg:
            assert all(n.hand == hand for n in sub), "a beam crossed from one hand to the other"
            assert all(staff.clef_for_note(n) == clef for n in sub), "a beam crossed between staves"
            assert staff.note_value(sub[0].duration, staff.get_beat_duration(song, sub[0].start)) \
                in (staff.EIGHTH, staff.DOTTED_EIGHTH, staff.SIXTEENTH), "a longer note got beamed"
        # the whole group sits inside a single beat of a single bar
        beats, bars = set(), set()
        for sub in bg:
            n0 = sub[0]
            m = next((m for m in sys0.measures if m.start_t <= n0.start < m.end_t), None)
            assert m is not None, "a beamed note fell outside every bar"
            bars.add(m.index)
            beats.add(int((n0.start - m.start_t) / staff.get_beat_duration(song, n0.start)))
        assert len(bars) == 1, "a beam reached across a bar line"
        assert len(beats) == 1, "a beam reached across a beat"


def test_beam_is_dead_level_and_stems_stretch_to_meet_it(screen):
    # A beam runs dead level, so it is the stems that give way: each one stretches or is
    # trimmed to meet the same bar. Check it on the real drawing, not just on the numbers.
    song = load_midi(midi_path("demo_song.mid"))
    rect = pygame.Rect(0, 0, screen.get_width(), 140)

    levels, stems = [], []
    real_lines, real_line = pygame.draw.lines, pygame.draw.line

    def spy_lines(surf, color, closed, pts, width):
        if pts and len(pts) >= 2:
            ys = [p[1] for p in pts]
            if len(set(ys)) == 1:          # dead level
                levels.append((pts[0][0], pts[-1][0], ys[0]))
        return real_lines(surf, color, closed, pts, width)

    def spy_line(surf, color, a, b, width):
        if abs(a[0] - b[0]) <= 1 and abs(a[1] - b[1]) > 4:
            stems.append((a[0], min(a[1], b[1]), max(a[1], b[1])))
        return real_line(surf, color, a, b, width)

    pygame.draw.lines, pygame.draw.line = spy_lines, spy_line
    try:
        for t in (1.0, 2.5, 4.0):
            screen.fill((255, 255, 255))
            staff.StaffView(song).draw(screen, rect, t, active_notes=song.notes[:5])
    finally:
        pygame.draw.lines, pygame.draw.line = real_lines, real_line

    assert levels, "no level beam was drawn"
    for x0, x1, y in levels:
        assert x1 > x0
        # the stems of this beam all end on it, and none of them stops short
        under = [st for st in stems if x0 - 2 <= st[0] <= x1 + 2 and st[1] <= y <= st[2]]
        assert under, f"beam at y={y} has no stem reaching it"


def test_a_beam_turns_its_stems_over_to_keep_off_the_other_hand():
    # A beam is a horizontal line and the two hands draw at the same time: when the wrong
    # side of one hand's chord is exactly the height the other hand's note is drawn at, the
    # bar lands on it. Here two chords a step apart low on the treble staff - the engraver's
    # stems point up - and another chord's notehead drawn exactly where that bar would go.
    s = 8.0
    nw = round(s * 0.75)
    beam_w = max(2, round(staff.BEAM_WIDTH_SPACES * s))
    gap = round(staff.BEAM_STEP_SPACES * s)
    reach = round(staff.STEM_LEN_SPACES * s)
    y_low, y_high = 100.0, 96.0
    stops = [(200.0, y_high, y_high, False), (240.0, y_low, y_low, False)]
    default_up = staff.stem_up_for({staff.TREBLE_MID_STEP - 2, staff.TREBLE_MID_STEP - 1},
                                   staff.TREBLE_MID_STEP)
    assert default_up is True

    # nobody there: the engraver's direction stands, with the bar where he asked for it
    up, tip = staff.layout_beam(stops, default_up, reach, beam_w, gap, nw, [], set(), s)
    assert up is default_up and tip == round(y_high - reach)

    # the other hand's note, at the height of the bar that direction puts there: the stems
    # turn over and the bar goes with them, below the chords and clear of the note
    heads = [(205.0, 235.0, y_high - reach - 4, y_high - reach + 4, 1)]
    up, tip = staff.layout_beam(stops, default_up, reach, beam_w, gap, nw, heads, set(), s)
    assert up is False
    assert tip >= round(y_low + reach)
    assert staff.ink_overlap(200, 240, tip - beam_w / 2, tip + beam_w / 2, heads, set()) == 0

    # a bar that has to leave the staff panel to clear a note is not worth it
    tight = [(205.0, 235.0, y_low + reach + 20, y_low + reach + 24, 1)]
    up, tip = staff.layout_beam(stops, default_up, reach, beam_w, gap, nw, tight, set(), s,
                                 panel=(0, 140))
    assert up is default_up, "the stems turned over into a bar outside the staff panel"
    assert tip == round(y_high - reach)


def test_a_beam_never_hangs_off_the_staff_panel(screen):
    # The same on a real drawing: a run of the left hand's eighths with the right hand
    # holding a note at the height their bar would take. Nothing may be pushed outside the
    # staff panel to dodge it - on a small panel that pushes the bar off the screen.
    notes = [_note(p, 0.25 * i, LEFT, dur=0.22) for i, p in enumerate((69, 71, 69, 71))]
    notes.append(_note(81, 0.55, RIGHT, dur=0.45))       # A5: two ledger lines above the treble
    song = _BracketSong(notes)

    for height in (90, 140, 280):
        rect = pygame.Rect(0, 0, 1200, height)
        bars = []
        real_lines = pygame.draw.lines

        def spy(surf, color, closed, pts, width):
            if pts and len(pts) >= 2 and len(set(p[1] for p in pts)) == 1:
                bars.append((pts[0][1], width))
            return real_lines(surf, color, closed, pts, width)

        pygame.draw.lines = spy
        try:
            staff.StaffView(song).draw(screen, rect, 1.0)
        finally:
            pygame.draw.lines = real_lines

        assert bars, f"the test song should draw a beam at {height}px"
        for y, w in bars:
            assert rect.top <= y - w / 2 and y + w / 2 <= rect.bottom, (
                f"a bar at y={y} hangs off the {height}px staff panel")


def test_a_stem_turns_over_rather_than_run_through_another_note():
    # place_stem scores one stem's ink - and its flags - against the noteheads that are
    # not its own chord's, so the two directions can be compared. A stem up from a note
    # below the middle line lands on a note a fourth above it; turned down it does not.
    s = 7.0
    stem_len = round(staff.STEM_LEN_SPACES * s)
    x, y = 200.0, 100.0
    y_f5 = y - 7 * s * 0.5                               # a fifth up: exactly a stem's reach
    heads = [(x - 30, x + 30, y_f5 - 5, y_f5 + 5, 1)]    # another chord's note, up there

    assert staff.place_stem(True, x, y, y, stem_len, 0, s, heads, set()) > 0
    assert staff.place_stem(False, x, y, y, stem_len, 0, s, heads, set()) == 0
    # the chord's own notehead is never in the way, either way
    own = [(x - 5, x + 5, y - 5, y + 5, 2)]
    assert staff.place_stem(True, x, y, y, stem_len, 0, s, own, {2}) == 0


def test_a_beamed_notehead_never_sits_on_the_beam(screen):
    # A beamed note's stem may not be so short that the notehead runs into the bars. The
    # bars are a stack (a run of sixteenths carries two), so a sixteenth run needs a
    # longer stem than an eighth run. Check it against what is really drawn.
    rect = pygame.Rect(0, 0, screen.get_width(), 140)

    beams, stems, bars_seen = [], [], set()
    rl, rline = pygame.draw.lines, pygame.draw.line

    def spy_lines(s, c, cl, pts, w):
        if pts and len(pts) >= 2 and len(set(p[1] for p in pts)) == 1:
            beams.append((pts[0][0], pts[-1][0], pts[0][1], w))
        return rl(s, c, cl, pts, w)

    def spy_line(s, c, a, b, w):
        if abs(a[0] - b[0]) <= 1 and abs(a[1] - b[1]) > 4:
            stems.append((a[0], min(a[1], b[1]), max(a[1], b[1])))
        return rline(s, c, a, b, w)

    pygame.draw.lines, pygame.draw.line = spy_lines, spy_line
    try:
        # 50-nam-ve-sau has sixteenth runs, so the two-bar case is really exercised
        for name in ("demo_song.mid", "50-nam-ve-sau.mid"):
            song = load_midi(midi_path(name))
            for t in (1.0, 3.0, 6.0, 9.0, 12.0, 15.0):
                screen.fill((255, 255, 255))
                beams.clear(); stems.clear()
                staff.StaffView(song).draw(screen, rect, t, active_notes=song.notes[:5])
                for x0, x1, y, w in beams:
                    mine = [st for st in stems
                            if x0 - 6 <= st[0] <= x1 + 6 and min(st[1], st[2]) <= y <= max(st[1], st[2])]
                    if not mine:
                        continue
                    space = w / staff.BEAM_WIDTH_SPACES      # a bar is half a space thick
                    bars = len({b[2] for b in beams if b[0] <= x1 and b[1] >= x0
                                and abs(b[2] - y) <= staff.BEAM_STEP_SPACES * space})
                    bars_seen.add(bars)
                    need = staff.min_stem_spaces(bars) * space
                    for x, y0, y1 in mine:
                        assert y1 - y0 >= need - 1, (
                            f"{name}: stem at x={x} is {y1 - y0:.0f}px, but {bars} beam bar(s) "
                            f"need {need:.0f}px or the notehead would sit on them")
    finally:
        pygame.draw.lines, pygame.draw.line = rl, rline

    assert bars_seen, "no beam was drawn"
    assert 2 in bars_seen, f"the test never drew a two-bar beam (saw {sorted(bars_seen)})"


def test_stem_direction_follows_the_engraver():
    # MuseScore's ChordLayout::computeAutoStemDirection: counting lines from the top
    # (line 0 is the top line, the middle line is 2), the two outer notes decide. Below the
    # middle line the stems go up, above it they go down, and when the two cancel out you
    # look one note further in.
    mid = staff.TREBLE_MID_STEP          # B4, the treble middle line
    top, bottom = mid + 4, mid - 4       # F5 on the top line, E4 on the bottom one

    assert staff.stem_up_for([mid - 3], mid) is True       # low note -> stem up
    assert staff.stem_up_for([mid + 3], mid) is False      # high note -> stem down
    assert staff.stem_up_for([mid], mid) is False          # on the line -> down
    assert staff.stem_up_for([bottom, mid - 2], mid) is True
    assert staff.stem_up_for([mid + 2, top], mid) is False

    # a chord whose two outer notes cancel falls through to the next pair in
    assert staff.stem_up_for([mid - 2, mid + 1], mid) is True
    assert staff.stem_up_for([mid - 1, mid + 2], mid) is False

    # the order the notes come in must not matter
    steps = [bottom, mid - 2, mid + 2]
    assert staff.stem_up_for(steps, mid) == staff.stem_up_for(list(reversed(steps)), mid)

    # and it is symmetric about the middle line: swap the clefs' middle lines
    bmid = staff.BASS_MID_STEP
    assert staff.stem_up_for([bmid - 3], bmid) is True
    assert staff.stem_up_for([bmid + 3], bmid) is False


def test_beam_and_stem_constants_match_the_engraver():
    # MuseScore engraves with these (src/engraving/style/styledef.cpp, stemlayout.cpp):
    # a 3.5-space stem, a half-space thick bar, and bars 0.75 space apart.
    assert staff.STEM_LEN_SPACES == 3.5
    assert staff.BEAM_WIDTH_SPACES == 0.5
    assert staff.BEAM_STEP_SPACES == 0.75
    # the stem of a beamed note clears the notehead and the whole stack of bars:
    # 1 bar -> 3.25, 2 bars -> 3.75, 3 bars -> 4.25 staff spaces
    assert [round(staff.min_stem_spaces(k), 2) for k in (1, 2, 3)] == [3.25, 3.75, 4.25]
    assert staff.min_stem_spaces(9) == staff.min_stem_spaces(4)


def test_draw_arpeggio_wavy_line_and_chord_stems(screen):
    # Test that draw_arpeggio_wavy_line runs without error
    staff.draw_arpeggio_wavy_line(screen, 100, 50, 120, 7.0, (50, 50, 65))

    # Test staff view draw on a song with chords & fast notes
    song = load_midi(midi_path("demo_song.mid"))
    rect = pygame.Rect(0, 34, screen.get_width(), 140)
    sv = staff.StaffView(song)
    sv.draw(screen, rect, 1.0, active_notes=song.notes[:3])


# --------------------------------------------------------------------------- #
# The clefs, the time signature and the octave brackets
# --------------------------------------------------------------------------- #
@pytest.fixture
def no_font():
    """The music font isn't there (or can't be opened) - a stripped build."""
    notation.clear_cache()
    notation._font = False
    notation._quit_hooked = True
    yield
    notation.clear_cache()


def _draw_staff_to(song, rect, t=1.0):
    """Draw one staff panel onto a surface of its own and hand back the pixels."""
    out = pygame.Surface(rect.size)
    out.fill((0, 0, 0))
    staff.StaffView(song).draw(out, rect, t)
    return out


def test_the_clefs_and_the_time_signature_are_drawn(screen):
    song = load_midi(midi_path("demo_song.mid"))
    rect = pygame.Rect(0, 0, 1200, 140)
    out = _draw_staff_to(song, rect)

    s = max(4.0, min(12.0, (rect.h - 26) / 14.0))
    center_y = rect.centery
    treble_mid_y = center_y - round(3.0 * s)
    bass_mid_y = center_y + round(3.0 * s)
    left_x = rect.x + 18

    def ink(box):
        return sum(1 for x in range(box.left, box.right) for y in range(box.top, box.bottom)
                   if out.get_at((x, y))[:3] != staff.STAFF_BG)

    # each clef is drawn on the line it names: G4 (mid + s) for the treble one, F3 (mid - s)
    # for the bass one, and its ink hangs off that line by what Leland's glyph does.
    for mid_y, name in ((treble_mid_y, "gClef"), (bass_mid_y, "fClef")):
        ref = mid_y + s if name == "gClef" else mid_y - s
        _w, top, bottom = notation.CLEF_BOX[name]
        box = pygame.Rect(round(left_x + 8), round(ref - top * s),
                          round(2.7 * s), round((top - bottom) * s))
        assert ink(box) > 0.2 * box.w * box.h, f"no {name} around y={mid_y}"

    # the 4/4 column: four digits, one on the line above the middle one and one below, on
    # both staves. It sits clear of the clefs, which reach to 2.66 sp wide.
    ts_x = staff.time_sig_x(left_x, s)
    assert ts_x - notation.TIME_SIG_HALF_W_SP * s > left_x + 8 + staff.clef_ink_w(s) - 1
    for mid_y in (treble_mid_y, bass_mid_y):
        box = pygame.Rect(round(ts_x - 1.1 * s), round(mid_y - 2.1 * s),
                          round(2.2 * s), round(4.2 * s))
        assert ink(box) > 0.05 * box.w * box.h, f"no time signature around y={mid_y}"


def test_the_time_signature_column_fits_between_the_clefs_and_the_first_note(screen):
    # At every staff size the column clears the clefs; the left margin (the first note is
    # at MARGIN_LEFT) is only wide enough for it up to about s = 10, and past that it stays
    # where the old UI-font digits were rather than pushing further into the notes.
    left_x = 18.0
    for s in (4.0, 5.0, 6.0, 8.0, 10.0, 12.0):
        ts_x = staff.time_sig_x(left_x, s)
        half = notation.TIME_SIG_HALF_W_SP * s
        assert ts_x - half > left_x + 8 + staff.clef_ink_w(s) - 1, f"clef runs into the digits at s={s}"
        if s <= 10.0:
            assert ts_x + half <= MARGIN_LEFT, f"the digits reach the first note at s={s}"
    assert staff.time_sig_x(left_x, 12.0) == left_x + round(12.0 * 4.6)


def test_the_staff_falls_back_to_its_own_clefs_and_digits_without_a_font(no_font, screen):
    # A stripped build (no Leland.otf): the staff still draws - through the hand-drawn clefs
    # and the UI font's 4/4 it fell back to.
    song = load_midi(midi_path("demo_song.mid"))
    rect = pygame.Rect(0, 0, 1200, 140)
    out = _draw_staff_to(song, rect)

    s = max(4.0, min(12.0, (rect.h - 26) / 14.0))
    center_y = rect.centery
    left_x = rect.x + 18

    def ink(box):
        return sum(1 for x in range(box.left, box.right) for y in range(box.top, box.bottom)
                   if out.get_at((x, y))[:3] != staff.STAFF_BG)

    for mid_y in (center_y - round(3.0 * s), center_y + round(3.0 * s)):
        box = pygame.Rect(round(left_x + 8), round(mid_y - 2.9 * s), round(2.0 * s), round(5.7 * s))
        assert ink(box) > 0.01 * box.w * box.h, "the hand-drawn clef is missing"
    ts_x = staff.time_sig_x(left_x, s)
    for mid_y in (center_y - round(3.0 * s), center_y + round(3.0 * s)):
        box = pygame.Rect(round(ts_x - 1.2 * s), round(mid_y - 2.0 * s),
                          round(2.4 * s), round(4.0 * s))
        assert ink(box) > 0.02 * box.w * box.h, "the UI font's digits are missing"


class _BracketSong:
    """Just enough song for StaffView to lay out: notes, a duration and bar lines."""

    def __init__(self, notes, duration=10.0, bar=2.0):
        self.notes = notes
        self.duration = duration
        self.bar_times = [i * bar for i in range(int(duration / bar) + 1)]
        self.beat_times = []


def _note(pitch, start, hand, dur=0.4):
    return Note(pitch=pitch, start=start, end=start + dur, velocity=80, track=0, hand=hand)


def test_the_octave_bracket_clears_the_notes_it_covers(screen):
    # get_octave_transposition shifts each note by its own amount, so one group covers a
    # range of drawn heights: a chord of A6 + C7 (both 8va) is drawn at -3 sp and -4 sp from
    # the treble middle line, and A0 + D1 (8vb) at +5 sp and +3.5 sp below the bass one. The
    # bracket's line is worked out from the notes under it, so it clears all of them.
    pitches = ((RIGHT, (100, 104), 0.4),      # E7 then G7  -> 15ma
               (RIGHT, (93, 96), 2.0),        # A6 then C7  -> 8va
               (LEFT, (21, 26), 3.6),         # A0 then D1  -> 8vb
               (LEFT, (12, 16), 5.2))         # C0 then E0  -> 15mb
    notes = [_note(p, t0 + 0.6 * j, hand)
             for hand, ps, t0 in pitches for j, p in enumerate(ps)]
    song = _BracketSong(notes)

    rect = pygame.Rect(0, 0, 1200, 100)
    lines = []
    real_dashed = staff.draw_dashed_line

    def spy(surf, color, start_pos, end_pos, width=1, **kw):
        lines.append((start_pos, end_pos))
        return real_dashed(surf, color, start_pos, end_pos, width=width, **kw)

    staff.draw_dashed_line = spy
    try:
        staff.StaffView(song).draw(screen, rect, 1.0)
    finally:
        staff.draw_dashed_line = real_dashed

    assert len(lines) == 4, f"the test song should draw one bracket per group, got {len(lines)}"

    s = max(4.0, min(12.0, (rect.h - 26) / 14.0))
    center_y = rect.centery
    treble_mid_y = center_y - round(3.0 * s)
    bass_mid_y = center_y + round(3.0 * s)
    sys0 = staff.StaffView(song).systems[0]
    info = staff.compute_notes_info(sys0.notes, sys0.start_t, sys0.duration,
                                    rect.w - MARGIN_LEFT - MARGIN_RIGHT,
                                    MARGIN_LEFT, s, staff.get_beat_duration(song))
    head_h = notation.NOTEHEAD_H_SP * s / 2.0
    nw = round(s * 0.75)
    checked = 0
    for start_pos, end_pos in lines:
        line_y, x0, x1 = start_pos[1], start_pos[0], end_pos[0]
        covered = [n for n in sys0.notes if x0 - nw <= info[id(n)]["x"] <= x1 + nw]
        assert covered, "a bracket was drawn with no notes under it"
        for n in covered:
            i = info[id(n)]
            ny = note_y(i["step_draw"], i["clef"], treble_mid_y, bass_mid_y, s)
            if i["clef"] == CLEF_TREBLE:                 # 8va / 15ma: fully above
                assert line_y < ny - head_h, (
                    f"the bracket at y={line_y} crosses the notehead at y={ny} (pitch {n.pitch})")
            else:                                        # 8vb / 15mb: fully below
                assert line_y > ny + head_h, (
                    f"the bracket at y={line_y} crosses the notehead at y={ny} (pitch {n.pitch})")
            checked += 1
    # one notehead per bracket falls under its line (the first note of a group sits under
    # the label, to the left of the line), so all four groups were really checked
    assert checked >= 4, f"the test never really checked a bracket ({checked} notes)"


class _RecordingSurface(pygame.Surface):
    """A surface that remembers where each image was blitted, so a test can see
    where draw_octave_brackets printed its labels."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.blits = []

    def blit(self, src, dest, *args, **kwargs):
        self.blits.append((src.get_size(), (dest[0], dest[1])))
        return super().blit(src, dest, *args, **kwargs)


def test_an_octave_bracket_label_is_kept_off_other_groups_notes(screen):
    # A label is printed to the left of its own group's first note, so it can land on a
    # notehead belonging to another group - the two 8va groups of this song sit one notehead
    # apart in height. The bracket moves to keep it off, but only while the staff panel has
    # room: a height that clears its own notes wins over one that chases a neighbour, since
    # that is the line's job and pushing further would only land it somewhere worse.
    pitches = ((RIGHT, (100, 104), 0.4),      # E7 then G7  -> 15ma
               (RIGHT, (93, 96), 2.0),        # A6 then C7  -> 8va
               (LEFT, (21, 26), 3.6),         # A0 then D1  -> 8vb
               (LEFT, (12, 16), 5.2))         # C0 then E0  -> 15mb
    notes = [_note(p, t0 + 0.6 * j, hand)
             for hand, ps, t0 in pitches for j, p in enumerate(ps)]
    song = _BracketSong(notes)

    rect = pygame.Rect(0, 0, 1200, 140)       # room in the panel: the labels can move
    surf = _RecordingSurface(rect.size)
    surf.fill(staff.STAFF_BG)

    s = max(4.0, min(12.0, (rect.h - 26) / 14.0))
    center_y = rect.centery
    treble_mid_y = center_y - round(3.0 * s)
    bass_mid_y = center_y + round(3.0 * s)
    head_w = notation.NOTEHEAD_W_SP * s / 2.0
    head_h = notation.NOTEHEAD_H_SP * s / 2.0

    sys0 = staff.StaffView(song).systems[0]
    info = staff.compute_notes_info(sys0.notes, sys0.start_t, sys0.duration,
                                    rect.w - MARGIN_LEFT - MARGIN_RIGHT,
                                    MARGIN_LEFT, s, staff.get_beat_duration(song))
    staff.draw_octave_brackets(surf, info, sys0.notes, treble_mid_y, bass_mid_y, s, rect)

    labels = [b for b in surf.blits if 8 <= b[0][1] <= 30 and b[0][0] >= 10]
    assert len(labels) == 4, f"the four groups should each print a label, got {len(labels)}"

    # Each label's own group: the first note of a group always sits just right of its label
    # (x_min is the group's left edge less the notehead), so only the *other* groups count.
    own = {}
    for n in sys0.notes:
        i = info[id(n)]
        own.setdefault((i["clef"], i["octave_shift"], i["label"]), []).append(n)

    checked = 0
    for (lw, lh), (lx, ly) in labels:
        label_clef = CLEF_TREBLE if ly < center_y else CLEF_BASS
        for key, group in own.items():
            if key[0] != label_clef:
                continue
            for n in group:
                if id(n) == id(min(group, key=lambda m: info[id(m)]["x"])):
                    continue                  # the group's own first note: beside its label
                i = info[id(n)]
                ny = note_y(i["step_draw"], label_clef, treble_mid_y, bass_mid_y, s)
                if (min(lx + lw, i["x"] + head_w) - max(lx, i["x"] - head_w) > 0
                        and min(ly + lh, ny + head_h) - max(ly, ny - head_h) > 0):
                    pytest.fail(f"a {key[2] or 'plain'} label at ({lx},{ly}) is printed on "
                                f"another group's notehead, pitch {n.pitch}")
                checked += 1
    assert checked >= 4, f"the test never really checked a label ({checked} notes)"
