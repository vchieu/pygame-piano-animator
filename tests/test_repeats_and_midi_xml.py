"""
tests/test_repeats_and_midi_xml.py - Unit tests for MusicXML repeat expansion and MIDI-to-MusicXML converter.
"""
import os
import xml.etree.ElementTree as ET
import pytest

from midi_loader import Note, MidiSong
from musicxml_repeats import parse_repeat_info, expand_repeats, get_musicxml_repeat_order
from midi_to_musicxml import song_to_musicxml, pitch_to_step_alter_octave, duration_to_type


def test_pitch_conversion():
    # C4 = MIDI 60
    step, alter, oct_num = pitch_to_step_alter_octave(60, key_fifths=0)
    assert (step, alter, oct_num) == ("C", 0, 4)

    # F#4 = MIDI 66 in sharp keys
    step, alter, oct_num = pitch_to_step_alter_octave(66, key_fifths=1)
    assert (step, alter, oct_num) == ("F", 1, 4)

    # Bb4 = MIDI 70 in flat keys
    step, alter, oct_num = pitch_to_step_alter_octave(70, key_fifths=-1)
    assert (step, alter, oct_num) == ("B", -1, 4)


def test_duration_to_type():
    ntype, dot = duration_to_type(480, 480)
    assert ntype == "quarter" and not dot

    ntype, dot = duration_to_type(720, 480)
    assert ntype == "quarter" and dot

    ntype, dot = duration_to_type(960, 480)
    assert ntype == "half" and not dot

    ntype, dot = duration_to_type(240, 480)
    assert ntype == "eighth" and not dot


def test_repeat_expansion_simple():
    # 4 measures: measure 1 to 2, repeat backward at 2, then measures 3, 4
    info = [
        dict(fwd=False, bwd=False, times=2, end_start=None, end_stop=False,
             segno=None, coda=None, dacapo=False, dalsegno=None, tocoda=None, fine=False),
        dict(fwd=False, bwd=True, times=2, end_start=None, end_stop=False,
             segno=None, coda=None, dacapo=False, dalsegno=None, tocoda=None, fine=False),
        dict(fwd=False, bwd=False, times=2, end_start=None, end_stop=False,
             segno=None, coda=None, dacapo=False, dalsegno=None, tocoda=None, fine=False),
        dict(fwd=False, bwd=False, times=2, end_start=None, end_stop=False,
             segno=None, coda=None, dacapo=False, dalsegno=None, tocoda=None, fine=False),
    ]
    order, ending_pass = expand_repeats(info)
    # Played: 0, 1, 0, 1, 2, 3
    assert order == [0, 1, 0, 1, 2, 3]


def test_repeat_expansion_voltas():
    # Measures:
    # 0: intro
    # 1: start of repeat section
    # 2: ending 1 (repeats back)
    # 3: ending 2
    # 4: outro
    info = [
        dict(fwd=False, bwd=False, times=2, end_start=None, end_stop=False,
             segno=None, coda=None, dacapo=False, dalsegno=None, tocoda=None, fine=False),
        dict(fwd=True, bwd=False, times=2, end_start=None, end_stop=False,
             segno=None, coda=None, dacapo=False, dalsegno=None, tocoda=None, fine=False),
        dict(fwd=False, bwd=True, times=2, end_start=[1], end_stop=True,
             segno=None, coda=None, dacapo=False, dalsegno=None, tocoda=None, fine=False),
        dict(fwd=False, bwd=False, times=2, end_start=[2], end_stop=True,
             segno=None, coda=None, dacapo=False, dalsegno=None, tocoda=None, fine=False),
        dict(fwd=False, bwd=False, times=2, end_start=None, end_stop=False,
             segno=None, coda=None, dacapo=False, dalsegno=None, tocoda=None, fine=False),
    ]
    order, ending_pass = expand_repeats(info)
    # Played: 0, 1, 2, 1, 3, 4
    assert order == [0, 1, 2, 1, 3, 4]


def test_song_to_musicxml():
    notes = [
        Note(pitch=60, start=0.0, end=1.0, velocity=80, track=0, hand="R"),
        Note(pitch=64, start=1.0, end=2.0, velocity=80, track=0, hand="R"),
        Note(pitch=48, start=0.0, end=2.0, velocity=80, track=1, hand="L"),
        Note(pitch=67, start=2.0, end=3.0, velocity=80, track=0, hand="R"),
        Note(pitch=52, start=2.0, end=4.0, velocity=80, track=1, hand="L"),
    ]
    bar_times = [0.0, 2.0]
    song = MidiSong(
        notes=notes,
        tracks=[],
        duration=4.0,
        bar_times=bar_times,
        beat_times=[0.0, 1.0, 2.0, 3.0],
        time_signature=(2, 4),
        key_fifths=0,
    )
    xml_str = song_to_musicxml(song)
    root = ET.fromstring(xml_str)
    part = root.find("part")
    measures = part.findall("measure")
    # Must have exactly 2 measures matching bar_times
    assert len(measures) == 2
    # Verify staves tag is 2
    attrs = measures[0].find("attributes")
    assert attrs.find("staves").text == "2"

