"""
musicxml_loader.py - Load a MusicXML score (.xml / .musicxml / .mxl) into a MidiSong.

MusicXML is what notation software exchanges: it says exactly what the score
says, where a MIDI file only says what it sounds like. So a score read here
carries, besides the pitches and times MIDI gives:

- which hand plays a note - <staff>1</staff> is the right hand, <staff>2</staff>
  the left - instead of hand_split.py having to work it out;
- the written spelling: C# is step C with <alter>1</alter>, Db is step D with
  <alter>-1</alter>, so the staff prints the one the composer wrote;
- the written note value (<type>quarter</type>, <dot/>), not one guessed from
  how many seconds the note lasts;
- the printed fingering, where the score has one (many leave the easy bars
  blank);
- the repeats, voltas and jumps played out, so what is heard is what the score
  says.

Only the standard library is used (zipfile for .mxl, xml.etree for the XML), so
nothing has to be added to the build.

Typical use:

    from musicxml_loader import load_musicxml
    song = load_musicxml("score.mxl")

`midi_loader.load_song` dispatches by extension; that is what the app calls.
"""
from __future__ import annotations

import os
import re
import zipfile
from dataclasses import replace
from typing import Dict, List, Optional, Tuple

import xml.etree.ElementTree as ET

import progress
from midi_loader import (LEFT, RIGHT, MIDDLE_C, Note, TrackInfo, MidiSong, _fit_to_piano,
                         sanitize_notes)

MUSICXML_EXTENSIONS = (".xml", ".musicxml", ".mxl")

# MIDI number of each written step, before <alter>.
STEP_SEMITONES = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}

# <type> -> the name notation.note_shape and staff.py use for that value.
TYPE_VALUES = {
    "whole": "whole", "half": "half", "quarter": "quarter", "eighth": "eighth",
    "16th": "sixteenth", "32nd": "thirty_second", "64th": "sixty_fourth",
    "breve": "whole", "long": "whole",
}

# <dynamics> text -> velocity, for a score that names one (p, mf, f...).
DYNAMICS = {"pppp": 20, "ppp": 28, "pp": 36, "p": 45, "mp": 56, "mf": 68,
            "f": 82, "ff": 96, "fff": 108, "ffff": 118, "sf": 94, "sfz": 100,
            "fp": 78, "rf": 90, "rfz": 100}

DEFAULT_VELOCITY = 64
GRACE_DURATION = 0.06      # s, a grace note has no <duration>: it is written, not measured
GRACE_STEP = 0.05          # s, how far back from its main note each grace of a run is placed
MAX_PASSES = 256           # guard against a score whose repeat signs would never settle
# A tied note's two written halves meet at one moment. Sanitizing a very short note
# may lengthen it to its minimum, so allow that much overlap when pairing the halves.
TIE_MEET_T = 0.06          # s


def is_musicxml(path: str) -> bool:
    """True for a MusicXML score, by extension."""
    return os.path.splitext(path or "")[1].lower() in MUSICXML_EXTENSIONS


# --------------------------------------------------------------------------- #
# Reading the XML
# --------------------------------------------------------------------------- #

def _local(tag) -> str:
    """A tag with its namespace or prefix stripped ('{uri}note' / 'm:note' -> 'note')."""
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def read_root(path: str) -> ET.Element:
    """
    The <score-partwise> root element of `path`.

    A .mxl is a zip: META-INF/container.xml names the score inside it; without
    that, the first .xml entry not under META-INF (MuseScore writes score.xml).
    A plain .xml / .musicxml is read as it is.
    """
    if os.path.splitext(path)[1].lower() == ".mxl":
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            rootfile = None
            try:
                container = ET.fromstring(z.read("META-INF/container.xml"))
                rf = container.find(".//rootfile")
                if rf is not None:
                    rootfile = rf.attrib.get("full-path")
            except (KeyError, ET.ParseError):
                rootfile = None
            if not rootfile or rootfile not in names:
                rootfile = next((n for n in names
                                 if n.lower().endswith((".xml", ".musicxml"))
                                 and not n.replace("\\", "/").startswith("META-INF")), None)
            if rootfile is None:
                raise ValueError("the .mxl holds no score")
            data = z.read(rootfile)
    else:
        with open(path, "rb") as fh:
            data = fh.read()
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValueError(f"not readable as MusicXML ({exc})")
    tag = _local(root.tag)
    if tag == "opus":
        raise ValueError("this is an <opus> (a list of scores), not a score")
    if tag not in ("score-partwise", "score-timewise"):
        raise ValueError(f"not a MusicXML score (root <{tag}>)")
    return root


def _kids(elem, tag: str):
    return [c for c in elem if _local(c.tag) == tag]


def _kid(elem, tag: str):
    for c in elem:
        if _local(c.tag) == tag:
            return c
    return None


def _text(elem, tag: str, default=None) -> Optional[str]:
    c = _kid(elem, tag) if elem is not None else None
    if c is None or c.text is None:
        return default
    return c.text.strip()


def _num(elem, tag: str, default=None) -> Optional[float]:
    v = _text(elem, tag)
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _int(elem, tag: str, default=None) -> Optional[int]:
    v = _num(elem, tag)
    return int(v) if v is not None else default


def _attrs_int(elem, name: str, default: int) -> int:
    try:
        return max(1, int(elem.attrib.get(name, default)))
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# The score's own order: repeats, voltas, and written-out jumps
# --------------------------------------------------------------------------- #

class _Measure:
    """One measure of one part, with the signs that sit on its bar lines."""

    __slots__ = ("elem", "number", "forward_repeat", "backward_repeat", "backward_times",
                 "endings", "segno", "coda", "dacapo", "dalsegno", "tocoda", "fine")

    def __init__(self, elem):
        self.elem = elem
        self.number = elem.attrib.get("number", "")
        self.forward_repeat = False
        self.backward_repeat = False
        self.backward_times = 2
        self.endings: List[int] = []      # the volta numbers this measure is part of
        self.segno = self.coda = False
        self.dacapo = self.dalsegno = self.tocoda = self.fine = False
        for bl in _kids(elem, "barline"):
            for rep in _kids(bl, "repeat"):
                if rep.attrib.get("direction") == "forward":
                    self.forward_repeat = True
                elif rep.attrib.get("direction") == "backward":
                    self.backward_repeat = True
                    self.backward_times = _attrs_int(rep, "times", 2)
            for end in _kids(bl, "ending"):
                nums = re.findall(r"\d+", end.attrib.get("number", "") or "")
                if end.attrib.get("type") in ("start", "discontinue") and nums:
                    self.endings = [int(x) for x in nums]
        for direction in _kids(elem, "direction"):
            if _kid(direction, "segno") is not None:
                self.segno = True
            if _kid(direction, "coda") is not None:
                self.coda = True
            sound = _kid(direction, "sound")
            if sound is not None:
                if str(sound.attrib.get("dacapo", "")).lower() in ("yes", "true"):
                    self.dacapo = True
                if sound.attrib.get("dalsegno"):
                    self.dalsegno = True
                if str(sound.attrib.get("tocoda", "")).lower() in ("yes", "true"):
                    self.tocoda = True
                if str(sound.attrib.get("fine", "")).lower() in ("yes", "true"):
                    self.fine = True


def _first(measures: List[_Measure], mark: str) -> Optional[int]:
    return next((k for k, m in enumerate(measures) if getattr(m, mark)), None)


def play_order(measures: List[_Measure], report: List[str]) -> List[int]:
    """
    The measure indices in the order the score says they are played.

    Repeated sections (a `<repeat direction="forward">` up to a
    `<repeat direction="backward" times="n">`, `n` passes - 2 by default),
    voltas (a measure whose `<ending number="k">` is played only on pass `k`)
    and written-out jumps (D.C. al Fine, D.S., al Coda, with the Segno and Coda
    marks) are followed.

    Nothing is guessed. A sign naming a mark the score does not have (an al
    Coda with no `<coda>`) is left out and `report` says so, and the unrolling
    stops rather than looping forever if the signs never settle. A score with
    none of these signs comes back as 0, 1, 2, ... - its measures in order.
    """
    n = len(measures)
    if not n:
        return []
    order: List[int] = []
    segno_at = _first(measures, "segno")
    coda_at = _first(measures, "coda")
    fine_at = _first(measures, "fine")
    has_jump = any(m.dacapo or m.dalsegno for m in measures)
    i = 0
    repeat_lo = 0          # where the forward repeat of the open section sends us
    pass_no = 0            # which pass of that section we are on (0 = first)
    jumps = 0
    took = set()           # the jump signs already followed
    total = 0
    while 0 <= i < n:
        total += 1
        if total > n * MAX_PASSES:
            report.append("stopped unrolling repeats after %d measures" % (n * MAX_PASSES))
            break
        m = measures[i]
        if m.forward_repeat and pass_no == 0 and not took:
            repeat_lo = i
        # voltas: a measure that names endings is played only on those passes
        if not m.endings or (pass_no + 1) in m.endings:
            order.append(i)
        # A jump is followed at the measure that asks for it, once. It ends the
        # section, so the repeat count starts over.
        jump_to = None
        if m.dacapo and "dacapo" not in took:
            took.add("dacapo")
            jumps += 1
            jump_to = fine_at if (fine_at is not None and has_jump) else 0
        elif m.dalsegno and "dalsegno" not in took:
            if segno_at is None:
                report.append("a D.S. with no segno was left out")
            else:
                took.add("dalsegno")
                jumps += 1
                jump_to = segno_at
        elif m.tocoda and "tocoda" not in took:
            if coda_at is None:
                report.append("an al Coda with no coda was left out")
            else:
                took.add("tocoda")
                jumps += 1
                jump_to = coda_at
        if jump_to is not None:
            pass_no, repeat_lo = 0, 0
            if jumps > MAX_PASSES or jump_to >= n:
                break
            if jump_to == i:
                break                       # a sign pointing at itself: give up
            i = jump_to
            continue
        # a Fine closes the piece, but only on a pass a jump sent us on
        if m.fine and took:
            break
        if m.backward_repeat:
            pass_no += 1
            if pass_no < m.backward_times:
                i = repeat_lo
                continue
            pass_no = 0
        i += 1
    return order


# --------------------------------------------------------------------------- #
# Tempo: a position in divisions -> seconds
# --------------------------------------------------------------------------- #

class _Tempo:
    """
    Turns a position in divisions into seconds, following the score's tempo.

    A <divisions> says how many ticks make a quarter note; a <sound tempo="120">
    or a <metronome><per-minute> says how fast - and both can change from
    measure to measure, so the map is kept as it is built.
    """

    def __init__(self):
        self.bpm = 120.0
        self.divisions = 1
        # (division position, seconds there, bpm from there)
        self.points = [(0.0, 0.0, 120.0)]

    def sec(self, div: float) -> float:
        d, s, bpm = self.points[-1]
        return s + (div - d) * 60.0 / bpm / max(1, self.divisions)

    def restart_here(self, div: float):
        """Continue the map from `div` with the current tempo and divisions."""
        s = self.sec(div)
        if self.points and self.points[-1][0] == div:
            self.points[-1] = (div, s, self.bpm)
        else:
            self.points.append((div, s, self.bpm))

    def set_tempo(self, bpm: float, div: float):
        if bpm and bpm > 0 and abs(bpm - self.bpm) > 1e-9:
            self.bpm = float(bpm)
            self.restart_here(div)

    def set_divisions(self, divisions: int, div: float):
        if divisions and divisions > 0 and divisions != self.divisions:
            self.restart_here(div)          # calculate using OLD divisions
            self.divisions = int(divisions)


def _apply_tempo(measure, at_div: float, tempo: _Tempo):
    """Take the tempo a measure's <sound tempo> / <metronome> asks for."""
    for direction in _kids(measure, "direction"):
        met = _kid(direction, "metronome")
        if met is not None:
            per = _num(met, "per-minute")
            if per:
                tempo.set_tempo(per, at_div)
        sound = _kid(direction, "sound")
        if sound is not None:
            t = sound.attrib.get("tempo")
            if t:
                try:
                    tempo.set_tempo(float(t), at_div)
                except (TypeError, ValueError):
                    pass


# --------------------------------------------------------------------------- #
# Reading notes
# --------------------------------------------------------------------------- #

def _velocity(note, dynamics: int) -> int:
    v = _num(note, "velocity")
    if v:
        return max(1, min(127, int(v)))
    d = _text(note, "dynamics")
    if d and d.lower() in DYNAMICS:
        return DYNAMICS[d.lower()]
    return dynamics


def _printed_value(note) -> Optional[str]:
    """The written note value (<type> plus any <dot>) as staff.py names it, or None."""
    name = TYPE_VALUES.get((_text(note, "type") or "").lower())
    if not name:
        return None
    dots = len(_kids(note, "dot"))
    if dots == 0:
        d = _num(note, "dot")
        dots = 1 if d else 0
    if dots == 1:
        return "dotted_" + name
    if dots >= 2:
        return "double_dotted_" + name
    return name


def _fingering(note) -> Optional[int]:
    """The printed fingering of a note, when it is 1..5."""
    notations = _kid(note, "notations")
    if notations is None:
        return None
    tech = _kid(notations, "technical")
    if tech is None:
        return None
    for f in _kids(tech, "fingering"):
        try:
            v = int((f.text or "").strip())
        except ValueError:
            v = 0
        if 1 <= v <= 5:
            return v
    return None


def _tie(note) -> tuple:
    """(starts_a_tie, ends_a_tie): a score may write <tie> or <notations><tied>."""
    starts = ends = False
    for t in _kids(note, "tie"):
        if t.attrib.get("type") == "start":
            starts = True
        elif t.attrib.get("type") == "stop":
            ends = True
    notations = _kid(note, "notations")
    if notations is not None:
        for t in _kids(notations, "tied"):
            if t.attrib.get("type") == "start":
                starts = True
            elif t.attrib.get("type") == "stop":
                ends = True
    return starts, ends


def _pair_ties(notes: List[Note]) -> List[Tuple[Note, Note]]:
    """The scored ties of `notes` as [(left, right)], once the note list is final."""
    ties: List[Tuple[Note, Note]] = []
    pending = {}                     # (part, voice, pitch) -> notes still waiting for their stop
    for n in sorted(notes, key=lambda n: (n.start, n.end, n.pitch)):
        waiting = pending.setdefault((n.track, n.voice, n.pitch), [])
        if n.tie in ("stop", "continue"):
            # The stop belongs to the latest open note of that part, voice and pitch
            # that reaches it - the voice keeps two equal pitches on different staves
            # apart, and a malformed score still pairs the nearest one.
            # The two halves of a tie meet - after sanitizing a very short tied note
            # may have been lengthened to its minimum, so allow that much overlap.
            for i in range(len(waiting) - 1, -1, -1):
                prev = waiting[i]
                if prev.start < n.start <= prev.end + TIE_MEET_T:
                    del waiting[i]
                    ties.append((prev, n))
                    break
        if n.tie in ("start", "continue"):
            waiting.append(n)
    return ties


def _tuplet(note) -> tuple:
    """
    (tuplet_num, tuplet_type): the tuplet this note is written in, when the score
    wrote one.

    <time-modification><actual-notes> says how many written notes take the place
    of how many (3 in a triplet, against <normal-notes> 2), and it is on every
    note of the group; <notations><tuplet type="start"> / type="stop"> marks the
    notes that open and close the group. A note with the number but no mark is
    one in the middle, so its tuplet_type is None.
    """
    num = _int(_kid(note, "time-modification"), "actual-notes")
    kind = None
    notations = _kid(note, "notations")
    if notations is not None:
        for t in _kids(notations, "tuplet"):
            typ = t.attrib.get("type")
            if typ in ("start", "stop"):
                kind = typ
                break
    return num, kind


def _mean_pitch(part) -> Optional[float]:
    """The average written pitch of a part (its register), for the part-order rule."""
    ps = []
    for m in _kids(part, "measure"):
        for note in _kids(m, "note"):
            p = _kid(note, "pitch")
            if p is None:
                continue
            step = (_text(p, "step") or "").upper()
            octave = _int(p, "octave")
            if step in STEP_SEMITONES and octave is not None:
                ps.append((octave + 1) * 12 + STEP_SEMITONES[step] + (_int(p, "alter", 0) or 0))
    return sum(ps) / len(ps) if ps else None


def _part_hands(parts) -> List[Optional[str]]:
    """
    The hand of each part, when the parts' own registers say it.

    A single part holding a whole piano is None (hand_split.py decides). With
    two or more parts, the highest sits in the right hand, the lowest in the
    left; a part in between, or one with nothing to compare, is None.
    """
    if len(parts) < 2:
        return [None] * len(parts)
    means = [_mean_pitch(p) for p in parts]
    known = [m for m in means if m is not None]
    if len(known) < 2:
        return [None] * len(parts)
    hi, lo = max(known), min(known)
    out: List[Optional[str]] = []
    for m in means:
        if m is None:
            out.append(None)
        elif m == hi:
            out.append(RIGHT)
        elif m == lo:
            out.append(LEFT)
        else:
            out.append(None)
    return out


def _key_fifths(attributes) -> Optional[int]:
    """
    The key signature an `<attributes>` block declares: how many sharps (positive)
    or flats (negative) the key holds. `<key><fifths>4</fifths>` is E major.
    """
    key = _kid(attributes, "key")
    if key is None:
        return None
    f = _int(key, "fifths")
    return None if f is None else max(-7, min(7, int(f)))


def _read_all_parts(parts, order: List[int], hands: List[Optional[str]],
                    notes: List[Note],
                    staff_from_file: bool = True,
                    controls: Optional[List[Tuple[float, int, int]]] = None) -> tuple:
    """
    Read every part together, measure by measure, in playing order.

    The tempo and the divisions are the score's, not one part's: a score writes
    a `<sound tempo>` once, usually in the first part, and it holds for all of
    them. So the map is shared, advanced once per measure of the order, and each
    part's measure at that index is read into `notes` at that moment. A measure
    played twice (a repeat) is read twice, so the same bar comes out again at its
    new times. Returns when the music ends, in seconds, and the key the score
    declares: (duration, key_fifths, key_changes, controls).
    """
    if controls is None:
        controls = []
    part_measures = [[_Measure(m) for m in _kids(p, "measure")] for p in parts]
    tempo = _Tempo()
    at = 0.0            # the running position in seconds
    div = 0             # the running position in divisions
    key_fifths = 0      # the key the piece starts in
    current = 0         # the key in force
    key_seen = False    # whether the score has declared a key yet
    key_changes: List[Tuple[float, int]] = []
    for idx in order:
        present = [its[idx].elem for its in part_measures if idx < len(its)]
        if not present:
            continue
        # The tempo and division signs of this measure, wherever they are written
        for measure in present:
            _apply_tempo(measure, div, tempo)
            for attributes in _kids(measure, "attributes"):
                d = _int(attributes, "divisions")
                if d:
                    tempo.set_divisions(d, div)
                # The key signature: a grand staff writes it in both parts, so only
                # a key that differs from the one in force is a change.
                fifths = _key_fifths(attributes)
                if fifths is not None:
                    if not key_seen:
                        key_fifths = fifths
                        key_seen = True
                    elif fifths != current:
                        key_changes.append((at, fifths))
                    current = fifths
        length = 0.0
        for i, its in enumerate(part_measures):
            if idx >= len(its):
                continue
            length = max(length, _read_measure(its[idx].elem, tempo, div, at, i,
                                               hands[i], notes, staff_from_file,
                                               controls=controls))
        div += _measure_span(part_measures, idx, tempo.divisions)
        at += length
    return at, key_fifths, key_changes, controls


def _measure_span(part_measures, idx: int, divisions: int) -> int:
    """How many divisions measure `idx` spans: the longest of the parts that have it."""
    return max((_measure_divisions(its[idx].elem, divisions)
                for its in part_measures if idx < len(its)), default=0)


def _measure_divisions(measure, divisions: int) -> int:
    """How far a measure spans in divisions: the largest cursor any voice reaches."""
    cur = 0
    hi = 0
    for c in measure:
        tag = _local(c.tag)
        if tag == "attributes":
            d = _int(c, "divisions")
            if d:
                divisions = d
        elif tag in ("backup", "forward"):
            n = _num(c, "duration") or 0
            cur += -n if tag == "backup" else n
        elif tag == "note":
            if _kid(c, "grace") is not None or _kid(c, "chord") is not None:
                continue
            cur += _num(c, "duration") or 0
            hi = max(hi, cur)
    if hi == 0:
        # nothing sounding: fall back to the time signature (4/4 when absent)
        beats, beat_type = 4, 4
        for attributes in _kids(measure, "attributes"):
            time = _kid(attributes, "time")
            if time is not None:
                beats = int(_num(time, "beats", 4) or 4)
                beat_type = int(_num(time, "beat-type", 4) or 4)
        hi = int(round(beats * (4.0 / beat_type) * divisions))
    return int(hi)


def _first_measure_pickup(part_measures, order: List[int]) -> bool:
    """
    Whether the first measure played is a pickup (anacrusis).

    MusicXML says so exactly: `implicit="yes"` (MuseScore writes it), measure
    `number="0"`, or a first measure shorter than its own time signature - the
    usual way a score spells an anacrusis as a full-numbered measure with fewer
    beats. `expected` is the measure's time signature converted to divisions;
    the span is what the measure really holds (`_measure_span`).
    """
    if not order:
        return False
    idx = order[0]
    present = [its[idx] for its in part_measures if idx < len(its)]
    if not present:
        return False
    for measure in present:
        if str(measure.elem.attrib.get("implicit", "")).lower() in ("yes", "true"):
            return True
        if (measure.number or "").strip() == "0":
            return True

    beats, beat_type, divisions = 4, 4, 1
    for measure in present:
        for attributes in _kids(measure.elem, "attributes"):
            d = _int(attributes, "divisions")
            if d:
                divisions = d
            time = _kid(attributes, "time")
            if time is not None:
                beats = int(_num(time, "beats", beats) or beats)
                beat_type = int(_num(time, "beat-type", beat_type) or beat_type)
    expected = beats * (4.0 / beat_type) * divisions
    span = _measure_span(part_measures, idx, divisions)
    return 0 < span < expected


def _pedal_value(text: str) -> int:
    """A MusicXML pedal value ("yes"/"no", "on"/"off", or a 0-100 position) as a switch value."""
    v = text.strip().lower()
    try:
        return 127 if float(v) >= 50 else 0
    except ValueError:
        return 127 if v in ("yes", "true", "on") else 0


def _pedal_events(direction, t: float) -> List[Tuple[float, int, int]]:
    """Parse <pedal type="...">, <sound damper-pedal="...">, and <words>Ped.</words>."""
    out = []

    def add_event(time: float, cc: int, val: int):
        ev = (time, cc, val)
        if ev not in out:
            out.append(ev)

    for dt in _kids(direction, "direction-type"):
        for ped in _kids(dt, "pedal"):
            ptype = (ped.attrib.get("type") or "").lower()
            if ptype in ("start", "continue", "resume"):
                add_event(t, 64, 127)
            elif ptype in ("stop", "discontinue"):
                add_event(t, 64, 0)
            elif ptype == "change":
                add_event(t, 64, 0)
                add_event(t + 0.03, 64, 127)
        for w in _kids(dt, "words"):
            txt = (w.text or "").strip().lower()
            if txt in ("ped.", "ped", "pedal", "pedal on", "pedal down"):
                add_event(t, 64, 127)
            elif txt in ("*", "pedal off", "pedal up", "stop pedal"):
                add_event(t, 64, 0)

    sound = _kid(direction, "sound")
    if sound is not None:
        # damper-pedal="40" means half down: the switch reads down from 50 up.
        for attr, cc in (("damper-pedal", 64), ("sostenuto-pedal", 66), ("soft-pedal", 67)):
            if attr in sound.attrib:
                add_event(t, cc, _pedal_value(sound.attrib[attr]))
    return out


def _read_measure(measure, tempo: _Tempo, start_div: int, at: float,
                  part_index: int, hand: Optional[str],
                  notes: List[Note], staff_from_file: bool = True,
                  controls: Optional[List[Tuple[float, int, int]]] = None) -> float:
    """
    Read one measure's notes into `notes`; returns its length in seconds.

    Notes sit at a cursor in divisions; <backup> and <forward> move it (that is
    how one measure holds several voices); a <chord> note starts with the one
    before it; a <grace> note takes no time. A tie running into the next note of
    the same pitch merges the two, so a note tied over a bar line sounds once.
    """
    origin = tempo.sec(start_div)
    cursor = 0
    prev_onset = 0
    pending_graces: List[Note] = []     # the graces seen since the last main note
    for c in measure:
        tag = _local(c.tag)
        if tag in ("backup", "forward"):
            n = _num(c, "duration") or 0
            cursor += -n if tag == "backup" else n
            cursor = max(0, cursor)
            continue
        if tag == "direction":
            if controls is not None:
                off = _num(c, "offset") or 0
                t = at + tempo.sec(start_div + max(0, cursor + off)) - origin
                controls.extend(_pedal_events(c, t))
            continue
        if tag != "note":
            continue
        is_grace = _kid(c, "grace") is not None
        is_chord = _kid(c, "chord") is not None
        dur = _num(c, "duration") or 0
        if is_grace:
            dur = 0
        if is_chord:
            onset = prev_onset
        else:
            onset = cursor
            prev_onset = onset
            cursor += dur
        if _kid(c, "rest") is not None:
            continue
        pitch_el = _kid(c, "pitch")
        if pitch_el is None:
            continue
        step_name = (_text(pitch_el, "step") or "").upper()
        octave = _int(pitch_el, "octave")
        if step_name not in STEP_SEMITONES or octave is None:
            continue
        alter = _int(pitch_el, "alter", 0) or 0
        pitch = _fit_to_piano((octave + 1) * 12 + STEP_SEMITONES[step_name] + alter)
        staff = _int(c, "staff")
        note_hand = _note_hand(staff if staff_from_file else None, hand)
        start = at + tempo.sec(start_div + onset) - origin
        end = start + (GRACE_DURATION if is_grace
                       else max(0.0, tempo.sec(start_div + onset + dur)
                                - tempo.sec(start_div + onset)))
        tuplet_num, tuplet_type = _tuplet(c)
        starts, ends = _tie(c)
        tie = "continue" if (starts and ends) else "start" if starts else "stop" if ends else None
        n = Note(pitch, start, end, _velocity(c, DEFAULT_VELOCITY), part_index, note_hand,
                 _fingering(c), False, octave * 7 + "CDEFGAB".index(step_name), alter,
                 _printed_value(c), tuplet_num, tuplet_type, grace=is_grace, tie=tie,
                 voice=_text(c, "voice") or None)
        if is_grace:
            # A grace note has no <duration>: every grace of a measure lands on the
            # same cursor. They are placed once their main note is seen, so a run of
            # graces comes out one after another into that note (as MuseScore plays
            # and prints them), not stacked up on one instant.
            if not is_chord:
                pending_graces.append(n)
        elif pending_graces:
            _place_graces(notes, pending_graces, n)
            pending_graces = []
        # Both tied noteheads are kept: the score writes two notes, and the staff
        # prints two heads joined by a tie. `_pair_ties` joins them after the note
        # list is final (sanitized, handed), so no reference goes stale.
        notes.append(n)
    return tempo.sec(start_div + _measure_divisions(measure, tempo.divisions)) - origin


def _grace_run_starts(main_start: float, count: int) -> List[float]:
    """
    Where each grace of a run begins, in written order, given its main note's start.

    Each grace takes GRACE_STEP of seconds and the run ends exactly at the main note.
    A run at the very start of the music has no room to reach back into, so its step
    shrinks to fit instead of pushing the whole song later.
    """
    step = GRACE_STEP
    if main_start < count * step:
        step = main_start / count if count else 0.0
    return [max(0.0, main_start - (count - k) * step) for k in range(count)]


def _place_graces(notes: List[Note], graces: List[Note], main: Note):
    """
    Put a run of grace notes just before the note they lean on, one after another.

    A MusicXML grace note carries no <duration>, so every grace of a measure lands
    on the same cursor. Written to one instant they would draw as one grace chord;
    MuseScore plays and prints them as a run that flickers into the main note. Each
    grace takes GRACE_STEP of seconds, the run ending exactly where its main note
    starts, so the staff sees them in played order and the falling notes strike in
    turn.
    """
    starts = _grace_run_starts(main.start, len(graces))
    bounds = starts + [main.start]
    for g, start, end in zip(graces, starts, bounds[1:]):
        for i, old in enumerate(notes):
            if old is g:
                notes[i] = replace(g, start=start, end=end)
                break


def _note_hand(staff: Optional[int], part_hand: Optional[str]) -> Optional[str]:
    """
    Which hand plays a note.

    A part the score already gives to one hand (its register said so) keeps it.
    Otherwise the note's own <staff> says: 1 is the right hand, 2 the left. With
    no <staff> and no part hand, it is left to hand_split.py (None).
    """
    if part_hand is not None:
        return part_hand
    if staff is not None:
        return RIGHT if staff <= 1 else LEFT
    return None


# --------------------------------------------------------------------------- #
# The loader
# --------------------------------------------------------------------------- #

def load_musicxml(path: str, hands_from_file: bool = True, fingering: str = "fill",
                  min_duration: float = 0.05) -> MidiSong:
    """
    Load a MusicXML score.

    hands_from_file: follow the score's own layout - <staff>1</staff> is the
                     right hand, <staff>2</staff> the left, and the parts'
                     registers decide for a score written as two parts. Off, the
                     hands are worked out from the music as for a MIDI file.
    fingering:       "auto"  - the planner finds every finger, the score's
                               printed ones ignored;
                     "file"  - the score's printed fingers exactly as written;
                     "fill"  - the score's printed fingers kept, the notes it
                               leaves blank planned in that hand position (the
                               planner may also correct a printed finger that
                               cannot be played).
                     See fingering.py; the marks themselves stay on Note.finger.
    min_duration:    very short notes are stretched to this length (seconds) so
                     they stay visible when drawn.
    """
    root = read_root(path)
    report: List[str] = []
    parts = _kids(root, "part")
    if not parts:
        raise ValueError("the score has no <part>")
    with progress.stage(0.0, 0.6):
        # The longest part decides the playing order: a score may hold a second
        # part (a second piano, a pedal line) written over fewer measures.
        part_measures = [[_Measure(m) for m in _kids(p, "measure")] for p in parts]
        measures = max(part_measures, key=len)
        order = play_order(measures, report)
        names = _part_names(root)
        part_hand = _part_hands(parts) if hands_from_file else [None] * len(parts)

        notes: List[Note] = []
        tracks: List[TrackInfo] = []
        controls: List[Tuple[float, int, int]] = []
        duration, key_fifths, key_changes, controls = _read_all_parts(parts, order, part_hand, notes,
                                                                      hands_from_file, controls)
        for i in range(len(parts)):
            hand = part_hand[i] if hands_from_file else None
            tracks.append(TrackInfo(i, (names[i] if i < len(names) else "") or f"Part {i + 1}",
                                    _part_program(root, i), 0, hand))

    if not notes:
        raise ValueError("the score has no notes")
    notes, r = sanitize_notes(notes, min_duration)
    report += r

    # Hands: the score's own layout is kept; only notes it left open (a single
    # part with no <staff>) go to hand_split, exactly as a MIDI file would.
    loose = [n for n in notes if n.hand is None]
    if loose:
        try:
            from hand_split import split_hands
            with progress.stage(0.6, 0.9):
                hands = split_hands(loose)
        except ImportError:
            hands = {id(n): (LEFT if n.pitch < MIDDLE_C else RIGHT) for n in loose}
        notes = [replace(n, hand=hands.get(id(n), n.hand)) for n in notes]

    with progress.stage(0.9, 1.0):
        bar_times, beat_times, quarter_times = _bar_and_beat_times(part_measures, order)
        notes = _fingering_stage(notes, fingering, report)

    for i, t in enumerate(tracks):
        mine = [n.hand for n in notes if n.track == i]
        if mine and all(h == mine[0] for h in mine):
            tracks[i] = replace(t, hand=mine[0], note_count=len(mine))
        else:
            tracks[i] = replace(t, hand=None, note_count=len(mine))

    duration = max((n.end for n in notes), default=duration)
    song = MidiSong(notes, tracks, duration, bar_times, beat_times, path, controls,
                    key_fifths=key_fifths, key_changes=key_changes,
                    xml_ties=_pair_ties(notes))
    song.quarter_times = quarter_times
    if _first_measure_pickup(part_measures, order) and len(bar_times) >= 2:
        song.pickup_end = bar_times[1]
    song.cleanup = report
    for line in report:
        print("%s: %s" % (os.path.basename(path), line))
    return song


def _fingering_stage(notes: List[Note], mode: str, report: List[str]) -> List[Note]:
    """
    The score's printed fingers, or the planner's, per the "fingering" setting.

    "auto" drops the score's marks before the planner runs; "file" and "fill"
    keep them on the notes, and the planner (hands.HandAnimator) is what decides
    between following them exactly and planning around them.
    """
    given = sum(1 for n in notes if n.finger)
    if mode == "auto":
        if given:
            report.append("ignored %d printed fingering mark(s) (MusicXML fingering: auto)" % given)
            return [replace(n, finger=None) for n in notes]
        return notes
    if given:
        report.append("kept %d printed fingering mark(s) from the file, %d note(s) left to the planner"
                      % (given, len(notes) - given))
    return notes


def _bar_and_beat_times(part_measures, order: List[int]):
    """
    (bar_times, beat_times, quarter_times) for the played order.

    A bar line is where the measure before it ends, so the times come from the
    same shared tempo map the notes were read with, plus each measure's own time
    signature for the beats in between.

    The beat grid runs without a gap: every measure contributes its own downbeat
    too, not only the beats after it, so a note on a bar line still sits one beat
    from its neighbours (get_beat_duration reads the spacing straight off this
    list). `quarter_times` is (time, seconds per quarter note) from the same
    tempo map, for the auto pedal's sixteenth-note step.
    """
    bar_times: List[float] = []
    beat_times: List[float] = []
    tempo = _Tempo()
    div = 0
    beats, beat_type = 4, 4
    span = 0
    for idx in order:
        present = [its[idx].elem for its in part_measures if idx < len(its)]
        if not present:
            continue
        for measure in present:
            _apply_tempo(measure, div, tempo)
            for attributes in _kids(measure, "attributes"):
                d = _int(attributes, "divisions")
                if d:
                    tempo.set_divisions(d, div)
                time = _kid(attributes, "time")
                if time is not None:
                    beats = int(_num(time, "beats", beats) or beats)
                    beat_type = int(_num(time, "beat-type", beat_type) or beat_type)
        bar_times.append(tempo.sec(div))
        for b in range(beats):
            step = b * (4.0 / beat_type) * tempo.divisions
            beat_times.append(tempo.sec(div + step))
        span = _measure_span(part_measures, idx, tempo.divisions)
        if span > 0:
            div += span
    if bar_times:
        bar_times.append(tempo.sec(div))
        # The closing bar line is a beat of its own: without it the final measure's
        # last beat would be read as stretching to the end of the music.
        beat_times.append(tempo.sec(div))
    quarter_times: List[Tuple[float, float]] = []
    # Build quarter_times with the LAST point at a given time winning
    # (the first point at a time is often the stale default 120 bpm)
    by_time: Dict[float, float] = {}
    for _, sec, bpm in tempo.points:
        if bpm > 0:
            by_time[sec] = 60.0 / bpm  # later point overwrites earlier one
    quarter_times = sorted(by_time.items())
    return bar_times, beat_times, quarter_times

def _part_names(root) -> List[str]:
    out = []
    pl = _kid(root, "part-list")
    if pl is None:
        return out
    for sp in _kids(pl, "score-part"):
        out.append(_text(sp, "part-name", "") or "")
    return out


def _part_program(root, index: int) -> int:
    """The part's General MIDI program, 0-based like pretty_midi's (0 = piano)."""
    pl = _kid(root, "part-list")
    if pl is None:
        return 0
    sps = _kids(pl, "score-part")
    if index >= len(sps):
        return 0
    for mi in _kids(sps[index], "midi-instrument"):
        p = _int(mi, "midi-program")
        if p:
            return max(0, p - 1)
    return 0


# --------------------------------------------------------------------------- #
# Writing fingering back (export)
# --------------------------------------------------------------------------- #

def _note_key(staff, voice, pitch):
    return (staff or 1, voice or "", pitch)


def _set_staff(note_elem, hand):
    """Add or replace <staff> from the hand (1 = right, 2 = left); leave it when unknown."""
    want = None if hand is None else (1 if hand == RIGHT else 2)
    existing = _kid(note_elem, "staff")
    if want is None:
        return
    if existing is not None:
        existing.text = str(want)
    else:
        el = ET.SubElement(note_elem, "staff")
        el.text = str(want)
        # <staff> belongs after <voice>, before <type>: move it there if we can
        order = ["grace", "cue", "chord", "pitch", "rest", "duration", "tie", "voice",
                 "type", "dot", "accidental", "time-modification", "stem", "notehead",
                 "staff", "beam", "notations", "lyric"]
        kids = list(note_elem)
        kids.sort(key=lambda c: order.index(_local(c.tag)) if _local(c.tag) in order else len(order))
        note_elem[:] = kids


def _set_fingering(note_elem, finger):
    """Add or replace <notations><technical><fingering> with `finger` (None removes it)."""
    notations = _kid(note_elem, "notations")
    tech = _kid(notations, "technical") if notations is not None else None
    if finger:
        if notations is None:
            notations = ET.SubElement(note_elem, "notations")
        if tech is None:
            tech = ET.SubElement(notations, "technical")
        f = _kid(tech, "fingering")
        if f is None:
            f = ET.SubElement(tech, "fingering")
        f.text = str(int(finger))
        f.attrib.pop("substitution", None)
        f.attrib.pop("alternate", None)
        # keep <notations> after the notes' other children
        kids = list(note_elem)
        kids.sort(key=lambda c: 0 if _local(c.tag) != "notations" else 1)
        note_elem[:] = kids
    elif tech is not None:
        f = _kid(tech, "fingering")
        if f is not None:
            tech.remove(f)
        if not list(tech):
            notations.remove(tech)
        if not list(notations):
            note_elem.remove(notations)


def _note_start(measure, measure_at: float, start_div: int, tempo: _Tempo):
    """
    (note element, its start in seconds) for each <note> of a measure, in the
    order _read_measure reads them: the cursor advances note by note, a <chord>
    note keeps the note before it's onset, a <backup>/<forward> moves the cursor
    (that is how one measure holds several voices), and a <grace> note takes no
    time. This is how the loaded Note knows its start, so the two can be paired.
    """
    origin = tempo.sec(start_div)
    cursor = 0
    prev_onset = 0
    grace_idx: List[int] = []       # positions in `out` of the graces since the last main note
    out = []
    for c in measure:
        tag = _local(c.tag)
        if tag in ("backup", "forward"):
            n = _num(c, "duration") or 0
            cursor += -n if tag == "backup" else n
            cursor = max(0, cursor)
            continue
        if tag != "note" or _kid(c, "pitch") is None:
            continue
        is_grace = _kid(c, "grace") is not None
        dur = 0 if is_grace else (_num(c, "duration") or 0)
        if _kid(c, "chord") is not None:
            onset = prev_onset
        else:
            onset = cursor
            prev_onset = onset
            cursor += dur
        start = measure_at + tempo.sec(start_div + onset) - origin
        out.append((c, start))
        if is_grace:
            if _kid(c, "chord") is None:
                grace_idx.append(len(out) - 1)
        elif grace_idx:
            # The graces of a run are placed in _read_measure's own way, so a <note>
            # still pairs with the Note it made.
            starts = _grace_run_starts(start, len(grace_idx))
            for k, idx in enumerate(grace_idx):
                out[idx] = (out[idx][0], starts[k])
            grace_idx = []
    return out


def _score_note_times(parts, order):
    """
    [(part index, note element, start in seconds)] for the whole score, in the
    order the loader reads it, so a <note> can be paired with its loaded Note by
    (part, pitch, start) - the same (pitch, start) key save_fingered_midi uses.
    A measure played twice (a repeat) is walked once: the score writes it once,
    so only the first pass's note is in the file to finger.
    """
    part_measures = [[_Measure(m) for m in _kids(p, "measure")] for p in parts]
    tempo = _Tempo()
    at = 0.0
    div = 0
    done = []
    out = []
    for idx in order:
        if idx in done:
            continue
        done.append(idx)
        present = [its[idx].elem for its in part_measures if idx < len(its)]
        if not present:
            continue
        for measure in present:
            _apply_tempo(measure, div, tempo)
            for attributes in _kids(measure, "attributes"):
                d = _int(attributes, "divisions")
                if d:
                    tempo.set_divisions(d, div)
        length = 0.0
        for i, its in enumerate(part_measures):
            if idx >= len(its):
                continue
            rows = _note_start(its[idx].elem, at, div, tempo)
            for c, start in rows:
                out.append((i, c, start))
            length = max(length, tempo.sec(div + _measure_divisions(its[idx].elem, tempo.divisions))
                         - tempo.sec(div))
        div += _measure_span(part_measures, idx, tempo.divisions)
        at += length
    return out


def _write_fingers(root, notes, fingers):
    """
    Put each note's hand and finger into the score's own <note> elements, in
    place. Each <note> is paired with the loaded Note it produced by (part,
    pitch, start in seconds) - the same key save_fingered_midi uses - so notes
    of the same pitch in one measure (different voices, or two hands) keep their
    own mark. A note the editor added has no <note> of its own and is skipped.
    Returns how many note elements were fingered.
    """
    parts = _kids(root, "part")
    if not parts:
        return 0
    part_measures = [[_Measure(m) for m in _kids(p, "measure")] for p in parts]
    order = play_order(max(part_measures, key=len), [])
    times = _score_note_times(parts, order)

    index = {}
    for n in notes:
        index.setdefault((n.track, n.pitch, round(n.start * 1000)), []).append(n)
    used = set()
    marked = 0
    for pi, c, start in times:
        pitch_el = _kid(c, "pitch")
        step = (_text(pitch_el, "step") or "").upper()
        octave = _int(pitch_el, "octave")
        if step not in STEP_SEMITONES or octave is None:
            continue
        alter = _int(pitch_el, "alter", 0) or 0
        pitch = _fit_to_piano((octave + 1) * 12 + STEP_SEMITONES[step] + alter)
        pool = index.get((pi, pitch, round(start * 1000)), [])
        n = next((x for x in pool if id(x) not in used), None)
        if n is None:                       # a note the editor added, or a shifted one
            continue
        used.add(id(n))
        _set_staff(c, n.hand)
        f = fingers.get(id(n)) if fingers is not None else None
        _set_fingering(c, f)
        if f:
            marked += 1
    return marked


def save_musicxml(src_path: str, dst_path: str, notes, fingers) -> int:
    """
    Write a copy of the MusicXML score at `src_path` to `dst_path`, with every
    note's hand in <staff> and its finger in <notations><technical><fingering>
    - what the score said plus whatever the fingering editor changed. Every
    other part of the score (structure, layout, tempo, dynamics, ties) is left
    as it was. A .mxl destination is written as a compressed score, a .xml one
    as plain text. `notes` are the loaded Note objects and `fingers` maps
    id(note) -> finger. Returns how many notes were marked.
    """
    root = read_root(src_path)
    marked = _write_fingers(root, notes, fingers)
    data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    if os.path.splitext(dst_path)[1].lower() == ".mxl":
        container = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                     '<container version="1.0" '
                     'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
                     '  <rootfiles>\n'
                     '    <rootfile full-path="score.xml" media-type="application/vnd.recordare.musicxml+xml"/>\n'
                     '  </rootfiles>\n</container>\n')
        with zipfile.ZipFile(dst_path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("META-INF/container.xml", container)
            z.writestr("score.xml", data)
    else:
        with open(dst_path, "wb") as fh:
            fh.write(data)
    return marked
