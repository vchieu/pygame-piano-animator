"""Loading in the background: the progress bar, results and errors handed back, and every way in."""
import pygame
import pytest

from conftest import midi_path

import progress
from midi_loader import RIGHT


def test_stages_nest_and_only_count_while_tracked():
    progress.report(0.5)                       # nothing tracked: ignored
    progress.begin()
    with progress.stage(0.0, 0.5):
        progress.report(0.5)
        assert progress.value() == 0.25
        with progress.stage(0.5, 1.0):
            progress.report(0.0)
            assert progress.value() == 0.25
            progress.report(1.0)
            assert progress.value() == 0.5
    assert progress.value() == 0.5
    with progress.stage(0.5, 1.0):
        progress.report(0.2)                   # never goes back
        assert progress.value() == 0.6
    assert progress.value() == 1.0
    progress.end()
    progress.report(0.0)
    assert progress.value() == 1.0


def test_run_busy_returns_the_result_and_raises_the_error(screen):
    from common import load_fonts, run_busy
    fonts = load_fonts()
    seen = []

    def job():
        for i in range(5):
            progress.report(i / 5)
            seen.append(progress.value())
        return 42
    assert run_busy(screen, fonts, "Working…", job) == 42
    assert seen == sorted(seen) and seen[-1] == 0.8

    def bad():
        raise ValueError("no good")
    with pytest.raises(ValueError):
        run_busy(screen, fonts, "Working…", bad)
    pygame.event.post(pygame.event.Event(pygame.QUIT))
    run_busy(screen, fonts, "Working…", lambda: None)
    assert any(e.type == pygame.QUIT for e in pygame.event.get())      # closing the window waits, isn't lost


def test_a_file_loads_with_its_hands_planned(screen):
    import main
    from audio_sync import PlaybackSetup
    app = main.App(screen, sound=False)
    app.choose_playback(midi_path("demo_song.mid"))
    setup = app.mode
    assert isinstance(setup, PlaybackSetup) and setup.hands and RIGHT in setup.hands
    setup._do("default")
    v = app.mode
    assert isinstance(v, main.Visualizer) and v.hands is setup.hands           # planned once
    v.update(0.1)
    v.render()
    app.menu()
    app.choose_playback(str(midi_path("demo_song.mid")) + ".missing")
    assert isinstance(app.mode, main.MainMenu) and "Could not open" in app.mode.message


def test_the_editor_opens_another_file(screen):
    import main
    app = main.App(screen, sound=False)
    app.edit(midi_path("demo_song.mid"))
    ed = app.mode
    first = ed.song
    assert ed._load_path(midi_path("demo_song.mid"))
    assert ed.song is not first and ed.hands and len(ed.finger) == len(ed.notes)
    ed._load_path(midi_path("demo_song.mid") + ".missing")    # a message, not a crash
    ed.render()


def test_midi_triplet_detection():
    """MIDI notes whose onset timing matches 3:2 triplets are marked with tuplet_num=3 and printed shape."""
    import midi_loader
    from midi_loader import Note, RIGHT, detect_midi_tuplets

    # 3 notes in 2 beats (beat_len = 0.5s): starts 0.0, 0.333, 0.667, ends 0.33, 0.66, 1.0 -> quarter triplet
    notes = [
        Note(60, 0.000, 0.330, 64, 0, RIGHT),
        Note(62, 0.333, 0.660, 64, 0, RIGHT),
        Note(64, 0.667, 1.000, 64, 0, RIGHT),
    ]
    beat_times = [0.0, 0.5, 1.0, 1.5, 2.0]
    updated, count = detect_midi_tuplets(notes, beat_times)
    assert count == 1
    assert [n.tuplet_num for n in updated] == [3, 3, 3]
    assert [n.tuplet_type for n in updated] == ["start", None, "stop"]
    assert [n.printed for n in updated] == ["quarter", "quarter", "quarter"]

    # In khong-mon-dang-ho-doi.mid, bar 51 triplet at ~96.19s is detected
    import os
    p = midi_path("khong-mon-dang-ho-doi.mid")
    if not os.path.exists(p):
        import pytest
        pytest.skip("khong-mon-dang-ho-doi.mid not found")
    song = midi_loader.load_midi(p)
    m51 = [n for n in song.notes if 96.0 <= n.start <= 97.2 and n.hand == RIGHT and n.tuplet_num]
    assert len(m51) == 3
    assert [n.tuplet_num for n in m51] == [3, 3, 3]
    assert [n.tuplet_type for n in m51] == ["start", None, "stop"]
    assert [n.printed for n in m51] == ["quarter", "quarter", "quarter"]


def test_spell_with_key_octave_calculation():
    """spell_with_key matches diatonic_step octaves for flat and sharp key signatures."""
    import midi_loader
    # Pitch 44 is Ab2 (or G#2). Octave is 2 (2 * 7 + 5 = 19).
    step, alter = midi_loader.spell_with_key(44, -3)
    assert step == 19 and alter == -1

    # Pitch 70 is Bb4. Octave is 4 (4 * 7 + 6 = 34).
    step, alter = midi_loader.spell_with_key(70, -1)
    assert step == 34 and alter == -1

    # Pitch 60 is C4. Octave is 4 (4 * 7 + 0 = 28).
    step, alter = midi_loader.spell_with_key(60, 0)
    assert step == 28 and alter == 0


def test_a_flat_key_spells_its_black_keys_as_the_flats_it_holds():
    import midi_loader
    # Eb major holds Bb, Eb and Ab. Pitch 58 is the Bb the key calls for - B3 with a
    # flat - not an A#3 that then needs a sharp beside it.
    assert midi_loader.spell_with_key(58, -3) == (3 * 7 + 6, -1)
    # Ab major adds Db, so the D# and C# pitches are written Eb and Db.
    assert midi_loader.spell_with_key(63, -4) == (4 * 7 + 2, -1)
    assert midi_loader.spell_with_key(61, -4) == (4 * 7 + 1, -1)
    # A chromatic note the key does not alter keeps the sharp reading it always had.
    assert midi_loader.spell_with_key(61, -3) == (4 * 7 + 0, 1)


