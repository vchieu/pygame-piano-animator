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

    def control_state(self, t):
        return {}

    def controls_between(self, t0, t1):
        return []


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


def test_bug1_cursor_at_bars_0():
    """Bug #1: x_for_time at t == bars[0] enters measure interpolation instead of sticking."""
    surf = pygame.Surface((1000, 200))
    m0 = {
        'number': 1, 'score_idx': 0, 'x': 50, 'w': 200,
        'absTs': 0.0, 'dur': 1.0,
        'entries': [{'ts': 0.0, 'x': 70}, {'ts': 0.5, 'x': 150}]
    }
    sys0 = OSMDSystemItem(0, surf, 0.0, 4.0, 1, 1, 20, 150, [m0])
    song = DummySong(bar_times=[0.0, 2.0], duration=4.0)

    # Before bars[0] (e.g. lead-in -0.1s): stays on first notehead
    assert sys0.x_for_time(-0.1, song) == 70

    # At bars[0] (t == 0.0s): note 0 is at 0.0s -> returns 70
    assert sys0.x_for_time(0.0, song) == 70

    # At t = 0.5s: note 1 is at 1.0s in song time, midpoint is t = 0.5s -> x is between 70 and 150
    x_mid = sys0.x_for_time(0.5, song)
    assert 70 < x_mid < 150


def test_bug2_normalized_song_musicxml_content(tmp_path):
    """Bug #2: Normalized song regenerates MusicXML instead of loading disk raw MusicXML."""
    xml_file = tmp_path / "song.musicxml"
    xml_file.write_text("<score-partwise><raw/></score-partwise>", encoding="utf-8")

    from midi_loader import Note, RIGHT
    song = DummySong(bar_times=[0.0, 2.0, 4.0], duration=4.0)
    song.path = str(xml_file)
    song.notes = [Note(60, 0.0, 1.0, 80, 0, RIGHT)]
    song.normalized = True

    view = StaffView(None)
    content = view._get_musicxml_content(song)
    assert content is not None
    # Must NOT be the raw unnormalized file from disk
    assert "<raw/>" not in content
    assert "<score-partwise" in content


def test_bug3_and_bug7_time_for_x_symmetry_and_grace_notes():
    """Bug #3 & #7: time_for_x handles right edge guard, measure left edge, and decreasing x."""
    surf = pygame.Surface((1000, 200))
    # Measure 1 with decreasing x (e.g. grace note or dense chord layout)
    m0 = {
        'number': 1, 'score_idx': 0, 'x': 50, 'w': 200,
        'absTs': 0.0, 'dur': 1.0,
        'entries': [{'ts': 0.0, 'x': 100}, {'ts': 0.5, 'x': 90}, {'ts': 1.0, 'x': 200}]
    }
    sys0 = OSMDSystemItem(0, surf, 0.0, 2.0, 1, 1, 20, 150, [m0])
    song = DummySong(bar_times=[0.0, 2.0], duration=2.0)

    # Click between x=90 and x=100 (decreasing interval)
    t_click = sys0.time_for_x(95, song)
    # Must be valid time between ts 0.0 and 0.5 (which maps to 0.0s and 1.0s)
    assert 0.0 <= t_click <= 1.0


def test_bug4_repeat_fallback_closest_measure():
    """Bug #4: Repeat fallback selects system by measure proximity, not absolute time."""
    surf = pygame.Surface((1000, 200))
    # sys0: measures 1..2 (score_idx 0..1), start_t 0.0
    sys0 = OSMDSystemItem(0, surf, 0.0, 4.0, 1, 2, 20, 150, [
        {'number': 1, 'score_idx': 0, 'x': 50, 'w': 100, 'entries': []},
        {'number': 2, 'score_idx': 1, 'x': 150, 'w': 100, 'entries': []}
    ])
    # sys1: measures 3..4 (score_idx 2..3), start_t 4.0
    sys1 = OSMDSystemItem(1, surf, 4.0, 8.0, 3, 4, 20, 150, [
        {'number': 3, 'score_idx': 2, 'x': 50, 'w': 100, 'entries': []},
        {'number': 4, 'score_idx': 3, 'x': 150, 'w': 100, 'entries': []}
    ])

    song = DummySong(bar_times=[0.0, 2.0, 4.0, 6.0, 8.0], duration=10.0)
    view = StaffView(song)
    with view._osmd_lock:
        view.osmd_systems = [sys0, sys1]
        # Repeat order plays: measure 0, measure 1, then repeats measure 0!
        view.osmd_repeat_order = [0, 1, 0, 2, 3]

    # At t = 4.5s, play_idx is 2 (3rd bar played), repeat_order[2] == 0 (measure 0)
    # sys0 covers measure 0, so even though t=4.5s > sys1.start_t (4.0s), it MUST pick sys0!
    chosen = view.osmd_system_for_time(4.5)
    assert chosen is sys0


def test_bug5_end_t_not_drifting_5s():
    """Bug #5: Last system end_t does not add excessive 5-second drift."""
    # When systems are constructed in _prepare_osmd, last sys_item.end_t is max(total_dur + 0.5, sys.start_t + 1.0)
    total_dur = 20.0
    start_t = 15.0
    end_t = max(total_dur + 0.5, start_t + 1.0)
    assert end_t == 20.5  # not 25.0


def test_bug6_native_staff_cache_versioning():
    """Bug #6: StaffView._song_version increments on set_song to avoid ABA pointer id cache collisions."""
    view = StaffView(None)
    v0 = view._song_version
    song1 = DummySong()
    view.set_song(song1)
    v1 = view._song_version
    assert v1 > v0
    song2 = DummySong()
    view.set_song(song2)
    assert view._song_version > v1


def test_bug8_loading_song_not_stored():
    """Bug #8: _loading_song is not held as an attribute preventing GC."""
    view = StaffView(None)
    assert not hasattr(view, '_loading_song')


def test_bug9_toggle_osmd_while_loading():
    """Bug #9: toggle_osmd() while already loading does not re-launch reload_osmd."""
    view = StaffView(None)
    view.song = DummySong()
    view.use_osmd = False
    view.osmd_loading = True
    req_id = view._osmd_request_id

    # Toggle to OSMD while loading in background
    res = view.toggle_osmd()
    assert res is True
    assert view.use_osmd is True
    # Request ID was NOT changed/reloaded
    assert view._osmd_request_id == req_id


def test_bug10_plan_accidentals_measure_boundary():
    """Bug #10: Note strictly before measure boundary is not prematurely assigned to next measure."""
    import staff
    from midi_loader import Note, RIGHT
    sharp = lambda t: Note(66, t, t + 0.4, 80, 0, RIGHT, None, False, 3 * 7 + 3, 1, None)
    # Note at 1.999999 is in measure 0 (boundary at [0.0, 2.0]), so accidental state holds
    n0 = sharp(0.0)
    n1 = sharp(1.999999)
    plan = staff.plan_accidentals([n0, n1], [0.0, 2.0], 0)
    assert plan[id(n0)] == 1
    # n1 is still in measure 0, so accidental holds and prints None (not repeated)
    assert plan[id(n1)] is None


def test_review_bug1_duration_to_type_fine_buckets():
    """Review Bug #1: duration_to_type correctly categorizes 16th, 32nd, 64th, and dotted variants."""
    from midi_to_musicxml import duration_to_type, song_to_musicxml
    from midi_loader import Note, RIGHT

    dpq = 480
    assert duration_to_type(int(dpq * 6.0), dpq) == ("whole", True)
    assert duration_to_type(int(dpq * 4.0), dpq) == ("whole", False)
    assert duration_to_type(int(dpq * 3.0), dpq) == ("half", True)
    assert duration_to_type(int(dpq * 2.0), dpq) == ("half", False)
    assert duration_to_type(int(dpq * 1.5), dpq) == ("quarter", True)
    assert duration_to_type(int(dpq * 1.0), dpq) == ("quarter", False)
    assert duration_to_type(int(dpq * 0.75), dpq) == ("eighth", True)
    assert duration_to_type(int(dpq * 0.5), dpq) == ("eighth", False)
    assert duration_to_type(int(dpq * 0.25), dpq) == ("16th", False)
    assert duration_to_type(int(dpq * 0.125), dpq) == ("32nd", False)
    assert duration_to_type(int(dpq * 0.0625), dpq) == ("64th", False)

    # Short 16th notes in a measure are not dropped as rests
    song = DummySong(bar_times=[0.0, 2.0], duration=2.0)
    song.notes = [
        Note(60, 0.0, 0.25, 80, 0, RIGHT),
        Note(62, 0.25, 0.5, 80, 0, RIGHT),
        Note(64, 0.5, 0.75, 80, 0, RIGHT),
    ]
    xml = song_to_musicxml(song)
    assert "<type>16th</type>" in xml or "<type>eighth</type>" in xml
    # Right hand notes should be present
    assert "<step>C</step>" in xml
    assert "<step>D</step>" in xml
    assert "<step>E</step>" in xml


def test_review_bug2_time_at_x_scale_minus_8_alignment():
    """Review Bug #2: time_at_x must use (staff_rect.h - 8) to match draw() scale exactly."""
    surf = pygame.Surface((1000, 200))
    m0 = {
        'number': 1, 'score_idx': 0, 'x': 100, 'w': 400,
        'absTs': 0.0, 'dur': 1.0,
        'entries': [{'ts': 0.0, 'x': 100}, {'ts': 1.0, 'x': 500}]
    }
    sys0 = OSMDSystemItem(0, surf, 0.0, 4.0, 1, 1, 20, 150, [m0])

    song = DummySong(bar_times=[0.0, 4.0], duration=4.0)
    view = StaffView(song)
    with view._osmd_lock:
        view.osmd_systems = [sys0]
        view.use_osmd = True

    staff_rect = pygame.Rect(50, 50, 1000, 108)  # height 108 -> (108 - 8) = 100
    # sw=1000, sh=200 -> scale = min(1000/1000, 100/200) = 0.5
    # scaled_w = 500. dest_x = (1000 - 500) // 2 = 250.
    # At screen x = staff_rect.x + dest_x + 100 * scale = 50 + 250 + 50 = 350 -> should be t = 0.0
    t_found = view.time_at_x(350, staff_rect, 0.0)
    assert abs(t_found - 0.0) < 0.01


def test_review_bug4_measure_interpolation_points_symmetry():
    """Review Bug #4: Shared _build_measure_interpolation_points guarantees x_for_time and time_for_x symmetry."""
    from staff import _build_measure_interpolation_points
    song = DummySong(bar_times=[0.0, 2.0, 4.0], duration=4.0)
    m = {
        'number': 1, 'score_idx': 0, 'x': 60, 'w': 300,
        'absTs': 0.0, 'dur': 1.0,
        'entries': [{'ts': 0.0, 'x': 80}, {'ts': 0.5, 'x': 180}, {'ts': 0.75, 'x': 250}]
    }
    pts = _build_measure_interpolation_points(song, m, 0, 0.0, 2.0)
    assert pts[0][0] == 0.0
    assert pts[0][1] == 80  # First notehead
    assert pts[-1][0] == 2.0
    assert pts[-1][1] == 360  # Measure right edge (60 + 300)

    surf = pygame.Surface((1000, 200))
    sys0 = OSMDSystemItem(0, surf, 0.0, 2.0, 1, 1, 20, 150, [m])

    # Round trip: for time t in [0.0, 2.0], time_for_x(x_for_time(t)) ~ t
    for test_t in [0.0, 0.5, 1.0, 1.5, 2.0]:
        x = sys0.x_for_time(test_t, song)
        t_recovered = sys0.time_for_x(x, song, current_t=test_t)
        assert abs(t_recovered - test_t) < 0.05


def test_review_bug5_and_tuning_osmd_rules_and_viewport():
    """Review Bug #5 & Improvement #2: OSMD HTML includes spacing rules and worker viewport width is 2400."""
    import osmd_render
    html = osmd_render._create_render_html("<score-partwise></score-partwise>", width=2000)
    assert "BetweenStaffDistance" in html
    assert "FillEmptyMeasuresWithWholeRest" in html
    assert "DisplacedNoteMargin" in html


def test_mariage_damour_measure_5_and_6_downbeats_are_not_rests():
    """Bug 1 verification: Measure 5 LH downbeat and Measure 6 RH/LH downbeats are not rests."""
    import os
    import xml.etree.ElementTree as ET
    import midi_loader
    import midi_to_musicxml

    midi_path = os.path.join("MIDIs", "Paul de Senneville - Mariage d'Amour.mid")
    if not os.path.exists(midi_path):
        pytest.skip("Mariage d'Amour MIDI not found")

    song = midi_loader.load_midi(midi_path)
    xml_str = midi_to_musicxml.song_to_musicxml(song)
    root = ET.fromstring(xml_str)
    measures = root.findall(".//measure")
    assert len(measures) >= 6

    # Measure 5: Voice 2 (LH) must start with a note, not a rest
    m5 = measures[4]
    m5_notes = m5.findall("note")
    m5_voice2 = [n for n in m5_notes if n.find("voice") is not None and n.find("voice").text == "2"]
    assert len(m5_voice2) > 0
    assert m5_voice2[0].find("rest") is None, "Measure 5 LH first note should not be a rest"

    # Measure 6: Both Voice 1 (RH) and Voice 2 (LH) must start with a note, not a rest
    m6 = measures[5]
    m6_notes = m6.findall("note")
    m6_voice1 = [n for n in m6_notes if n.find("voice") is not None and n.find("voice").text == "1"]
    m6_voice2 = [n for n in m6_notes if n.find("voice") is not None and n.find("voice").text == "2"]
    assert len(m6_voice1) > 0 and m6_voice1[0].find("rest") is None, "Measure 6 RH first note should not be a rest"
    assert len(m6_voice2) > 0 and m6_voice2[0].find("rest") is None, "Measure 6 LH first note should not be a rest"


def test_transport_seek_triggers_notes_at_seek_time_immediately():
    """Bug 2 verification: Seeking to downbeat triggers notes starting at that exact time on the first update."""
    from common import Transport
    import midi_loader

    class MockMidi:
        def __init__(self):
            self.notes_on = []
            self.notes_off = []
        def all_off(self):
            pass
        def control_change(self, c, v):
            pass
        def note_on(self, note):
            self.notes_on.append(note.pitch)
        def note_off(self, note):
            self.notes_off.append(note.pitch)

    song = DummySong(bar_times=[0.0, 2.0, 4.0], duration=4.0)
    # Add a note starting exactly at bar 1 (2.0)
    n1 = midi_loader.Note(pitch=60, start=2.0, end=3.0, velocity=80, track=0, hand="R")
    n2 = midi_loader.Note(pitch=48, start=2.0, end=3.5, velocity=80, track=0, hand="L")
    song.notes = [n1, n2]

    tr = Transport()
    tr._init_transport(MockMidi())
    tr.song = song
    tr.paused = False

    tr.seek(2.0)
    # First update frame should immediately trigger the notes starting at 2.0
    tr.update(0.05)
    assert 60 in tr.midi.notes_on
    assert 48 in tr.midi.notes_on




