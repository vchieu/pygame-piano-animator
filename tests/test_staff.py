"""Unit tests for staff.py: pitch mapping, system paging, cursor playback, and Visualizer integration."""
import os
import pygame
import pytest

from conftest import midi_path
import main
from midi_loader import LEFT, RIGHT, Note, load_midi, MidiSong
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


def test_a_note_crossing_a_bar_line_is_split_and_tied():
    # The real note from 50-nam-ve-sau measure 2: pitch 88 from 3.169 to 4.1717, with the
    # bar line at 3.3803 strictly inside it. An engraver cuts it at the bar and ties the
    # two pieces; left whole, the measure showed a half note reaching 1.87 beats past its
    # own end (the defect this split exists for).
    bar = 3.3803
    n = Note(pitch=88, start=3.169, end=4.1717, velocity=80, track=0, hand=RIGHT, finger=3)
    pieces, ties = staff.split_at_bar_lines([n], [0.0, 1.6901, bar, 5.0704])

    assert len(pieces) == 2
    left, right = pieces
    # the cut lands exactly on the bar time - the right piece opens the next measure
    assert left.start == pytest.approx(3.169) and left.end == pytest.approx(bar)
    assert right.start == pytest.approx(bar) and right.end == pytest.approx(4.1717)
    # nothing else about the note changes: same pitch, same velocity, same hand
    assert right.pitch == left.pitch == 88
    assert right.velocity == left.velocity == 80 and right.hand == left.hand == RIGHT
    # the first piece keeps the file's own fingering; the second is a plain continuation
    assert left.finger == 3 and right.finger is None

    # and the two pieces are tied to one another, left to right
    assert ties == [(left, right)]


def test_a_note_spanning_two_bar_lines_gets_one_piece_per_measure():
    # A note longer than a measure is cut at every bar it crosses, not just the first.
    n = Note(pitch=60, start=0.1, end=2.9, velocity=80, track=0, hand=RIGHT)
    pieces, ties = staff.split_at_bar_lines([n], [0.0, 1.0, 2.0, 3.0])

    assert [(round(p.start, 4), round(p.end, 4)) for p in pieces] == \
        [(0.1, 1.0), (1.0, 2.0), (2.0, 2.9)]
    # each piece tied to the one before it, in order
    assert ties == [(pieces[0], pieces[1]), (pieces[1], pieces[2])]
    # the pieces cover the note's own interval exactly - no time is lost at the joins
    assert sum(p.duration for p in pieces) == pytest.approx(n.duration)


def test_a_note_inside_one_measure_is_left_whole():
    # No bar time strictly inside it, so there is nothing to tie: the note itself comes
    # back, not a copy, and no tie.
    n = Note(pitch=67, start=1.2, end=1.8, velocity=80, track=0, hand=RIGHT)
    pieces, ties = staff.split_at_bar_lines([n], [0.0, 1.0, 2.0])
    assert pieces == [n] and ties == []
    # a note starting exactly on the bar belongs to the measure it opens, uncut
    on_bar = Note(pitch=67, start=2.0, end=2.6, velocity=80, track=0, hand=RIGHT)
    pieces, ties = staff.split_at_bar_lines([on_bar], [0.0, 1.0, 2.0, 3.0])
    assert pieces == [on_bar] and ties == []


def test_no_measure_of_50_nam_ve_sau_overfills_its_time_signature():
    # Every note that crossed a bar line is split, so no measure's notes reach past the
    # measure - measure 2 used to show a half note 1.87 beats into measure 3.
    song = load_midi(midi_path("50-nam-ve-sau.mid"))
    for sys in build_systems(song):
        pieces, _ties = staff.split_at_bar_lines(sys.notes, song.bar_times)
        for m in sys.measures:
            # A piece belongs to the measure it opens. The 1e-9 slack is only for the
            # file's own rounding, where a downbeat lands a hair before the bar time;
            # a piece sitting on a measure's end belongs to the *next* measure, not this
            # one, so the upper bound carries the slack too.
            over = [p for p in pieces
                    if m.start_t - 1e-9 <= p.start < m.end_t - 1e-9
                    and p.end > m.end_t + 1e-6]
            assert not over, f"measure {m.index} shows ink past its end"


def test_the_split_note_is_tied_on_screen(screen, monkeypatch):
    # The tie is drawn, not just recorded: system 3 of 50-nam-ve-sau holds a split note,
    # and rendering it must put ink between the two noteheads it now has - ink that is
    # there only because of the tie.
    song = load_midi(midi_path("50-nam-ve-sau.mid"))
    sv = StaffView(song)
    sys3 = sv.systems[3]
    rect = pygame.Rect(0, 0, 1400, 300)

    with_tie = pygame.Surface(rect.size)
    sv.draw(with_tie, rect, sys3.start_t)

    # Same draw with the tie painter silenced: only the tie's own ink can differ.
    calls = []
    monkeypatch.setattr(notation, "draw_tie", lambda *a, **k: calls.append(a))
    without_tie = pygame.Surface(rect.size)
    sv.draw(without_tie, rect, sys3.start_t)
    monkeypatch.undo()

    assert calls, "system 3 should draw a tie for its split note"
    # at least one tie draws a real crescent whose ink the silent pass lacks
    for (surf, sp, x0, x1, y, up, color) in calls:
        assert x0 < x1
        tie_ink = 0
        for x in range(int(x0) + 1, int(x1)):
            for yy in range(max(0, int(y) - round(6 * sp)), min(rect.h, int(y) + round(6 * sp))):
                if with_tie.get_at((x, yy))[:3] != without_tie.get_at((x, yy))[:3]:
                    tie_ink += 1
        if tie_ink:
            break
    assert tie_ink > 0, "the tie leaves ink between the two tied noteheads"


def test_a_tie_goes_opposite_the_stem(screen, monkeypatch):
    # MuseScore ties a single note on the side away from its chord's stem
    # (SlurTieLayout::computeUp, slurtielayout.cpp:3086: setUp(!chord->up())). Capture the
    # direction each tie is drawn with and check it against the stem the layout chose.
    song = load_midi(midi_path("50-nam-ve-sau.mid"))
    sv = StaffView(song)
    rect = pygame.Rect(0, 0, 1400, 300)

    calls = []
    real = notation.draw_tie

    def record(surf, sp, x0, x1, y, up, color):
        calls.append((x0, x1, y, up))
        return real(surf, sp, x0, x1, y, up, color)

    monkeypatch.setattr(notation, "draw_tie", record)
    for i in range(len(sv.systems)):
        sys = sv.systems[i]
        before = len(calls)
        sv.draw(pygame.Surface(rect.size), rect, sys.start_t)
        # every tie for this system is drawn with a real side (no crash, no None)
        for _x0, _x1, _y, up in calls[before:]:
            assert isinstance(up, bool)

    assert calls, "50-nam-ve-sau draws ties"
    # both directions really occur in the song, so the placement follows the stems
    # rather than being a constant
    assert {c[3] for c in calls} == {True, False}


def test_a_tie_over_a_system_break_reaches_both_margins(screen, monkeypatch):
    # A tie cut by a system's edge is drawn in full on both systems: the earlier one
    # carries it out to the closing bar line, the later one opens it at the staff's
    # left edge. A short stub on either side would read as a broken tie.
    song = load_midi(midi_path("50-nam-ve-sau.mid"))
    sv = StaffView(song)
    rect = pygame.Rect(0, 0, 1400, 300)
    assert len(sv.systems) >= 2

    calls = []
    real = notation.draw_tie

    def record(surf, sp, x0, x1, y, up, color):
        calls.append((x0, x1))
        return real(surf, sp, x0, x1, y, up, color)

    monkeypatch.setattr(notation, "draw_tie", record)

    sys0 = sv.systems[0]
    margin_left, usable_w = sv._header(rect, sys0)
    limit = margin_left + usable_w
    sv.draw(pygame.Surface(rect.size), rect, sys0.start_t)
    assert any(abs(x1 - limit) < 1e-6 for _x0, x1 in calls), \
        "the tie leaving the system should reach the closing bar line"

    calls.clear()
    sv.draw(pygame.Surface(rect.size), rect, sv.systems[1].start_t)
    assert any(abs(x0 - staff.STAFF_LEFT_X) < 1e-6 for x0, _x1 in calls), \
        "the tie entering the system should start at the staff's left edge"


def test_note_crossing_system_boundary_is_drawn_on_next_system():
    # A note starting in system 0 and extending into system 1 must be present in
    # system 1's split pieces so that the continuation note is drawn at the start
    # of the measure in system 1.
    song = load_midi(midi_path("50-nam-ve-sau.mid"))
    sv = StaffView(song)
    assert len(sv.systems) >= 2
    sys0, sys1 = sv.systems[0], sv.systems[1]
    # Find any note crossing the boundary between sys0 and sys1
    boundary = sys0.end_t
    crossing = [n for n in song.notes if n.start < boundary - 1e-9 and n.end > boundary + 1e-9]
    assert crossing, "fixture 50-nam-ve-sau should have notes crossing system boundary"

    pieces0, _ = staff.split_at_bar_lines(sys0.notes, song.bar_times)
    kept0 = [p for p in pieces0 if sys0.start_t - 1e-9 <= p.start < sys0.end_t - 1e-9]

    pieces1, _ = staff.split_at_bar_lines(sys1.notes, song.bar_times)
    kept1 = [p for p in pieces1 if sys1.start_t - 1e-9 <= p.start < sys1.end_t - 1e-9]

    for n in crossing:
        # sys0 has the head piece ending at boundary
        head = [p for p in kept0 if p.pitch == n.pitch and abs(p.end - boundary) < 1e-4]
        assert head, f"system 0 should hold head piece for crossing pitch {n.pitch}"
        # sys1 has the tail piece starting at boundary
        tail = [p for p in kept1 if p.pitch == n.pitch and abs(p.start - boundary) < 1e-4]
        assert tail, f"system 1 should hold tail piece for crossing pitch {n.pitch}"


def test_tuplet_groups_with_chord_at_start():
    # If a chord has multiple notes at the same start time with tuplet_type="start",
    # they belong to the same group rather than splitting into separate groups.
    notes = [
        Note(68, 0.0, 0.2, 80, 0, RIGHT, tuplet_num=3, tuplet_type="start"),
        Note(71, 0.0, 0.2, 80, 0, RIGHT, tuplet_num=3, tuplet_type="start"),
        Note(73, 0.2, 0.4, 80, 0, RIGHT, tuplet_num=3, tuplet_type="None"),
        Note(75, 0.4, 0.6, 80, 0, RIGHT, tuplet_num=3, tuplet_type="stop"),
    ]
    info = staff.compute_notes_info(notes, 0.0, 1.0, 600.0, staff.MARGIN_LEFT, 10.0)
    groups = staff.find_tuplet_groups(notes, info)
    assert len(groups) == 1
    assert len(groups[0][0]) == 4


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


def test_a_note_at_a_measure_start_keeps_off_the_bar_line():
    # The bar line of a measure sits where its beat is, and a note that starts on that beat
    # stands a noteBarDistance off it - which for a sharped note means its accidental, not
    # just its head, has to clear the line.
    s = 7.0
    margin_left, usable_w, sys_duration = staff.MARGIN_LEFT, 1000.0, 10.0
    n_plain = Note(pitch=64, start=4.0, end=4.4, velocity=80, track=0, hand=RIGHT)
    n_sharp = Note(pitch=61, start=6.0, end=6.4, velocity=80, track=0, hand=RIGHT)
    info = staff.compute_notes_info([n_plain, n_sharp], 0.0, sys_duration, usable_w,
                                    margin_left, s, 0.5, [4.0, 6.0])
    head_half = notation.HEAD_BOX["black"][2] * s / 2.0          # half a notehead's width
    gap = staff.NOTE_BAR_DISTANCE_SP * s

    bar_x = margin_left + 4.0 / sys_duration * usable_w
    # a plain note keeps the whole staff space and a bit of ledger line off the line
    assert info[id(n_plain)]["x"] - (head_half + notation.LEDGER_EXT_SP * s) >= bar_x + gap - 1e-6

    bar_x = margin_left + 6.0 / sys_duration * usable_w
    accidental = head_half + notation.NOTEHEAD_W_SP * s + notation.ACC_DIST_SP * s
    assert info[id(n_sharp)]["x"] - accidental >= bar_x + gap - 1e-6, (
        "the sharp of a note on a downbeat is printed on the bar line")
    # and it only ever moves sideways, never off the system
    assert info[id(n_sharp)]["x"] <= margin_left + usable_w


def test_a_crowded_measure_is_squeezed_inside_its_bar_lines():
    # A run of fast notes late in a measure is pushed right so their flags do not touch,
    # and at a crowded measure that push runs past the bar line: the last notes would be
    # printed on the next measure's side of the bar, with their beam through it. The
    # measure is squeezed from its bar line instead, and the notes before the last one
    # come along, each keeping its minimum gap.
    s = 7.0
    margin_left, usable_w, sys_duration = staff.MARGIN_LEFT, 400.0, 5.0
    starts = [1.875 + 0.125 * i for i in range(5)]
    notes = [Note(pitch=76, start=t, end=t + 0.125, velocity=80, track=0, hand=RIGHT)
             for t in starts]
    info = staff.compute_notes_info(notes, 0.0, sys_duration, usable_w,
                                    margin_left, s, 0.5, [2.5, 5.0])
    nw = round(s * 0.75)
    tail = nw + 0.9 * s                      # a sixteenth's flag reaches past its head
    gap = staff.NOTE_BAR_DISTANCE_SP * s
    bar_x = margin_left + 2.5 / sys_duration * usable_w

    xs = [info[id(n)]["x"] for n in notes]
    assert xs == sorted(xs), "the squeeze moved the notes out of their order"
    assert xs[-1] + tail <= bar_x - gap + 1e-6, "the last note is printed past its bar line"
    for left, right in zip(xs, xs[1:]):
        assert right - left >= tail + (nw + 1.0) - 1e-6, (
            "the squeeze closed the notes up until their ink touched")
    # the notes were pulled left of their beats to make the room, never off the paper
    assert all(x <= staff.MARGIN_LEFT + t / sys_duration * usable_w + 1e-6
               for x, t in zip(xs, starts))
    assert xs[0] >= margin_left


def test_a_measure_with_room_keeps_its_notes_on_their_beats():
    # The squeeze only ever answers a bar line that is too close: a measure with room in
    # it is left exactly on its beats.
    s = 7.0
    margin_left, usable_w, sys_duration = staff.MARGIN_LEFT, 1000.0, 10.0
    notes = [Note(pitch=64, start=1.0, end=1.5, velocity=80, track=0, hand=RIGHT),
             Note(pitch=67, start=2.0, end=2.5, velocity=80, track=0, hand=RIGHT)]
    info = staff.compute_notes_info(notes, 0.0, sys_duration, usable_w,
                                    margin_left, s, 0.5, [3.0, 10.0])
    for n in notes:
        assert info[id(n)]["x"] == margin_left + n.start / sys_duration * usable_w


def test_a_bar_line_is_drawn_where_its_beat_is(screen):
    # The measure line and the playback cursor are two drawings of one moment: the line is
    # at the beat's x, so the note that starts on it sits a noteBarDistance to its right
    # rather than on it.
    notes = [_note(60, 0.0, RIGHT), _note(62, 2.0, RIGHT), _note(64, 6.0, RIGHT)]
    song = _BracketSong(notes)
    rect = pygame.Rect(0, 0, 1200, 140)
    usable_w = rect.w - MARGIN_LEFT - MARGIN_RIGHT
    s = max(4.0, min(12.0, (rect.h - 26) / 14.0))

    sys0 = StaffView(song).systems[0]
    assert len(sys0.measures) > 1, "the test song should need a bar line inside a system"
    wanted = [round(MARGIN_LEFT + (m.start_t - sys0.start_t) / sys0.duration * usable_w)
              for m in sys0.measures[1:]]

    drawn = []
    real_line = pygame.draw.line

    def spy(surf, color, a, b, width=1):
        if abs(a[0] - b[0]) <= 1 and abs(a[1] - b[1]) > 6 * s:      # a full staff height
            drawn.append(a[0])
        return real_line(surf, color, a, b, width)

    pygame.draw.line = spy
    try:
        StaffView(song).draw(screen, rect, 1.0)
    finally:
        pygame.draw.line = real_line

    assert wanted[0] in drawn, f"the bar line is not at {wanted[0]} where its beat is"
    assert wanted[0] - 10 not in drawn, "the bar line was drawn 10px short of its beat"


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
    assert any("reset" in d.lower() for d in controls.values())


def test_staff_zoom_reset_button_and_shortcuts(screen):
    app = main.App(screen, sound=False)
    app.play(midi_path("demo_song.mid"))
    vis = app.mode

    # Change zoom away from default
    vis.set_staff_target_dur(6.0)
    vis.set_staff_scale(1.25)
    assert vis.staff_target_dur == 6.0
    assert abs(vis.staff_scale - 1.25) < 1e-6

    # When rendered with custom zoom, the reset button is visible
    vis.render()
    assert vis.staff_reset_button.w > 0 and vis.staff_reset_button.h > 0

    # Clicking the reset button resets zoom
    reset_click = vis.staff_reset_button.center
    app.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=reset_click))
    assert vis.staff_target_dur == 10.0
    assert abs(vis.staff_scale - 1.0) < 1e-6

    # When rendered at default zoom, reset button is hidden
    vis.render()
    assert vis.staff_reset_button.h == 0

    # Shortcut '0' resets zoom
    vis.set_staff_target_dur(5.0)
    app.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_0, mod=0))
    assert vis.staff_target_dur == 10.0

    # Double click on staff resets zoom
    vis.set_staff_scale(1.3)
    staff_pt = (vis.staff_rect.centerx, vis.staff_rect.centery)
    vis._last_staff_click = pygame.time.get_ticks()
    app.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=staff_pt))
    assert abs(vis.staff_scale - 1.0) < 1e-6


def test_per_song_zoom_persistence(screen, tmp_path):
    app = main.App(screen, sound=False)
    song_a_path = midi_path("demo_song.mid")
    app.play(song_a_path)
    vis = app.mode

    # Configure Song A custom zoom
    vis.set_staff_target_dur(7.5)
    vis.set_staff_scale(1.2)
    saved_dur, saved_scale = pianist.get_song_zoom(vis.song)
    assert saved_dur == 7.5
    assert abs(saved_scale - 1.2) < 1e-6

    # Switch to synthetic Song B (has different path / identity)
    notes_b = [Note(pitch=60, start=0.0, end=1.0, velocity=80, track=0, hand=RIGHT)]
    song_b = MidiSong(notes_b, [], 5.0, [], [], path=str(tmp_path / "song_b.mid"))
    vis.set_song(song_b)
    # Song B should use defaults or global settings, not Song A's custom zoom
    assert vis.staff_target_dur == 10.0 or vis.staff_target_dur != 7.5

    # Switch back to Song A: custom zoom restored
    vis.set_song(load_midi(song_a_path))
    assert vis.staff_target_dur == 7.5
    assert abs(vis.staff_scale - 1.2) < 1e-6

    # Resetting Song A clears override
    vis.reset_staff_zoom()
    assert vis.staff_target_dur == 10.0
    assert abs(vis.staff_scale - 1.0) < 1e-6
    # Calling get_song_zoom now falls back to default
    dur, scale = pianist.get_song_zoom(vis.song, default_dur=10.0, default_scale=1.0)
    assert dur == 10.0 and abs(scale - 1.0) < 1e-6


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


def test_a_beam_bar_stays_inside_the_panel_when_the_stems_can_give_way():
    # The panel is the paper, but it is not a veto: a clear direction whose bar would poke
    # out of the panel has its stems shortened until the bar sits on the edge - as long as
    # its own noteheads stay clear of the stack. Only when not even that much fits does the
    # bar stay where the engraver asked for it and the other side win.
    s = 8.0
    beam_w, gap, nw = 4.0, 6.0, 6.0
    reach = round(3.5 * s)
    head_half = notation.NOTEHEAD_H_SP * s / 2.0
    tight = head_half + (0.5 + 0.25 + staff.BEAM_MIN_HEAD_GAP_SP) * s   # one bar, no stack
    panel = (0, 60)
    no_ink, own = [], frozenset()

    # A group high on a short panel: the engraver's bar lands off the top, the stems give
    # up the length the panel takes and the bar comes down onto the paper.
    stops = [(100.0, 20.0, 36.0, False)]
    up, tip = staff.layout_beam(stops, True, reach, beam_w, gap, nw, no_ink, own, s, panel)
    assert up, "the engraver's direction still wins when both sides are clear"
    assert tip - beam_w / 2 >= panel[0], f"the bar at {tip} hangs off the top"
    assert stops[0][1] - tip >= tight - 1e-6, "the stems were shortened past the notehead"

    # A group so low that even a bar on the edge would sit on its own noteheads: flipping
    # is not an option, so the bar keeps the engraver's direction (and its full length).
    stops = [(100.0, 40.0, 56.0, False)]
    up, tip = staff.layout_beam(stops, True, reach, beam_w, gap, nw, no_ink, own, s, panel)
    assert up, "a downward bar here would land on the group's own noteheads"
    assert panel[0] <= tip - beam_w / 2 and tip + beam_w / 2 <= panel[1]
    assert stops[0][1] - tip >= reach - 1e-6, "the panel stole a stem that had room"

    # And one where no bar fits at all on the engraver's side: the other side draws, and
    # its bar is the one that has to fit.
    stops = [(100.0, 4.0, 20.0, False)]
    up, tip = staff.layout_beam(stops, True, reach, beam_w, gap, nw, no_ink, own, s, panel)
    assert not up, "the side that can be drawn wins over the side that cannot"
    assert panel[0] <= tip - beam_w / 2 and tip + beam_w / 2 <= panel[1]
    assert tip - stops[0][2] >= tight - 1e-6


def test_a_sixteenths_second_bar_sits_between_the_first_bar_and_the_notes(screen):
    # A run of sixteenths carries a second bar. It goes between the first bar and the
    # noteheads - the stems run through it, and the reach asked of the layout already
    # leaves room for a stack that way. Drawn on the far side it would float past the end
    # of the stems with nothing holding it up.
    song = _BracketSong([_note(64, 0.1 * i, RIGHT, dur=0.1) for i in range(4)])
    rect = pygame.Rect(0, 0, 1200, 140)

    bars, stems = [], []
    real_lines, real_line = pygame.draw.lines, pygame.draw.line
    hand_cols = {staff.HAND_COLORS[RIGHT][0], staff.HAND_COLORS[LEFT][0]}

    def spy_lines(surf, color, closed, pts, width):
        if color == staff.STEM_COLOR and pts and len(pts) >= 2 \
                and len({p[1] for p in pts}) == 1:
            bars.append((pts[0][0], pts[-1][0], pts[0][1]))
        return real_lines(surf, color, closed, pts, width)

    def spy_line(surf, color, a, b, width):
        if color in hand_cols and abs(a[0] - b[0]) <= 1 and abs(a[1] - b[1]) > 4:
            stems.append((a[0], min(a[1], b[1]), max(a[1], b[1])))
        return real_line(surf, color, a, b, width)

    pygame.draw.lines, pygame.draw.line = spy_lines, spy_line
    try:
        screen.fill((255, 255, 255))
        StaffView(song).draw(screen, rect, 1.0)
    finally:
        pygame.draw.lines, pygame.draw.line = real_lines, real_line

    stacks = []
    for i, first in enumerate(bars):
        for second in bars[i + 1:]:
            if abs(first[0] - second[0]) <= 1 and abs(first[1] - second[1]) <= 1:
                stacks.append((first, second))
    assert stacks, "the four sixteenths should draw two bars"
    for first, second in stacks:
        touched = [st for st in stems
                   if first[0] - 2 <= st[0] <= first[1] + 2 and st[1] <= first[2] <= st[2]]
        assert touched, "the primary bar is the one the stems reach"
        st = touched[0]
        # the stem's far end is the notehead's: the bar sits at the other one
        head_side = st[2] if abs(st[2] - first[2]) > abs(st[1] - first[2]) else st[1]
        assert st[1] <= second[2] <= st[2], (
            "the second bar floats past the stems' ends with nothing holding it up")
        assert (second[2] - first[2]) * (head_side - first[2]) > 0, (
            "the second bar is on the wrong side of the first")


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


def test_the_octave_bracket_clears_the_stems_and_beams_it_covers(screen):
    # The layout can turn a stem over to keep it off the other hand, so a group's ink can
    # reach up towards its bracket - with a beam hanging off the stem's end - and the line
    # has to go over that, not over the noteheads. Here both groups have a stem pointing
    # straight at its own bracket: the 8va one beamed above its heads, the 8vb one a single
    # chord whose stem was turned down. (The heights are the ones the layout leaves room
    # for: a stem of a space and a half, both well inside the panel.)
    rect = pygame.Rect(0, 0, 1200, 340)
    s = max(4.0, min(12.0, (rect.h - 26) / 14.0))
    center_y = rect.centery
    treble_mid_y = center_y - round(3.0 * s)
    bass_mid_y = center_y + round(3.0 * s)
    beam_half = max(2, round(staff.BEAM_WIDTH_SPACES * s)) / 2.0
    gap = staff.OCTAVE_GAP_SPACES * s

    up_notes = [_note(93, 0.4, RIGHT), _note(96, 1.0, RIGHT)]     # A6, C7 -> 8va
    down_notes = [_note(21, 4.0, LEFT), _note(26, 4.6, LEFT)]     # A0, D1 -> 8vb
    song = _BracketSong(up_notes + down_notes)
    sys0 = staff.StaffView(song).systems[0]
    info = staff.compute_notes_info(sys0.notes, sys0.start_t, sys0.duration,
                                    rect.w - MARGIN_LEFT - MARGIN_RIGHT,
                                    MARGIN_LEFT, s, staff.get_beat_duration(song))

    heads = {}
    for n in up_notes + down_notes:
        i = info[id(n)]
        heads[n] = staff.note_y(i["step_draw"], i["clef"], treble_mid_y, bass_mid_y, s)
    beam_up_y = min(heads[n] for n in up_notes) - round(1.5 * s)      # a beam above the heads
    stem_dn_y = max(heads[n] for n in down_notes)                     # a stem turned down
    for n in up_notes:                                                # the beamed 8va run
        info[id(n)]["beam_stem_up"] = True
        info[id(n)]["beam_tip_y"] = beam_up_y
    for n in down_notes:                                              # the flipped 8vb chord
        info[id(n)]["chord_stem_up"] = False
        info[id(n)]["y_bot"] = stem_dn_y

    lines = []
    real_dashed = staff.draw_dashed_line

    def spy(surf, color, start_pos, end_pos, width=1, **kw):
        lines.append(start_pos[1])
        return real_dashed(surf, color, start_pos, end_pos, width=width, **kw)

    staff.draw_dashed_line = spy
    try:
        staff.draw_octave_brackets(screen, info, sys0.notes,
                                   treble_mid_y, bass_mid_y, s, rect)
    finally:
        staff.draw_dashed_line = real_dashed

    assert len(lines) == 2, f"the test song should draw two brackets, got {len(lines)}"
    above, below = sorted(lines)                        # 8va is over the treble, 8vb under
    assert above <= beam_up_y - beam_half - gap + 1, (
        f"the 8va line at y={above} sits on the beam at y={beam_up_y}")
    assert below >= stem_dn_y + staff.STEM_LEN_SPACES * s + gap - 1, (
        f"the 8vb line at y={below} sits on the stem that points at it")


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


def test_ottava_phrase_consistency_transposes_intermediate_notes():
    # Phrase in treble: F6 (89, triggers 8va), G5 (79), C6 (84), E6 (88, trigger)
    # The intermediate notes G5 and C6 must be transposed along with the phrase (-1 octave),
    # preserving melodic contour and keeping bracket integrity.
    n1 = _note(89, 1.0, RIGHT)  # F6 -> trigger 8va
    n2 = _note(79, 1.3, RIGHT)  # G5 -> intermediate
    n3 = _note(84, 1.6, RIGHT)  # C6 -> intermediate
    n4 = _note(88, 1.9, RIGHT)  # E6 -> trigger 8va
    song = _BracketSong([n1, n2, n3, n4])
    info = staff.compute_notes_info([n1, n2, n3, n4], 0.0, 5.0, 1000.0, staff.MARGIN_LEFT, 7.0)

    for n in (n1, n2, n3, n4):
        i = info[id(n)]
        assert i["octave_shift"] == -1, f"Note pitch {n.pitch} must be transposed under 8va"
        assert i["label"] == "8va"

    # G5 (79) natural step is 39; transposed by -7 should be step 32
    assert info[id(n2)]["step_draw"] == staff.diatonic_step(79)[0] - 7
    # C6 (84) natural step is 42; transposed by -7 should be step 35
    assert info[id(n3)]["step_draw"] == staff.diatonic_step(84)[0] - 7


def test_ottava_bracket_does_not_bridge_incompatible_pitches(screen):
    # Treble notes: C7 (96, extreme) at t=1.0, Middle C (60) at t=2.0, C7 (96, extreme) at t=3.0.
    # Middle C is far below the 8va range (< 72), so 8va must NOT bridge across it.
    n1 = _note(96, 1.0, RIGHT)
    n2 = _note(60, 2.0, RIGHT)
    n3 = _note(96, 3.0, RIGHT)
    info = staff.compute_notes_info([n1, n2, n3], 0.0, 5.0, 1000.0, staff.MARGIN_LEFT, 7.0)

    assert info[id(n1)]["octave_shift"] == -1
    assert info[id(n2)]["octave_shift"] == 0, "Middle C must not be transposed by 8va"
    assert info[id(n2)]["label"] == ""
    assert info[id(n3)]["octave_shift"] == -1

    # In draw_octave_brackets, they must not form a single bracket spanning across n2
    groups = []
    lines = []
    real_dashed = staff.draw_dashed_line

    def spy(surf, color, start_pos, end_pos, width=1, **kw):
        lines.append((start_pos[0], end_pos[0]))
        return real_dashed(surf, color, start_pos, end_pos, width=width, **kw)

    staff.draw_dashed_line = spy
    try:
        surf = pygame.Surface((1000, 200))
        staff.draw_octave_brackets(surf, info, [n1, n2, n3], 100, 150, 7.0)
    finally:
        staff.draw_dashed_line = real_dashed

    # Neither bracket line may span across n2's x position
    x_c = info[id(n2)]["x"]
    for x_start, x_end in lines:
        assert not (x_start <= x_c <= x_end), "8va line must not span across Middle C"


def test_isolated_single_note_uses_ledger_lines():
    # An isolated single note at pitch 88 (E6, 3 ledger lines) should NOT trigger 8va;
    # it stays at natural pitch with standard ledger lines.
    n = _note(88, 1.0, RIGHT)
    info = staff.compute_notes_info([n], 0.0, 5.0, 1000.0, staff.MARGIN_LEFT, 7.0)
    assert info[id(n)]["octave_shift"] == 0
    assert info[id(n)]["label"] == ""
    assert info[id(n)]["step_draw"] == staff.diatonic_step(88)[0]


def test_sequential_non_overlapping_notes_are_not_grouped_as_chords():
    # Rapid sequential grace notes: A4 (dur=0.05) at t=38.768, A#4 (dur=0.05) at t=38.820,
    # B4 (dur=0.40) at t=38.873. A4 ends before A#4 starts, so they are non-overlapping.
    n1 = Note(pitch=69, start=38.768, end=38.818, velocity=80, track=0, hand=RIGHT)
    n2 = Note(pitch=70, start=38.820, end=38.870, velocity=80, track=0, hand=RIGHT)
    n3 = Note(pitch=71, start=38.873, end=39.274, velocity=80, track=0, hand=RIGHT)

    chords = staff.group_chords([n1, n2, n3], 0.423)
    assert len(chords) == 3, f"Non-overlapping sequential notes must be 3 separate chords, got {len(chords)}"
    assert chords[0] == [n1]
    assert chords[1] == [n2]
    assert chords[2] == [n3]

    info = staff.compute_notes_info([n1, n2, n3], 38.0, 5.0, 1000.0, staff.MARGIN_LEFT, 7.0)
    assert info[id(n1)]["x"] < info[id(n2)]["x"] < info[id(n3)]["x"], "Each sequential note gets a distinct x position"


def test_ottava_phrase_8vb_bass_consistency():
    # Bass phrase: E1 (28, triggers 8vb), G2 (43), C2 (36), E1 (28, trigger)
    n1 = _note(28, 1.0, LEFT)
    n2 = _note(43, 1.4, LEFT)
    n3 = _note(36, 1.8, LEFT)
    n4 = _note(28, 2.2, LEFT)
    info = staff.compute_notes_info([n1, n2, n3, n4], 0.0, 5.0, 1000.0, staff.MARGIN_LEFT, 7.0)

    for n in (n1, n2, n3, n4):
        i = info[id(n)]
        assert i["octave_shift"] == 1, f"Bass note pitch {n.pitch} must be transposed under 8vb"
        assert i["label"] == "8vb"

    # G2 (43) natural step is 18; transposed by +7 should be step 25
    assert info[id(n2)]["step_draw"] == staff.diatonic_step(43)[0] + 7


def test_50_nam_ve_sau_system_0_ottava():
    import midi_loader
    song = midi_loader.load_midi('MIDIs/50-nam-ve-sau.mid')
    sv = staff.StaffView(song)
    sys0 = sv.systems[0]
    info = staff.compute_notes_info(sys0.notes, sys0.start_t, sys0.duration, 1000.0, staff.MARGIN_LEFT, 7.0)

    treble_notes = [n for n in sys0.notes if info[id(n)]["clef"] == staff.CLEF_TREBLE]
    treble_notes.sort(key=lambda n: (n.start, n.pitch))

    # Notes #1 and #2 (G5, t=0.42, 0.85) are before the high phrase -> octave_shift == 0
    assert info[id(treble_notes[0])]["octave_shift"] == 0
    assert info[id(treble_notes[1])]["octave_shift"] == 0

    # Notes #3 (F6, 89) through #13 (E6, 88) are in the 8va phrase
    for n in treble_notes[2:13]:
        i = info[id(n)]
        assert i["octave_shift"] == -1, f"Treble note pitch {n.pitch} at t={n.start} must be 8va transposed"
        assert i["label"] == "8va"

    # Specific check for notes #4 (G5), #5 (C6), #9 (D6), #11 (G5) mentioned by user
    n4, n5, n9, n11 = treble_notes[3], treble_notes[4], treble_notes[8], treble_notes[10]
    assert info[id(n4)]["step_draw"] == staff.diatonic_step(79)[0] - 7
    assert info[id(n5)]["step_draw"] == staff.diatonic_step(84)[0] - 7
    assert info[id(n9)]["step_draw"] == staff.diatonic_step(86)[0] - 7
    assert info[id(n11)]["step_draw"] == staff.diatonic_step(79)[0] - 7


# --------------------------------------------------------------------------- #
# Grace notes (note hoa mỹ)
# --------------------------------------------------------------------------- #
def _grace_song():
    """50-nam-ve-sau's figure at t=38.8: C5, then two notes flicked into the downbeat B4."""
    c5 = _note(72, 1.00, RIGHT, dur=0.4)
    g1 = _note(69, 1.42, RIGHT, dur=0.05)
    g2 = _note(70, 1.48, RIGHT, dur=0.05)
    main = _note(71, 1.535, RIGHT, dur=0.4)
    return c5, g1, g2, main


def test_grace_notes_are_marked_and_tucked_against_their_main_note():
    c5, g1, g2, main = _grace_song()
    s = 7.0
    # no bar lines to respect here: the run has all the room it needs, so the main
    # note stays exactly at its moment in time and the run tucks in against it
    info = staff.compute_notes_info([c5, g1, g2, main], 0.0, 5.0, 1000.0,
                                    staff.MARGIN_LEFT, s)

    # the two flicked notes are graces; the quarter before and the quarter they lean
    # on are music, written out full size
    assert info[id(g1)]["grace"] and info[id(g2)]["grace"]
    assert not info[id(c5)]["grace"] and not info[id(main)]["grace"]

    # played order kept, and the main note stays exactly at its moment in time -
    # the run is what moves, not the note it belongs to
    assert info[id(c5)]["x"] < info[id(g1)]["x"] < info[id(g2)]["x"]
    assert info[id(main)]["x"] == pytest.approx(
        staff.MARGIN_LEFT + 1.535 / 5.0 * 1000.0, abs=0.01)

    # MuseScore's spacing, edge of ink to edge of ink: graceToMainNoteDist (0.45 sp)
    # between the last grace and the main note's head...
    head_w = notation.HEAD_BOX["black"][2] - notation.HEAD_BOX["black"][0]
    main_to_last = (head_w * 0.5 * s + notation.GRACE_TO_MAIN_SP * s
                    + head_w * 0.5 * s * notation.GRACE_MAG)
    assert info[id(main)]["x"] - info[id(g2)]["x"] == pytest.approx(main_to_last, abs=0.05)

    # ...and graceToGraceNoteDist (0.3 sp) between two graces, stretched by the sharp
    # sitting between A4 and A#4's head
    grace_gap = (head_w * 0.5 * s * notation.GRACE_MAG + notation.GRACE_TO_GRACE_SP * s
                 + (head_w + notation.ACC_DIST_SP + head_w * 0.5)
                 * s * notation.GRACE_MAG)
    assert info[id(g2)]["x"] - info[id(g1)]["x"] == pytest.approx(grace_gap, abs=0.05)

    # the run keeps its air from the quarter before it
    prev_right = info[id(c5)]["x"] + round(s * 0.75)
    g1_left = info[id(g1)]["x"] - head_w * 0.5 * s * notation.GRACE_MAG
    assert g1_left >= prev_right + 1.0


def test_a_grace_run_belongs_to_the_measure_of_the_note_it_leans_on():
    # 50-nam-ve-sau's own timings: C5 at 38.451, the two flicked notes at 38.768 and
    # 38.820, the downbeat B4 at 38.873 - and the bar line at 38.873. The file puts
    # the grace onsets in the *previous* measure, but the run is the B4's: it is drawn
    # in the B4's measure, never across the bar line into the one before.
    s = 7.0
    bar = [0.0, 0.4233 * 4, 0.4233 * 8]         # bar lines a bar apart (4/4 at this tempo)
    beat = 0.4233
    lead = Note(pitch=72, start=bar[1] + 4.4 * beat, end=bar[1] + 4.4 * beat + 0.3,
                velocity=80, track=0, hand=RIGHT)
    g1 = Note(pitch=69, start=bar[2] - 0.105, end=bar[2] - 0.055, velocity=80, track=0, hand=RIGHT)
    g2 = Note(pitch=70, start=bar[2] - 0.053, end=bar[2] - 0.003, velocity=80, track=0, hand=RIGHT)
    main = Note(pitch=71, start=bar[2], end=bar[2] + 0.4, velocity=80, track=0, hand=RIGHT)
    nxt = Note(pitch=74, start=bar[2] + 0.5, end=bar[2] + 0.9, velocity=80, track=0, hand=RIGHT)
    notes = [lead, g1, g2, main, nxt]

    usable = 1000.0
    info = staff.compute_notes_info(notes, 0.0, 5.0, usable, staff.MARGIN_LEFT, s,
                                    beat, bar)
    runs = staff.find_grace_runs(sorted(notes, key=lambda n: (n.start, n.pitch)), beat)
    assert runs and runs[0][0] == [g1, g2] and runs[0][1] is main

    bar_x = staff.MARGIN_LEFT + bar[2] / 5.0 * usable
    head_w = notation.HEAD_BOX["black"][2] - notation.HEAD_BOX["black"][0]
    for g in (g1, g2):
        left_ink = info[id(g)]["x"] - head_w * 0.5 * s * notation.GRACE_MAG
        assert left_ink >= bar_x + 1.0, "the run is drawn in the main note's measure"

    # the notes after the main note travel with it, so the main note never lands on the
    # next one - their spacing is what proportional spacing gave them, unchanged
    m_right = info[id(main)]["x"] + round(s * 0.75)
    assert info[id(nxt)]["x"] - m_right >= 1.0, "the main note does not touch the next one"


def test_a_grace_run_never_touches_the_bar_line_even_on_a_full_system():
    # 50-nam-ve-sau's own system 3: the run leans on the downbeat B4 at 38.873, whose bar
    # line opens the measure. The system is full enough that the notes after the main note
    # reach the paper's edge, so the run cannot keep its whole noteBarDistance - but it may
    # never be cut by the bar line, and that is what wins: the run and the notes after the
    # main note use the right margin rather than let the bar line cross a grace.
    import midi_loader
    song = midi_loader.load_midi('MIDIs/50-nam-ve-sau.mid')
    sv = staff.StaffView(song)
    sys3 = sv.systems[3]
    s = 12.0
    usable = 1400 - staff.MARGIN_LEFT - staff.MARGIN_RIGHT
    beat = staff.get_beat_duration(song)
    bar_times = ([m.start_t for m in sys3.measures[1:]]
                 + [sys3.start_t + sys3.duration])
    info = staff.compute_notes_info(sys3.notes, sys3.start_t, sys3.duration, usable,
                                    staff.MARGIN_LEFT, s, beat, bar_times)
    runs = staff.find_grace_runs(sys3.notes, beat)
    run, main = runs[0]

    # the bar line that opens the main note's measure
    opened = [bt for bt in bar_times if bt <= main.start + 1e-9]
    bar_x = staff.MARGIN_LEFT + (max(opened) - sys3.start_t) / sys3.duration * usable

    head_w = notation.HEAD_BOX["black"][2] - notation.HEAD_BOX["black"][0]
    for g in run:
        left_ink = info[id(g)]["x"] - head_w * 0.5 * s * notation.GRACE_MAG
        assert left_ink > bar_x, "the bar line never crosses a grace"

    # and the main note still keeps its air from the note after it
    after = [n for n in sys3.notes if n.start > main.start + 1e-9]
    next_note = min(after, key=lambda n: n.start)
    assert info[id(next_note)]["x"] - info[id(main)]["x"] >= round(s * 0.75)


def test_a_written_sixteenth_pickup_is_written_out_not_a_grace():
    # A grace is shorter than 0.15 of a beat - six tenths of a sixteenth. A real
    # written sixteenth (0.125 s at the half-beat) runs into a longer note too, but
    # it is music: it keeps its full size and its flags, and so does 50-nam-ve-sau's
    # 0.099 s pickup pair at t=1.5.
    p16 = _note(72, 2.00, RIGHT, dur=0.125)
    m16 = _note(74, 2.14, RIGHT, dur=0.4)
    info = staff.compute_notes_info([p16, m16], 0.0, 5.0, 1000.0, staff.MARGIN_LEFT, 7.0)
    assert not info[id(p16)]["grace"] and not info[id(m16)]["grace"]

    p99 = _note(69, 3.00, RIGHT, dur=0.099)
    m99 = _note(71, 3.105, RIGHT, dur=0.4)
    info = staff.compute_notes_info([p99, m99], 0.0, 5.0, 1000.0, staff.MARGIN_LEFT, 7.0)
    assert not info[id(p99)]["grace"] and not info[id(m99)]["grace"]


def test_grace_notes_draw_small_and_a_run_is_beamed_with_one_slash(screen):
    c5, g1, g2, main = _grace_song()
    song = _BracketSong([c5, g1, g2, main], duration=5.0, bar=2.0)
    rect = pygame.Rect(0, 0, 1200, 160)
    s = max(4.0, min(12.0, (rect.h - 26) / 14.0))

    drawn, slashed, beams = [], [], []
    real_note = notation.draw_staff_note
    real_slash, real_beam_slash = notation._stem_slash, notation.beam_slash

    def note_spy(surf, x, y, sp, shape, color, stem_up, *args, **kw):
        drawn.append((sp, shape, kw.get("grace"), kw.get("stem")))
        return real_note(surf, x, y, sp, shape, color, stem_up, *args, **kw)

    def slash_spy(surf, sp, x_stem, y_tip, up, color):
        slashed.append(sp)
        return real_slash(surf, sp, x_stem, y_tip, up, color)

    def beam_slash_spy(surf, sp, x0, x1, y, up, color):
        beams.append((sp, x0, x1))
        return real_beam_slash(surf, sp, x0, x1, y, up, color)

    notation.draw_staff_note = note_spy
    notation._stem_slash = slash_spy
    notation.beam_slash = beam_slash_spy
    try:
        out = pygame.Surface(rect.size)
        out.fill((0, 0, 0))
        staff.StaffView(song).draw(out, rect, 1.5)
    finally:
        notation.draw_staff_note = real_note
        notation._stem_slash = real_slash
        notation.beam_slash = real_beam_slash

    graces = [d for d in drawn if d[2]]
    full = [d for d in drawn if not d[2]]
    assert len(graces) == 2, f"the two flicked notes are drawn as graces, got {len(graces)}"
    assert len(full) == 2, "C5 and the main note keep their full size"

    # the grace notehead, its accidental and its stem all shrink to graceNoteMag...
    for sp, shape, _g, stem in graces:
        assert sp == pytest.approx(s * notation.GRACE_MAG, abs=1e-6)
        # ...its flags are replaced by the stroke, and it is beamed to its neighbour
        assert shape[1] == 0
        assert stem is not None
    for sp, _shape, _g, _stem in full:
        assert sp == s

    # MuseScore's look for a run of two flicked notes: a small beam across them, with a
    # single stroke through that beam - not a stroke on each stem.
    assert slashed == [], "a beamed run carries no per-stem stroke"
    assert len(beams) == 1, f"the run is beamed once, got {len(beams)}"
    sp, x0, x1 = beams[0]
    assert sp == pytest.approx(s * notation.GRACE_MAG, abs=1e-6)
    assert x1 > x0, "the stroke crosses the beam, left-hand grace to right-hand grace"

    # the two grace stems reach the one beam, drawn as small as the notes
    g_stems = [stem for _sp, _shape, _g, stem in graces]
    assert g_stems[0][1] == g_stems[1][1], "both stems end on the same beam"
    # the stem is the grace's own length, not a full-size stem
    assert all(abs(tip - low) < staff.STEM_LEN_SPACES * s
               for low, tip in (st if st[1] < st[0] else (st[1], st[0]) for st in g_stems))


def test_only_a_run_of_flag_carrying_graces_is_beamed():
    # two flicked notes short enough to carry flags: beamed together, one stroke
    c5, g1, g2, main = _grace_song()
    notes = [c5, g1, g2, main]
    info = staff.compute_notes_info(notes, 0.0, 5.0, 1000.0, staff.MARGIN_LEFT, 7.0)
    runs = staff.find_grace_runs(sorted(notes, key=lambda n: (n.start, n.pitch)), 0.5)
    beat_of = lambda t: 0.5  # noqa: E731

    beamed = staff.group_grace_beams(runs, info, beat_of)
    assert len(beamed) == 1
    run, clusters = beamed[0]
    assert [n.start for n in run] == [g1.start, g2.start]
    assert [c[0].start for c in clusters] == [g1.start, g2.start]

    # a single grace has no neighbour to beam to
    lone_info = staff.compute_notes_info([c5, g1, main], 0.0, 5.0, 1000.0, staff.MARGIN_LEFT, 7.0)
    lone = staff.find_grace_runs(sorted([c5, g1, main], key=lambda n: (n.start, n.pitch)), 0.5)
    assert staff.group_grace_beams(lone, lone_info, beat_of) == []


def test_grace_notes_never_join_a_beam():
    c5, g1, g2, main = _grace_song()
    notes = [c5, g1, g2, main]
    song = _BracketSong(notes, duration=5.0, bar=2.0)
    info = staff.compute_notes_info(notes, 0.0, 5.0, 1000.0, staff.MARGIN_LEFT, 7.0)
    clusters = staff.group_chords(sorted(notes, key=lambda n: (n.start, n.pitch)), 0.5)
    measure_of = {}
    for m in staff.StaffView(song).systems[0].measures:
        for n in notes:
            if m.start_t <= n.start < m.end_t:
                measure_of[id(n)] = m
    beat_of = lambda t: staff.get_beat_duration(song, t)  # noqa: E731

    # without the grace flag these two sixteenths would beam together...
    bare = {k: {"clef": v["clef"], "step_draw": v["step_draw"]} for k, v in info.items()}
    assert staff.group_beams(clusters, bare, beat_of, measure_of) != []

    # ...but a grace hangs off the note it leans on: it ends a beam and starts none
    assert staff.group_beams(clusters, info, beat_of, measure_of) == []


def test_written_spelling_and_printed_value_come_from_the_file():
    # A written Bb: step B (octave 4), alter -1, sounding as 70. The staff must
    # print B-flat (a flat, on B's step), not the pitch's own A-sharp.
    bb = Note(70, 0.0, 0.5, 64, 0, RIGHT, None, False,
              4 * 7 + "CDEFGAB".index("B"), -1, "quarter")
    step, alter = staff.written_step(bb)
    assert step == 4 * 7 + 6 and alter == -1
    assert staff.has_accidental(bb)
    assert staff.accidental_extra_sp(bb) == 0.0        # a single accidental takes no extra room
    # The pitch alone (no written spelling) still falls back to sharps.
    plain = Note(70, 0.0, 0.5, 64, 0, RIGHT)
    pstep, palter = staff.written_step(plain)
    assert (pstep, palter) == staff.diatonic_step(70)   # A#, the sharp reading

    # A printed whole note that lasts only a quarter (a whole tied, written short):
    # the <type> wins over the duration maths.
    assert staff.printed_value(bb, 0.5, 0.5) == QUARTER        # from <type>
    short = Note(60, 0.0, 0.5, 64, 0, RIGHT, None, False, None, 0, "whole")
    assert staff.printed_value(short, 0.5, 0.5) == "whole"     # the written value, not the length's
    assert staff.printed_value(plain, 0.5, 0.5) == QUARTER     # no <type>: from the duration


# --------------------------------------------------------------------------- #
# The key signature and the accidentals it covers
# --------------------------------------------------------------------------- #
class _KeySong(_BracketSong):
    """A song that declares a key signature."""

    def __init__(self, notes, key_fifths=0, duration=10.0, bar=2.0):
        super().__init__(notes, duration=duration, bar=bar)
        self.key_fifths = key_fifths
        self.key_changes = []

    def key_at(self, t):
        return self.key_fifths


def test_the_key_signature_is_drawn_at_the_head_of_the_staff(screen):
    # Four sharps (E major) stand between the clef and the time signature, each on the
    # staff step the tradition gives it: F5 on the treble staff's top line, C5, G5 and D5.
    song = _KeySong([_note(64, 0.0, RIGHT)], key_fifths=4)
    rect = pygame.Rect(0, 0, 1200, 140)
    out = _draw_staff_to(song, rect)

    s = max(4.0, min(12.0, (rect.h - 26) / 14.0))
    treble_mid_y = rect.centery - round(3.0 * s)
    bass_mid_y = rect.centery + round(3.0 * s)
    key_x = staff.key_sig_x(rect.x + 18, s)

    def ink(box):
        return sum(1 for x in range(box.left, box.right) for y in range(box.top, box.bottom)
                   if out.get_at((x, y))[:3] != staff.STAFF_BG)

    # the sharps stand on their steps: the treble staff's F5 is its top line, its C5 the
    # space below the middle one, its G5 the space above the top line, its D5 the line above
    # the middle one.
    for i, (step, sign) in enumerate(staff.key_sig_steps(4, CLEF_TREBLE)):
        x = key_x + i * staff.KEY_SIG_ADVANCE_SP * s
        mid_y = treble_mid_y - (step - TREBLE_MID_STEP) * (s * 0.5)
        box = pygame.Rect(round(x), round(mid_y - 1.4 * s), round(1.0 * s), round(2.8 * s))
        assert ink(box) > 0.05 * box.w * box.h, f"no sharp {i} on step {step}"
        assert sign == 1

    # the bass staff has the same key a third lower: F3 on its fourth line, and so on
    for i, (step, _sign) in enumerate(staff.key_sig_steps(4, CLEF_BASS)):
        x = key_x + i * staff.KEY_SIG_ADVANCE_SP * s
        mid_y = bass_mid_y - (step - BASS_MID_STEP) * (s * 0.5)
        box = pygame.Rect(round(x), round(mid_y - 1.4 * s), round(1.0 * s), round(2.8 * s))
        assert ink(box) > 0.05 * box.w * box.h, f"no sharp {i} on the bass staff"

    # a flat key writes flats instead
    flat_song = _KeySong([_note(64, 0.0, RIGHT)], key_fifths=-2)
    flat_out = _draw_staff_to(flat_song, rect)
    flat_ink = sum(1 for x in range(round(key_x), round(key_x + 2 * s)) for y in range(rect.h)
                   if flat_out.get_at((x, y))[:3] != staff.STAFF_BG)
    assert flat_ink > 0


def test_the_key_signature_pushes_the_time_signature_and_the_first_note():
    # The head of the staff holds the clef, the key signature and the time signature, so
    # a key signature makes it wider: the notes start further right, and the music between
    # them is a little narrower.
    s = 10.0
    left_x = 18.0
    plain = staff.header_margin(left_x, s, 0)
    assert plain == MARGIN_LEFT

    key_end = staff.key_sig_x(left_x, s) + staff.key_sig_width_sp(4, s) * s
    with_key = staff.header_margin(left_x, s, 4)
    assert with_key > MARGIN_LEFT, "the notes would run into the sharps"
    # the time signature stands clear of the key signature
    ts_x = staff.time_sig_x(left_x, s, key_end)
    assert ts_x - notation.TIME_SIG_HALF_W_SP * s > key_end
    # and the first note stands clear of the time signature
    assert with_key > ts_x + notation.TIME_SIG_HALF_W_SP * s

    # the cursor and the notes share the same margin
    song = _KeySong([_note(64, 0.0, RIGHT)], key_fifths=4)
    rect = pygame.Rect(0, 0, 1200, 140)
    sv = StaffView(song)
    margin_left, usable_w = sv._header(rect, sv.systems[0])
    assert margin_left == staff.header_margin(left_x, sv._staff_size(rect), 4)
    assert sv.cursor_x(0.0, rect) == rect.x + margin_left


def test_the_key_signature_covers_the_accidentals_it_alters():
    # E major: F, C, G and D are sharp in every octave, so those notes print no sign.
    # A# is not in the key, so it prints a sharp.
    f_sharp = Note(66, 0.0, 0.5, 80, 0, RIGHT, None, False, 3 * 7 + 3, 1, None)
    a_sharp = Note(70, 0.5, 1.0, 80, 0, RIGHT, None, False, 4 * 7 + 5, 1, None)
    plan = staff.plan_accidentals([f_sharp, a_sharp], [0.0, 2.0], 4)
    assert plan[id(f_sharp)] is None, "the key already sharpens the F"
    assert plan[id(a_sharp)] == 1, "the A is not in the key"

    # the same notes in C major print their sharps
    plan = staff.plan_accidentals([f_sharp, a_sharp], [0.0, 2.0], 0)
    assert plan[id(f_sharp)] == 1
    assert plan[id(a_sharp)] == 1

    # a flat key: Bb is written as a flat and prints no sign
    b_flat = Note(70, 0.0, 0.5, 80, 0, RIGHT, None, False, 4 * 7 + 6, -1, None)
    plan = staff.plan_accidentals([b_flat], [0.0, 2.0], -2)
    assert plan[id(b_flat)] is None


def test_an_accidental_holds_for_its_measure():
    # The sign a note prints holds for the rest of its measure: the notes of that step
    # and octave after it print no sign, and the next measure prints it again.
    sharp = lambda t: Note(66, t, t + 0.4, 80, 0, RIGHT, None, False, 3 * 7 + 3, 1, None)
    first, second, next_measure = sharp(0.0), sharp(0.5), sharp(2.0)
    plan = staff.plan_accidentals([first, second, next_measure], [0.0, 2.0], 0)
    assert plan[id(first)] == 1, "the first F# prints the sharp"
    assert plan[id(second)] is None, "the sign holds for the rest of the measure"
    assert plan[id(next_measure)] == 1, "the next measure prints it again"

    # a natural after a sharp in the same measure says so again
    natural = Note(65, 0.8, 1.2, 80, 0, RIGHT, None, False, 3 * 7 + 3, 0, None)
    plan = staff.plan_accidentals([first, natural], [0.0, 2.0], 0)
    assert plan[id(natural)] == 0, "the F natural prints a natural"

    # in a key that sharpens the F, the F natural still prints one
    plan = staff.plan_accidentals([first, natural], [0.0, 2.0], 4)
    assert plan[id(natural)] == 0

    # and a plain F in a sharp key prints a natural: the key sharpens it
    plain = Note(65, 0.0, 0.4, 80, 0, RIGHT, None, False, 3 * 7 + 3, 0, None)
    plan = staff.plan_accidentals([plain], [0.0, 2.0], 4)
    assert plan[id(plain)] == 0
    # in C major it prints nothing at all
    plan = staff.plan_accidentals([plain], [0.0, 2.0], 0)
    assert plan[id(plain)] is None


def test_the_notes_of_a_key_signature_score_print_no_accidental(screen):
    # The whole point of the key signature: the notes it covers print no sign.
    song = _KeySong([_note(66, 0.0, RIGHT), _note(61, 0.5, RIGHT), _note(63, 1.0, RIGHT),
                     _note(68, 1.5, RIGHT), _note(64, 2.0, RIGHT)], key_fifths=4)
    rect = pygame.Rect(0, 0, 1200, 140)
    printed = []
    real = notation.draw_staff_note

    def spy(*args, **kwargs):
        printed.append(kwargs.get("accidental", args[9] if len(args) > 9 else None))
        return real(*args, **kwargs)

    notation.draw_staff_note = spy
    staff.notation.draw_staff_note = spy
    try:
        StaffView(song).draw(screen, rect, 0.0)
    finally:
        notation.draw_staff_note = real
        staff.notation.draw_staff_note = real

    assert printed, "the test song should draw its notes"
    assert all(a is None for a in printed), f"the key should cover them all, got {printed}"

