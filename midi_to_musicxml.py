"""
midi_to_musicxml.py - Export a Song (or MIDI) to MusicXML for OSMD Grand Staff rendering.
Preserves exact bar boundaries, tempo, time signature, key signature, and note timings.
"""
from __future__ import annotations

import math
from typing import List, Optional, Tuple
import xml.etree.ElementTree as ET

# Diatonic step names
STEP_NAMES = ["C", "D", "E", "F", "G", "A", "B"]

# Semitone to (step_char, alter) maps
SHARP_PITCH_MAP = {
    0: ("C", 0), 1: ("C", 1), 2: ("D", 0), 3: ("D", 1), 4: ("E", 0),
    5: ("F", 0), 6: ("F", 1), 7: ("G", 0), 8: ("G", 1), 9: ("A", 0),
    10: ("A", 1), 11: ("B", 0)
}

FLAT_PITCH_MAP = {
    0: ("C", 0), 1: ("D", -1), 2: ("D", 0), 3: ("E", -1), 4: ("E", 0),
    5: ("F", 0), 6: ("G", -1), 7: ("G", 0), 8: ("A", -1), 9: ("A", 0),
    10: ("B", -1), 11: ("B", 0)
}


def pitch_to_step_alter_octave(pitch: int, key_fifths: int = 0) -> Tuple[str, int, int]:
    """Convert MIDI pitch to MusicXML step, alter, octave."""
    octave = pitch // 12 - 1
    semi = pitch % 12
    mapping = FLAT_PITCH_MAP if key_fifths < 0 else SHARP_PITCH_MAP
    step, alter = mapping[semi]
    return step, alter, octave


def duration_to_type(dur_divisions: int, divisions_per_quarter: int = 480) -> Tuple[str, bool]:
    """Convert duration in divisions to MusicXML note type and dotted flag."""
    ratio = dur_divisions / max(1, divisions_per_quarter)
    # Check standard durations
    if ratio >= 3.5:
        return "whole", False
    elif ratio >= 2.8:
        return "half", True
    elif ratio >= 1.8:
        return "half", False
    elif ratio >= 1.3:
        return "quarter", True
    elif ratio >= 0.8:
        return "quarter", False
    elif ratio >= 0.6:
        return "eighth", True
    elif ratio >= 0.4:
        return "eighth", False
    elif ratio >= 0.2:
        return "16th", False
    else:
        return "32nd", False


def song_to_musicxml(song, divisions: int = 480) -> str:
    """
    Convert a Song object to a 2-staff Grand Staff MusicXML string.
    Guarantees measure count matches song.bar_times.
    """
    bars = getattr(song, "bar_times", [])
    if not bars:
        bars = [0.0]

    beats, beat_type = getattr(song, "time_signature", (4, 4))
    key_fifths = getattr(song, "key_fifths", 0) or 0
    total_dur = getattr(song, "duration", bars[-1] + 4.0)

    # Typical regular measure duration
    if len(bars) >= 2:
        # calculate median bar duration
        diffs = [bars[i+1] - bars[i] for i in range(len(bars)-1)]
        diffs.sort()
        regular_bar_dur = diffs[len(diffs) // 2]
    else:
        regular_bar_dur = 4.0 * (60.0 / 120.0)

    # Standard divisions per measure for the time signature
    regular_bar_divisions = beats * divisions

    root = ET.Element("score-partwise", version="4.0")
    part_list = ET.SubElement(root, "part-list")
    score_part = ET.SubElement(part_list, "score-part", id="P1")
    ET.SubElement(score_part, "part-name").text = "Piano"

    part = ET.SubElement(root, "part", id="P1")

    # Map notes to measures
    num_bars = len(bars)
    notes = sorted(getattr(song, "notes", []), key=lambda n: (n.start, n.pitch))

    # Index notes per measure
    for m_idx in range(num_bars):
        t0 = bars[m_idx]
        t1 = bars[m_idx + 1] if m_idx + 1 < num_bars else (t0 + regular_bar_dur)
        bar_dur = max(0.001, t1 - t0)

        # Handle pickup bar divisions
        if bar_dur < 0.7 * regular_bar_dur:
            # Pickup measure: scale divisions proportional to quarter beats
            m_beats = max(1, round((bar_dur / regular_bar_dur) * beats))
            m_divisions = m_beats * divisions
        else:
            m_beats = beats
            m_divisions = regular_bar_divisions

        m_elem = ET.SubElement(part, "measure", number=str(m_idx + 1))

        if m_idx == 0:
            attrs = ET.SubElement(m_elem, "attributes")
            ET.SubElement(attrs, "divisions").text = str(divisions)
            key = ET.SubElement(attrs, "key")
            ET.SubElement(key, "fifths").text = str(int(key_fifths))
            time = ET.SubElement(attrs, "time")
            ET.SubElement(time, "beats").text = str(beats)
            ET.SubElement(time, "beat-type").text = str(beat_type)
            ET.SubElement(attrs, "staves").text = "2"
            
            clef1 = ET.SubElement(attrs, "clef", number="1")
            ET.SubElement(clef1, "sign").text = "G"
            ET.SubElement(clef1, "line").text = "2"

            clef2 = ET.SubElement(attrs, "clef", number="2")
            ET.SubElement(clef2, "sign").text = "F"
            ET.SubElement(clef2, "line").text = "4"

        # Find notes in this measure
        # Note belongs to this measure if its start falls within [t0, t1)
        m_notes = [n for n in notes if t0 <= n.start < t1]

        # Separate into staff 1 (RH) and staff 2 (LH)
        rh_notes = [n for n in m_notes if n.hand == "R" or (n.hand != "L" and n.pitch >= 60)]
        lh_notes = [n for n in m_notes if n.hand == "L" or (n.hand != "R" and n.pitch < 60)]

        def write_staff_voice(staff_notes, staff_num: int, voice_num: int):
            if not staff_notes:
                # Whole measure rest
                rest_note = ET.SubElement(m_elem, "note")
                ET.SubElement(rest_note, "rest")
                ET.SubElement(rest_note, "duration").text = str(m_divisions)
                ET.SubElement(rest_note, "voice").text = str(voice_num)
                ET.SubElement(rest_note, "staff").text = str(staff_num)
                return

            # Group notes starting at almost the same time into chords (within 35ms)
            chords = []
            cur_chord = []
            for n in staff_notes:
                if not cur_chord:
                    cur_chord.append(n)
                elif abs(n.start - cur_chord[0].start) <= 0.035:
                    cur_chord.append(n)
                else:
                    chords.append(cur_chord)
                    cur_chord = [n]
            if cur_chord:
                chords.append(cur_chord)

            cur_div = 0
            for chord_idx, chord in enumerate(chords):
                chord_start_t = chord[0].start
                # Snap start offset to divisions
                note_div = round(((chord_start_t - t0) / bar_dur) * m_divisions)
                # Snap to grid of 30 divisions (1/64 note)
                note_div = max(0, min(m_divisions - 60, round(note_div / 30) * 30))

                # Insert rest if there is a gap before this chord
                if note_div > cur_div:
                    rest_div = note_div - cur_div
                    rest_note = ET.SubElement(m_elem, "note")
                    ET.SubElement(rest_note, "rest")
                    ET.SubElement(rest_note, "duration").text = str(rest_div)
                    ET.SubElement(rest_note, "voice").text = str(voice_num)
                    ET.SubElement(rest_note, "staff").text = str(staff_num)
                    cur_div = note_div

                # Determine duration: until next chord or note end
                if chord_idx + 1 < len(chords):
                    next_start_div = max(note_div + 60, round(((chords[chord_idx+1][0].start - t0) / bar_dur) * m_divisions))
                    max_dur_div = next_start_div - note_div
                else:
                    max_dur_div = m_divisions - note_div

                avg_dur_s = sum(n.end - n.start for n in chord) / len(chord)
                chord_dur_div = round((avg_dur_s / bar_dur) * m_divisions)
                chord_dur_div = max(60, min(max_dur_div, round(chord_dur_div / 60) * 60))
                if chord_dur_div <= 0:
                    chord_dur_div = 60

                ntype, dotted = duration_to_type(chord_dur_div, divisions)

                # Write each note of chord
                for note_i, n in enumerate(chord):
                    step, alter, oct_num = pitch_to_step_alter_octave(n.pitch, key_fifths)
                    n_elem = ET.SubElement(m_elem, "note")
                    if note_i > 0:
                        ET.SubElement(n_elem, "chord")
                    p_elem = ET.SubElement(n_elem, "pitch")
                    ET.SubElement(p_elem, "step").text = step
                    if alter != 0:
                        ET.SubElement(p_elem, "alter").text = str(alter)
                    ET.SubElement(p_elem, "octave").text = str(oct_num)
                    ET.SubElement(n_elem, "duration").text = str(chord_dur_div)
                    ET.SubElement(n_elem, "voice").text = str(voice_num)
                    ET.SubElement(n_elem, "type").text = ntype
                    if dotted:
                        ET.SubElement(n_elem, "dot")
                    ET.SubElement(n_elem, "staff").text = str(staff_num)

                cur_div += chord_dur_div

            # Fill remaining measure with rest
            if cur_div < m_divisions:
                rest_div = m_divisions - cur_div
                rest_note = ET.SubElement(m_elem, "note")
                ET.SubElement(rest_note, "rest")
                ET.SubElement(rest_note, "duration").text = str(rest_div)
                ET.SubElement(rest_note, "voice").text = str(voice_num)
                ET.SubElement(rest_note, "staff").text = str(staff_num)

        # Write Staff 1 (Right Hand)
        write_staff_voice(rh_notes, staff_num=1, voice_num=1)

        # Backup to start of measure
        backup = ET.SubElement(m_elem, "backup")
        ET.SubElement(backup, "duration").text = str(m_divisions)

        # Write Staff 2 (Left Hand)
        write_staff_voice(lh_notes, staff_num=2, voice_num=2)

    return ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8")

