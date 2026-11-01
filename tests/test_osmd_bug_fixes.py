"""
tests/test_osmd_bug_fixes.py - Test suite for the 7 reviewed OSMD bugs and fixes.
"""
import threading
import pygame
import pytest
from staff import (
    StaffView, OSMDSystemItem,
    _map_measure_frac_to_time, _map_measure_time_to_frac
)
import osmd_render


class DummySong:
    def __init__(self, bar_times=None, beat_times=None, duration=10.0):
        self.bar_times = bar_times or [0.0, 2.0, 4.0, 6.0]
        self.beat_times = beat_times or [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0]
        self.duration = duration
        self.notes = []


def test_bug1_tempo_aware_beat_interpolation():
    """Bug #1: non-linear tempo inside measure must map accurately through beat_times."""
    # Measure from 0.0 to 3.0 s with 4 beats:
    # Beat 0: 0.0s, Beat 1: 0.5s, Beat 2: 1.5s (tempo slows), Beat 3: 2.5s, Measure end: 3.0s
    song = DummySong(
        bar_times=[0.0, 3.0, 6.0],
        beat_times=[0.0, 0.5, 1.5, 2.5, 3.0, 3.5, 4.0]
    )

    # In 4/4, frac 0.25 is Beat 1 -> should map to 0.5s
    t_b1 = _map_measure_frac_to_time(song, 0.0, 3.0, 0.25)
    assert abs(t_b1 - 0.5) < 1e-4

    # frac 0.50 is Beat 2 -> should map to 1.5s (not 1.5s linear 1.5/3.0, but matching beat)
    t_b2 = _map_measure_frac_to_time(song, 0.0, 3.0, 0.50)
    assert abs(t_b2 - 1.5) < 1e-4

    # frac 0.75 is Beat 3 -> should map to 2.5s
    t_b3 = _map_measure_frac_to_time(song, 0.0, 3.0, 0.75)
    assert abs(t_b3 - 2.5) < 1e-4

    # Reverse: time to frac
    assert abs(_map_measure_time_to_frac(song, 0.0, 3.0, 0.5) - 0.25) < 1e-4
    assert abs(_map_measure_time_to_frac(song, 0.0, 3.0, 1.5) - 0.50) < 1e-4
    assert abs(_map_measure_time_to_frac(song, 0.0, 3.0, 2.5) - 0.75) < 1e-4


def test_bug2_thread_safe_lock():
    """Bug #2: StaffView must have an active _osmd_lock and thread-safe systems."""
    view = StaffView(None)
    assert hasattr(view, '_osmd_lock')
    assert hasattr(view, '_osmd_request_id')

    # Locking can be acquired without deadlocks
    with view._osmd_lock:
        view.osmd_systems = []
        view.osmd_loading = True


def test_bug3_continuous_system_transitions():
    """Bug #3: Adjacent systems must partition time continuously without any gaps."""
    surf = pygame.Surface((1000, 200))
    # Two systems: system 0 covers bars 0..1, system 1 covers bars 2..3
    sys0 = OSMDSystemItem(0, surf, -1.0, 2.0, 1, 2, 20, 150, [
        {'number': 1, 'score_idx': 0, 'x': 50, 'w': 200, 'entries': [{'ts': 0, 'x': 60}]},
        {'number': 2, 'score_idx': 1, 'x': 250, 'w': 200, 'entries': [{'ts': 1, 'x': 260}]}
    ])
    sys1 = OSMDSystemItem(1, surf, 2.0, 10.0, 3, 4, 20, 150, [
        {'number': 3, 'score_idx': 2, 'x': 50, 'w': 200, 'entries': [{'ts': 2, 'x': 60}]},
        {'number': 4, 'score_idx': 3, 'x': 250, 'w': 200, 'entries': [{'ts': 3, 'x': 260}]}
    ])

    song = DummySong(bar_times=[0.0, 1.0, 2.0, 3.0, 4.0])
    view = StaffView(song)
    with view._osmd_lock:
        view.osmd_systems = [sys0, sys1]

    # At t = 1.9999, it belongs to sys0
    assert view.osmd_system_for_time(1.9999) is sys0
    # At t = 2.0, it transitions to sys1
    assert view.osmd_system_for_time(2.0) is sys1
    # Lead-in (t = -0.5) belongs to sys0
    assert view.osmd_system_for_time(-0.5) is sys0


def test_bug4_pickup_measure_score_idx():
    """Bug #4: Pickup measure numbered 0 or 1 maps correctly via score_idx."""
    surf = pygame.Surface((1000, 200))
    # Measure 0 (pickup, score_idx 0): 0.0 to 0.5s
    # Measure 1 (first full bar, score_idx 1): 0.5 to 2.5s
    m0 = {'number': 0, 'score_idx': 0, 'x': 50, 'w': 100, 'entries': [{'ts': 0, 'x': 60}]}
    m1 = {'number': 1, 'score_idx': 1, 'x': 150, 'w': 300, 'entries': [{'ts': 0.25, 'x': 170}]}
    sys0 = OSMDSystemItem(0, surf, 0.0, 2.5, 0, 1, 20, 150, [m0, m1], first_measure_num=0)

    song = DummySong(bar_times=[0.0, 0.5, 2.5])
    # x at t = 0.25 should be in measure 0 (between 50 and 150)
    x_pickup = sys0.x_for_time(0.25, song)
    assert 50 <= x_pickup <= 150

    # x at t = 1.0 should be in measure 1 (between 150 and 450)
    x_m1 = sys0.x_for_time(1.0, song)
    assert 150 <= x_m1 <= 450


def test_bug5_repeat_order_length_mismatch_tolerance():
    """Bug #5: repeat_order of length M works when len(bars) == M + 1 (closing barline)."""
    surf = pygame.Surface((1000, 200))
    m0 = {'number': 1, 'score_idx': 0, 'x': 50, 'w': 100, 'entries': [{'ts': 0, 'x': 60}]}
    m1 = {'number': 2, 'score_idx': 1, 'x': 150, 'w': 100, 'entries': [{'ts': 1, 'x': 160}]}
    sys0 = OSMDSystemItem(0, surf, 0.0, 4.0, 1, 2, 20, 150, [m0, m1])

    # Played: measure 0, measure 1, repeat measure 0 (3 played measures)
    repeat_order = [0, 1, 0]
    # bars has 4 items: start of 0, start of 1, start of repeat 0, and closing barline
    song = DummySong(bar_times=[0.0, 1.0, 2.0, 3.0])

    # During repeat of measure 0 (at t = 2.5s):
    # Cursor should be in measure 0 (50 <= x <= 150) even though len(repeat_order) = 3 != len(bars) = 4!
    x_rep = sys0.x_for_time(2.5, song, repeat_order=repeat_order)
    assert 50 <= x_rep <= 150


def test_bug7_start_renderer_no_crash():
    """Bug #7: start_renderer() must not raise AttributeError."""
    osmd_render.start_renderer()


def test_osmd_javascript_is_bundled_locally():
    """The score is engraved from osmd_static/, not fetched from the CDN."""
    tag = osmd_render._get_osmd_script_tag()
    assert tag.startswith("<script>")
    assert "OpenSheetMusicDisplay" in tag
    assert "cdn.jsdelivr.net" not in tag


def test_a_missing_bundle_falls_back_to_the_cdn_and_says_why(tmp_path, monkeypatch, capsys):
    """The warning names the paths it tried, so a bad build is easy to diagnose."""
    missing = tmp_path / "osmd_static" / "opensheetmusicdisplay.min.js"
    monkeypatch.setattr(osmd_render.paths, "resource", lambda *parts: str(missing))
    monkeypatch.setattr(osmd_render.paths, "exe_dir", lambda: str(tmp_path))

    tag = osmd_render._get_osmd_script_tag()

    assert "cdn.jsdelivr.net" in tag
    out = capsys.readouterr().out
    assert str(missing) in out and "FileNotFoundError" in out


def test_an_unreadable_bundle_says_how_to_fix_its_permissions(tmp_path, monkeypatch, capsys):
    """Opening a directory raises PermissionError - the hint should point at icacls."""
    blocked = tmp_path / "osmd_static"
    blocked.mkdir()
    monkeypatch.setattr(osmd_render.paths, "resource", lambda *parts: str(blocked))
    monkeypatch.setattr(osmd_render.paths, "exe_dir", lambda: str(tmp_path))

    tag = osmd_render._get_osmd_script_tag()

    assert "cdn.jsdelivr.net" in tag
    out = capsys.readouterr().out
    assert "PermissionError" in out and "icacls" in out


def test_shift_o_switches_between_osmd_and_the_native_staff():
    """Shift+O toggles the view; the rendered systems are kept for the way back."""
    surf = pygame.Surface((1000, 200))
    sys0 = OSMDSystemItem(0, surf, 0.0, 4.0, 1, 2, 20, 150, [
        {'number': 1, 'score_idx': 0, 'x': 50, 'w': 100, 'entries': [{'ts': 0, 'x': 60}]}
    ])
    view = StaffView(None)
    view.song = DummySong()
    view.osmd_systems = [sys0]
    view.use_osmd = True

    assert view.toggle_osmd() is False              # OSMD -> the native staff
    assert view.use_osmd is False
    assert view.osmd_systems == [sys0]              # ...which keeps the engraving

    assert view.toggle_osmd() is True               # back to OSMD, no re-render
    assert view.use_osmd is True
    assert view.osmd_systems == [sys0]


def test_switching_off_osmd_abandons_a_render_in_flight():
    """The placeholder cannot stay up, and the late result is dropped."""
    view = StaffView(None)
    view.song = DummySong()
    view.use_osmd = True
    view.osmd_loading = True
    request_id = view._osmd_request_id

    assert view.toggle_osmd() is False
    assert view.use_osmd is False
    assert view.osmd_loading is False
    assert view._osmd_request_id == request_id + 1

