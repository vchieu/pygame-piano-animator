"""
midi_to_musicxml.py - Export a Song (or MIDI) to MusicXML for OSMD Grand Staff rendering.
Preserves exact bar boundaries, tempo, time signature(s), key signature, and note timings.

Time signatures
---------------
`song.bar_sigs` (optional) is a list with one (beats, beat_type) per bar, e.g.
[(4, 4), (4, 4), (5, 4), (3, 8), (4, 4)]. Every change is written as a <time> element at the
first bar it applies to. Without `bar_sigs`, `song.time_signature` applies to every bar (old behaviour,
including the pickup-bar heuristic).

For beat types longer than a quarter note's division (x/8, x/16 ...) the measure uses more divisions
per quarter so that a note's <type> stays correct: x/8 -> 2 * divisions, x/16 -> 4 * divisions.
A <divisions> element is written whenever that changes.

`song.bar_end` (optional) is the time (s) where the last bar ends; without it the last bar is as long
as the other bars (per quarter note).
"""
from __future__ import annotations

from typing import Tuple
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
    if ratio >= 5.5:
        return "whole", True
    elif ratio >= 3.5:
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


def _div_per_quarter(beat_type: int, divisions: int) -> int:
    """Divisions per quarter note used by a measure of the given beat type."""
    if beat_type > 4 and (beat_type & (beat_type - 1)) == 0:
        return divisions * (beat_type // 4)
    return divisions


def _bar_divisions(beats: int, beat_type: int, dpq: int) -> int:
    """Length of a bar in divisions (dpq divisions per quarter note)."""
    return max(1, int(round(beats * 4 * dpq / max(1, beat_type))))


def _bar_signatures(song, num_bars: int):
    """([(beats, beat_type)] per bar, explicit?) - explicit means the song gave one per bar."""
    sigs = getattr(song, "bar_sigs", None)
    if sigs:
        out = [(int(b), int(t)) for b, t in sigs[:num_bars]]
        while len(out) < num_bars:
            out.append(out[-1])
        return out, True
    beats, beat_type = getattr(song, "time_signature", (4, 4))
    return [(int(beats), int(beat_type))] * num_bars, False


def song_to_musicxml(song, divisions: int = 480) -> str:
    """
    Convert a Song object to a 2-staff Grand Staff MusicXML string.
    Guarantees measure count matches song.bar_times; each measure has the time signature
    given by song.bar_sigs (or song.time_signature).
    """
    bars = list(getattr(song, "bar_times", []))
    if not bars:
        bars = [0.0]
    num_bars = len(bars)

    sigs, explicit = _bar_signatures(song, num_bars)
    key_fifths = getattr(song, "key_fifths", 0) or 0
    bar_end = getattr(song, "bar_end", None)

    # Seconds per quarter note (to size the last bar) and, for the legacy path, a regular bar length
    quarters = [b * 4 / t for b, t in sigs]
    if num_bars >= 2:
        diffs = sorted(bars[i + 1] - bars[i] for i in range(num_bars - 1))
        regular_bar_dur = diffs[len(diffs) // 2]
        if explicit:
            ratios = sorted((bars[i + 1] - bars[i]) / max(1e-9, quarters[i]) for i in range(num_bars - 1))
            sec_per_quarter = ratios[len(ratios) // 2]
        else:
            sec_per_quarter = regular_bar_dur / max(1e-9, quarters[0])
    else:
        sec_per_quarter = 0.5
        regular_bar_dur = quarters[0] * sec_per_quarter

    root = ET.Element("score-partwise", version="4.0")
    part_list = ET.SubElement(root, "part-list")
    score_part = ET.SubElement(part_list, "score-part", id="P1")
    ET.SubElement(score_part, "part-name").text = "Piano"

    part = ET.SubElement(root, "part", id="P1")

    notes = sorted(getattr(song, "notes", []), key=lambda n: (n.start, n.pitch))

    prev_sig = None
    prev_dpq = None

    for m_idx in range(num_bars):
        beats, beat_type = sigs[m_idx]
        dpq = _div_per_quarter(beat_type, divisions)
        m_divisions = _bar_divisions(beats, beat_type, dpq)

        t0 = bars[m_idx]
        if m_idx + 1 < num_bars:
            t1 = bars[m_idx + 1]
        elif bar_end is not None and bar_end > t0:
            t1 = bar_end
        else:
            t1 = t0 + quarters[m_idx] * sec_per_quarter
        bar_dur = max(0.001, t1 - t0)

        # Legacy path only (no per-bar signatures): a short bar is a pickup measure
        if not explicit and bar_dur < 0.7 * regular_bar_dur:
            m_beats = max(1, round((bar_dur / regular_bar_dur) * beats))
            m_divisions = m_beats * dpq

        min_div = max(1, dpq // 8)      # shortest written value: a 32nd note
        grid_div = max(1, dpq // 16)    # positions snap to a 64th note

        m_elem = ET.SubElement(part, "measure", number=str(m_idx + 1))

        if m_idx == 0:
            attrs = ET.SubElement(m_elem, "attributes")
            ET.SubElement(attrs, "divisions").text = str(dpq)
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
        elif (beats, beat_type) != prev_sig or dpq != prev_dpq:
            attrs = ET.SubElement(m_elem, "attributes")
            if dpq != prev_dpq:
                ET.SubElement(attrs, "divisions").text = str(dpq)
            if (beats, beat_type) != prev_sig:
                time = ET.SubElement(attrs, "time")
                ET.SubElement(time, "beats").text = str(beats)
                ET.SubElement(time, "beat-type").text = str(beat_type)
        prev_sig = (beats, beat_type)
        prev_dpq = dpq

        # Notes belong to this measure if their start falls within [t0, t1)
        m_notes = [n for n in notes if t0 <= n.start < t1]

        # Separate into staff 1 (RH) and staff 2 (LH)
        rh_notes = [n for n in m_notes if n.hand == "R" or (n.hand != "L" and n.pitch >= 60)]
        lh_notes = [n for n in m_notes if n.hand == "L" or (n.hand != "R" and n.pitch < 60)]

        def snap(t: float) -> int:
            """Time (s) -> position in this measure, in divisions, on the 64th-note grid."""
            d = round(((t - t0) / bar_dur) * m_divisions)
            return int(round(d / grid_div)) * grid_div

        def write_rest(duration: int, voice_num: int, staff_num: int, whole: bool = False):
            rest_note = ET.SubElement(m_elem, "note")
            rest = ET.SubElement(rest_note, "rest")
            if whole:
                rest.set("measure", "yes")
            ET.SubElement(rest_note, "duration").text = str(duration)
            ET.SubElement(rest_note, "voice").text = str(voice_num)
            ET.SubElement(rest_note, "staff").text = str(staff_num)

        def write_staff_voice(staff_notes, staff_num: int, voice_num: int):
            if not staff_notes:
                write_rest(m_divisions, voice_num, staff_num, whole=True)
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
                note_div = max(cur_div, snap(chord[0].start))
                if note_div + min_div > m_divisions:
                    break                       # no room left in this measure

                # Insert rest if there is a gap before this chord
                if note_div > cur_div:
                    write_rest(note_div - cur_div, voice_num, staff_num)
                    cur_div = note_div

                # Duration: until the next chord (never past the bar) or the note's own length
                if chord_idx + 1 < len(chords):
                    next_start = max(note_div + min_div, snap(chords[chord_idx + 1][0].start))
                else:
                    next_start = m_divisions
                max_dur_div = min(next_start, m_divisions) - note_div

                avg_dur_s = sum(n.end - n.start for n in chord) / len(chord)
                chord_dur_div = round((avg_dur_s / bar_dur) * m_divisions)
                chord_dur_div = int(round(chord_dur_div / min_div)) * min_div
                chord_dur_div = max(min_div, min(max_dur_div, chord_dur_div))

                ntype, dotted = duration_to_type(chord_dur_div, dpq)

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
                write_rest(m_divisions - cur_div, voice_num, staff_num)

        # Write Staff 1 (Right Hand)
        write_staff_voice(rh_notes, staff_num=1, voice_num=1)

        # Backup to start of measure
        backup = ET.SubElement(m_elem, "backup")
        ET.SubElement(backup, "duration").text = str(m_divisions)

        # Write Staff 2 (Left Hand)
        write_staff_voice(lh_notes, staff_num=2, voice_num=2)

    return ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8")
