"""
staff.py - Sheet music Grand Staff (Treble and Bass clefs) rendering for Hand-thesia.

Features:
- Grand Staff (Treble clef + Bass clef) with standard diatonic pitch mapping.
- White sheet music paper background (252, 252, 254) with adjustable opacity (100%, 85%, 60%).
- Paged systems (MuseScore style): static staff systems with playback cursor moving
  smoothly from left to right. When cursor reaches the end of the system, it flips
  to the next system and resets to the left.
- Note durations matching actual duration: whole (nốt tròn), half (nốt trắng),
  quarter (nốt đen), 8th (móc đơn), 16th (móc kép), and dotted notes (chấm dôi).
- Hand-colored noteheads (green for right hand, blue for left hand) with active
  note highlighting/glow when sounding.
- Ledger lines, accidentals (sharps), stems, flags, dots, measure bar lines, clefs, and bracket.
"""
from __future__ import annotations

import bisect
import math
from collections import namedtuple
from typing import List, Optional, Tuple

import pygame

from common import (ACCENT, BAR_BG, BAR_LINE, BG, HAND_COLORS, PANEL_EDGE,
                    TEXT, TEXT_DIM, ui_font)
from midi_loader import LEFT, PITCH_MAP, RIGHT, Note, key_alterations
import notation
from notation import note_shape

CLEF_TREBLE = "treble"
CLEF_BASS = "bass"

# Note value classification constants
WHOLE = "whole"
DOTTED_HALF = "dotted_half"
HALF = "half"
DOTTED_QUARTER = "dotted_quarter"
QUARTER = "quarter"
DOTTED_EIGHTH = "dotted_eighth"
EIGHTH = "eighth"
SIXTEENTH = "sixteenth"

# Sheet music paper styling constants
STAFF_BG = (252, 252, 254)
STAFF_LINE = (80, 80, 95)
CLEF_COLOR = (25, 25, 35)
TIME_SIG_COLOR = (25, 25, 35)
BAR_LINE_COLOR = (110, 110, 125)
MEASURE_NUM_COLOR = (130, 130, 145)
STEM_COLOR = (35, 35, 45)
CURSOR_COLOR = (220, 40, 60)

# Diatonic step definitions:
# Treble middle line = B4 = step 34 (Lines: E4=30, G4=32, B4=34, D5=36, F5=38)
# Bass middle line = D3 = step 22 (Lines: G2=18, B2=20, D3=22, F3=24, A3=26)
# Middle C = C4 = step 28 (1 ledger line below Treble staff, 1 ledger line above Bass staff)
TREBLE_MID_STEP = 34
BASS_MID_STEP = 22
MIDDLE_C_STEP = 28

MARGIN_LEFT = 85
MARGIN_RIGHT = 20

# Where the staff's own left edge stands inside the panel: the bracket sits a little
# further left, the clefs a little further right.
STAFF_LEFT_X = 18

# Zoom of the staff: MIN/MAX_TARGET_DUR bound how many seconds of music may share one system
# (MIN_SCALE / MAX_SCALE bound how big the staff itself is drawn).
MIN_TARGET_DUR = 3.0
MAX_TARGET_DUR = 20.0
MIN_SCALE = 0.6
MAX_SCALE = 1.5

# Upper limit on the notes one beam may join. In practice a beat decides it and this only
# stops a very fast beat from turning into one endless bar across the whole measure.
MAX_BEAM_CHORDS = 8

# How far a note's ink stays off a bar line: MuseScore's noteBarDistance (styledef.cpp).
NOTE_BAR_DISTANCE_SP = 1.5

# The grand staff's vertical layout: the two middle lines stand this many spaces apart
# at least (the old fixed distance), and the hands' ink keeps this much clear air between
# them. A system that needs more room gets it, and if the spread no longer fits the panel
# the whole system is drawn smaller - never smaller than MIN_FIT_SCALE of the user's size.
BASE_MID_GAP_SP = 6.0
INNER_CLEARANCE_SP = 2.5
STAFF_PAD_Y = 4.0
MIN_FIT_SCALE = 0.6
OCTAVE_BRACKET_SP = 3.0
# The staff's own outer lines (2 sp from the middle) plus a little air.
STAFF_EDGE_SP = 2.15
# The 10 px measure-number font plus its 1 px gap: a band above the content block.
MEASURE_NUM_PAD_PX = 14.0
# A pedal bracket belongs below the bass staff and below every note's ink, with this much
# clear air for its "Ped." label (the label has a 9 px minimum, so at the smallest staff
# this much keeps even it clear of the notes), and this much room for the line and ticks.
PEDAL_GAP_SP = 3.4
PEDAL_BAND_SP = 0.4

# Where a system's ink sits when it is laid out: its size, the two staff middle lines,
# the room the head of the staff leaves before the notes, and where a pedal bracket goes
# (None when the system shows no pedal).
StaffGeometry = namedtuple(
    "StaffGeometry", "s treble_mid_y bass_mid_y margin_left usable_w pedal_y")

# These are the values MuseScore engraves with (src/engraving/style/styledef.cpp:
# stemLength 3.5, stemWidth 0.10, beamWidth 0.5), all measured in staff spaces -
# and what notation.py draws the notes with.
STEM_LEN_SPACES = 3.5
STEM_WIDTH_SPACES = 0.10
BEAM_WIDTH_SPACES = 0.5
# The gap between two beam bars: MuseScore's beamsHeight is beamCount * 0.75 - 0.25 spaces
# for the whole stack, so bar centre to bar centre is 0.75 * (bars - 1).
BEAM_STEP_SPACES = 0.75
# How far the notehead must stay clear of the beams, from StemLayout::calcMinStemLength:
# a beamed note's stem is never allowed to be so short that the head touches the bars.
BEAM_CLEARANCE_SPACES = (2.5, 2.25, 2.0, 1.75)


def stem_up_for(steps, mid_step: int) -> bool:
    """
    Which way the stems of these notes point on one staff.

    This is MuseScore's ChordLayout::computeAutoStemDirection. Counting lines from the top
    (line 0 is the top line, the middle line is 2), the two outer notes decide: if they
    sit on average below the middle line the stems go up, above it they go down, and when
    the two cancel out you look one note further in.
    """
    lines = sorted(((mid_step - st) / 2.0 for st in steps), reverse=True)   # low note first
    left, right = 0, len(lines) - 1
    while left <= right:
        net = lines[left] + lines[right]
        if net == 0:
            left += 1
            right -= 1
            continue
        return net > 0
    return False


def min_stem_spaces(beam_count: int) -> float:
    """
    Shortest a beamed note's stem may be, in staff spaces, measured from the notehead's
    centre to the far edge of the stack of beam bars. A run of sixteenths stacks two bars
    and needs a longer stem than a run of eighths, or the notehead would sit on the bars.
    """
    bars = max(1, min(beam_count, len(BEAM_CLEARANCE_SPACES)))
    stack = bars * BEAM_STEP_SPACES - 0.25          # thickness of the whole stack
    return BEAM_CLEARANCE_SPACES[bars - 1] + stack + BEAM_WIDTH_SPACES / 2


def clamp_target_dur(value) -> float:
    """Keep the seconds of music per system inside the range the staff draws well at."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 10.0
    return max(MIN_TARGET_DUR, min(MAX_TARGET_DUR, v))


def clamp_scale(value) -> float:
    """Keep the staff's size multiplier inside its range."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 1.0
    return max(MIN_SCALE, min(MAX_SCALE, v))


def diatonic_step(pitch: int) -> Tuple[int, int]:
    """Return (diatonic_step, is_sharp) for a MIDI pitch."""
    octave = pitch // 12 - 1
    step_in_oct, is_sharp = PITCH_MAP[pitch % 12]
    return octave * 7 + step_in_oct, is_sharp


def written_step(note: Note) -> Tuple[int, int]:
    """
    Return (diatonic_step, accidental) as the score wrote the note.

    A MIDI file only carries the pitch, so the speller falls back to the sharp
    reading (C# rather than Db) - what this has always drawn. A MusicXML score
    says what it wrote: `step` is the diatonic step and `alter` the semitones
    beside it, -1 for a flat, +1 for a sharp, +2 / -2 for the doubles. So Db is
    step D and -1, and the staff prints a flat where the composer wrote one.
    """
    if getattr(note, "step", None) is not None:
        return note.step, getattr(note, "alter", 0) or 0
    return diatonic_step(note.pitch)


def has_accidental(note: Note) -> bool:
    """True when the note is drawn with an accidental (a sharp, flat or natural)."""
    return written_step(note)[1] != 0


# How wide an accidental's ink is, in spaces, by the semitones it spells. A sharp,
# flat or natural is the plain sign the note has always been given room for; the
# doubles are wider, and take that much more.
ACCIDENTAL_WIDTH_SP = {1: 0.8, 2: 1.6}


def accidental_width_of(alt: Optional[int]) -> float:
    """
    The width of the sign that spells `alt` semitones, in spaces (0 for no sign).

    `alt` is None when the note prints no sign at all, 0 for a natural, +1 for a
    sharp, -1 for a flat, and +2 / -2 for the doubles.
    """
    if alt is None:
        return 0.0
    return ACCIDENTAL_WIDTH_SP.get(min(2, abs(alt)), 0.8)


def accidental_extra_of(alt: Optional[int]) -> float:
    """
    How much wider than a plain sign the sign that spells `alt` is, in spaces.

    A sharp, flat or natural is the one sign the layout has always allowed for, so
    its extra is 0 - the caller adds the plain sign's room itself. The doubles are
    a second sign wide.
    """
    return 0.8 if alt is not None and abs(alt) > 1 else 0.0


def accidental_width_sp(note: Note) -> float:
    """The width of the note's accidental, in spaces (0 when it has none)."""
    alt = written_step(note)[1]
    return accidental_width_of(alt if alt else None)


def accidental_extra_sp(note: Note) -> float:
    """
    How much wider than a plain sign the note's accidental is, in spaces.

    A sharp, flat or natural is the one sign the layout has always allowed for, so
    its extra is 0 - the caller adds the plain sign's room itself. The doubles are
    a second sign wide.
    """
    return accidental_extra_of(written_step(note)[1] or None)


def plan_accidentals(notes: List[Note], measure_starts: List[float],
                     key_fifths: int) -> dict:
    """
    Which notes print an accidental, and which do not: {id(note): the sign}.

    A key signature already alters the notes it covers - the four sharps of E major
    are the F, C, G and D of every octave - so those notes print no sign at all.
    A note that differs from the key (an A# in E major) prints one, and that sign
    holds for the rest of its measure: the notes of that step and octave after it
    print no sign, and a note that comes back to the key's spelling prints the
    sign that says so - a natural after a sharp.

    The sign is None when the note prints none, 0 for a natural, +1 for a sharp,
    -1 for a flat and +2 / -2 for the doubles.
    """
    out = {}
    if not notes:
        return out
    key_alt = key_alterations(key_fifths)
    state = {}                       # (clef, step) -> the alteration in force
    bounds = sorted(measure_starts)
    measure = None
    for n in sorted(notes, key=lambda n: (n.start, n.pitch)):
        if n.is_rest:
            continue
        step, alter = written_step(n)
        where = bisect.bisect_right(bounds, n.start + 1e-6) - 1
        if where != measure:
            measure = where
            state.clear()            # an accidental holds for its measure only
        k = (clef_for_note(n), step)
        if alter != state.get(k, key_alt.get(step % 7, 0)):
            out[id(n)] = alter
            state[k] = alter
        else:
            out[id(n)] = None
    return out


def clef_for_note(note: Note) -> str:
    """Determine whether a note belongs to Treble clef or Bass clef."""
    if note.hand == RIGHT:
        return CLEF_TREBLE if note.pitch >= 55 else CLEF_BASS
    elif note.hand == LEFT:
        return CLEF_BASS if note.pitch <= 64 else CLEF_TREBLE
    return CLEF_TREBLE if note.pitch >= 60 else CLEF_BASS


def note_y(step: int, clef: str, treble_mid_y: float, bass_mid_y: float, s: float) -> float:
    """Y coordinate for a given diatonic step on the specified clef."""
    if clef == CLEF_TREBLE:
        return treble_mid_y - (step - TREBLE_MID_STEP) * (s * 0.5)
    return bass_mid_y - (step - BASS_MID_STEP) * (s * 0.5)


def get_beat_duration(song, t: float = 0.0) -> float:
    """Determine beat duration in seconds at song time t."""
    if song is not None:
        beat_times = getattr(song, "beat_times", None)
        if beat_times and len(beat_times) >= 2:
            idx = bisect.bisect_right(beat_times, t) - 1
            idx = max(0, min(len(beat_times) - 2, idx))
            dur = beat_times[idx + 1] - beat_times[idx]
            if dur > 0.05:
                return dur
        bar_times = getattr(song, "bar_times", None)
        if bar_times and len(bar_times) >= 2:
            idx = bisect.bisect_right(bar_times, t) - 1
            idx = max(0, min(len(bar_times) - 2, idx))
            dur = (bar_times[idx + 1] - bar_times[idx]) / 4.0
            if dur > 0.05:
                return dur
    return 0.5


def note_value(duration: float, beat_len: float = 0.5) -> str:
    """Classify note value string based on duration and beat duration.

    The thresholds live in notation.note_shape (the drawing core); this keeps the
    names the staff has always used.
    """
    return _NOTE_VALUES[note_shape(duration, beat_len)]


# the eight note values, by the shape notation.note_shape gives for them
_NOTE_VALUES = {
    ("whole", 0, 0): WHOLE,
    ("half", 0, 1): DOTTED_HALF,
    ("half", 0, 0): HALF,
    ("black", 0, 1): DOTTED_QUARTER,
    ("black", 0, 0): QUARTER,
    ("black", 1, 1): DOTTED_EIGHTH,
    ("black", 1, 0): EIGHTH,
    ("black", 2, 0): SIXTEENTH,
}

# A MusicXML <type> (with its dots) in the shapes notation.note_shape returns, and
# the same shapes named as the staff names them (WHOLE, DOTTED_HALF, ...). A written
# value the staff has no name for (a 32nd) is drawn as its shape but named as the
# nearest it knows (a 16th), so the beaming and flag code keeps working.
_PRINTED_SHAPES = {
    "whole": ("whole", 0, 0), "dotted_whole": ("whole", 0, 1), "half": ("half", 0, 0),
    "dotted_half": ("half", 0, 1), "double_dotted_half": ("half", 0, 2),
    "quarter": ("black", 0, 0), "dotted_quarter": ("black", 0, 1),
    "double_dotted_quarter": ("black", 0, 2), "eighth": ("black", 1, 0),
    "dotted_eighth": ("black", 1, 1), "sixteenth": ("black", 2, 0),
    "thirty_second": ("black", 3, 0), "sixty_fourth": ("black", 4, 0),
}
_PRINTED_VALUES = {name: _NOTE_VALUES.get(shape, SIXTEENTH)
                   for name, shape in _PRINTED_SHAPES.items()}


def printed_value(note: Note, duration: float, beat_len: float = 0.5) -> str:
    """
    The note's value as the score wrote it, when it has one, else the guessed one.

    A MIDI file says how long a note lasts, not how it is written, so the value is
    guessed from the seconds against the beat (note_value). A MusicXML score wrote
    it down: a quarter tied to a sixteenth, or a triplet, keeps the value the score
    printed instead of one rounded out of its length.
    """
    name = getattr(note, "printed", None)
    return _PRINTED_VALUES.get(name) or note_value(duration, beat_len)


def printed_shape(note: Note, duration: float, beat_len: float = 0.5):
    """
    The note's drawn shape: the score's own written value when it has one, else
    the shape guessed from its length.
    """
    name = getattr(note, "printed", None)
    shape = _PRINTED_SHAPES.get(name)
    return shape if shape is not None else note_shape(duration, beat_len)


class Measure:
    def __init__(self, index: int, start_t: float, end_t: float):
        self.index = index
        self.start_t = start_t
        self.end_t = end_t
        self.notes: List[Note] = []

    @property
    def duration(self) -> float:
        return max(0.001, self.end_t - self.start_t)


class StaffSystem:
    def __init__(self, index: int, measures: List[Measure]):
        self.index = index
        self.measures = measures
        self.start_t = measures[0].start_t if measures else 0.0
        self.end_t = measures[-1].end_t if measures else 1.0
        self.notes: List[Note] = []

    @property
    def duration(self) -> float:
        return max(0.001, self.end_t - self.start_t)


def build_systems(song, target_dur: float = 10.0) -> List[StaffSystem]:
    """Slice song measures into static systems (pages)."""
    if not song:
        return []

    bars = [float(t) for t in getattr(song, "bar_times", []) if t >= 0.0]
    bars = sorted(set(bars))

    if not bars:
        # Generate synthetic measures every 3 seconds
        step_t = 3.0
        count = max(1, math.ceil(max(3.0, song.duration) / step_t))
        bars = [i * step_t for i in range(count + 1)]
    else:
        if bars[0] > 0.0:
            bars.insert(0, 0.0)
        if len(bars) == 1 or bars[-1] < song.duration:
            bars.append(max(bars[-1] + 2.0, song.duration))

    measures: List[Measure] = []
    for i in range(len(bars) - 1):
        measures.append(Measure(i, bars[i], bars[i + 1]))

    # How much music fits on one system: target_dur is the seconds of song per system, so a
    # smaller value shows fewer, wider notes (zoom in) and a bigger one crams them (zoom out).
    target_dur = max(MIN_TARGET_DUR, min(MAX_TARGET_DUR, float(target_dur)))
    avg_bar = max(0.5, song.duration / max(1, len(measures)))
    bars_per_sys = max(1, min(8, round(target_dur / avg_bar)))

    systems: List[StaffSystem] = []
    for i in range(0, len(measures), bars_per_sys):
        chunk = measures[i:i + bars_per_sys]
        systems.append(StaffSystem(len(systems), chunk))

    # Distribute notes into systems (a note spanning across system boundaries is
    # added to every system it overlaps with, so tied pieces across pages/systems
    # are rendered on both the system where they start and the system where they continue)
    for n in getattr(song, "notes", []):
        for sys in systems:
            if n.start < sys.end_t - 1e-9 and n.end > sys.start_t + 1e-9:
                sys.notes.append(n)

    return systems


def split_at_bar_lines(notes: List[Note], bar_times: List[float], beat_times: Optional[List[float]] = None) -> Tuple[List[Note], List[Tuple[Note, Note]]]:
    """
    Split the notes that run past a bar line or beat boundary, and pair the pieces that are tied.

    An engraver never writes one note across a bar line or across a beat boundary when
    starting off-beat: the note is cut at the bar/beat, the piece that ends the subdivision
    and the piece that opens the next beat are written out separately and joined by a tie.

    Each note becomes the note itself when no cut time lies strictly inside it, or one
    piece per span `[start, b1], [b1, b2], ..., [bk, end]` when it does. The pieces are
    `Note`s too - same frozen dataclass, clipped start/end, everything else carried over.

    Returns `(pieces, ties)`, `ties` pairing each `(left, right)` of pieces that must be
    tied - the right piece starts exactly where the left one ends.
    """
    cuts_set = {float(b) for b in (bar_times or [])}
    if beat_times:
        cuts_set.update(float(b) for b in beat_times)
    bars = sorted(cuts_set)
    pieces: List[Note] = []
    ties: List[Tuple[Note, Note]] = []
    for n in notes:
        # A file's own rounding can put a downbeat a hair before the bar (50-nam-ve-sau's
        # measure-1 downbeat is 2e-16 early). Such a note is that measure's downbeat, not a
        # note of the measure before it, so judge the cuts from the bar it is really on -
        # but leave the note's own start where the file put it.
        start = n.start
        for b in bars:
            if abs(b - start) < 1e-9:
                start = b
                break
        # Every bar time strictly inside the note cuts it. A note starting exactly on a
        # bar time is already in its own measure and is left whole.
        cuts = []
        for b in bars:
            if start + 1e-9 < b < n.end - 1e-9:
                cuts.append(b)
            elif b >= n.end - 1e-9:
                break
        if not cuts:
            pieces.append(n)
            continue
        # One piece per span: [start, b1], [b1, b2], ..., [bk, end]. Each piece after the
        # first is tied to the one before it.
        bounds = [n.start] + cuts + [n.end]
        prev = None
        for i in range(len(bounds) - 1):
            piece = Note(pitch=n.pitch, start=bounds[i], end=bounds[i + 1],
                         velocity=n.velocity, track=n.track, hand=n.hand,
                         finger=n.finger if i == 0 else None,
                         gliss=n.gliss if i == 0 else False,
                         # the written value belongs to the note the score wrote: it stays
                         # on the piece that opens it (the value is reworked per piece
                         # downstream anyway, from the piece's own span). The spelling
                         # (step and alter) is the note's, and every piece of a tied note
                         # is written the same way - a Db stays a Db after the bar line.
                         step=n.step,
                         alter=n.alter,
                         printed=n.printed if len(bounds) == 2 else None,
                         # so is the tuplet the note is written in: the bracket spans
                         # whichever pieces of it this system can see
                         tuplet_num=n.tuplet_num,
                         tuplet_type=n.tuplet_type,
                         voice=n.voice)
            pieces.append(piece)
            if prev is not None:
                ties.append((prev, piece))
            prev = piece
    return pieces, ties


# --------------------------------------------------------------------------- #
# Drawing helpers
# --------------------------------------------------------------------------- #

def draw_treble_clef(surf: pygame.Surface, x: float, mid_y: float, s: float, color: Tuple[int, int, int]):
    """A G clef on the line it names: G4, mid_y + s (SMuFL puts a clef's origin there).
    The hand-drawn shape below is what it falls back to without the music font."""
    if notation.draw_clef(surf, x, mid_y + s, s, color, "gClef"):
        return

    spine_x = x + s * 1.15
    y_top = mid_y - s * 2.8
    y_bot = mid_y + s * 2.7
    line_w = max(2, int(s * 0.22))

    # Vertical spine
    pygame.draw.line(surf, color, (round(spine_x), round(y_top)), (round(spine_x), round(y_bot)), line_w)
    # Bottom hook dot
    pygame.draw.circle(surf, color, (round(spine_x - s * 0.4), round(y_bot - s * 0.2)), max(2, int(s * 0.3)))

    # Upper loop
    loop_pts = []
    for deg in range(-90, 95, 15):
        rad = math.radians(deg)
        cx = spine_x + s * 0.25
        cy = mid_y - s * 1.8
        loop_pts.append((cx + s * 0.45 * math.cos(rad), cy - s * 0.95 * math.sin(rad)))
    if len(loop_pts) >= 2:
        pygame.draw.lines(surf, color, False, [(round(px), round(py)) for px, py in loop_pts], line_w)

    # Main lower body and spiral around G4 (mid_y + s)
    body_pts = []
    for deg in range(90, 275, 15):
        rad = math.radians(deg)
        cx = spine_x - s * 0.25
        cy = mid_y + s * 0.7
        body_pts.append((cx + s * 0.75 * math.cos(rad), cy - s * 1.1 * math.sin(rad)))
    for deg in range(-90, 65, 15):
        rad = math.radians(deg)
        cx = spine_x + s * 0.2
        cy = mid_y + s * 1.1
        body_pts.append((cx + s * 0.75 * math.cos(rad), cy - s * 0.6 * math.sin(rad)))
    for deg in range(60, 230, 15):
        rad = math.radians(deg)
        cx = spine_x + s * 0.05
        cy = mid_y + s * 0.95
        body_pts.append((cx + s * 0.4 * math.cos(rad), cy - s * 0.35 * math.sin(rad)))
    if len(body_pts) >= 2:
        pygame.draw.lines(surf, color, False, [(round(px), round(py)) for px, py in body_pts], line_w)


def draw_bass_clef(surf: pygame.Surface, x: float, mid_y: float, s: float, color: Tuple[int, int, int]):
    """An F clef on the line it names: F3, mid_y - s. Without the music font it falls back
    to the hand-drawn shape below - a dot on that line and an arc off it."""
    if notation.draw_clef(surf, x, mid_y - s, s, color, "fClef"):
        return

    f_line_y = mid_y - s
    r_dot = max(2, int(s * 0.45))
    line_w = max(2, int(s * 0.22))
    pygame.draw.circle(surf, color, (round(x + s * 0.6), round(f_line_y)), r_dot)

    pts = []
    for deg in range(180, -45, -15):
        rad = math.radians(deg)
        cx = x + s * 0.8
        cy = f_line_y - s * 0.1
        rx = s * 0.9
        ry = s * 0.9
        pts.append((cx + rx * math.cos(rad), cy - ry * math.sin(rad)))
    pts.append((x + s * 1.5, mid_y + s * 0.2))
    pts.append((x + s * 1.1, mid_y + s * 1.0))
    pts.append((x + s * 0.7, mid_y + s * 1.4))
    if len(pts) >= 2:
        pygame.draw.lines(surf, color, False, [(round(px), round(py)) for px, py in pts], line_w)

    dot_r = max(1, int(s * 0.22))
    dot_x = round(x + s * 1.8)
    pygame.draw.circle(surf, color, (dot_x, round(f_line_y - s * 0.5)), dot_r)
    pygame.draw.circle(surf, color, (dot_x, round(f_line_y + s * 0.5)), dot_r)


def clef_ink_w(sp: float) -> float:
    """How wide the widest clef's ink is at `sp` staff spaces, so the time signature
    can be placed clear of it (a treble clef is 2.56 sp wide, a bass one 2.66)."""
    w = 0.0
    for name in ("gClef", "fClef"):
        g = notation.glyph(name, sp, CLEF_COLOR)
        if g is not None:
            w = max(w, g.size[0])
    return w or float(round(sp * 2.0))       # the hand-drawn clefs, without the font


# The gap the time signature's column keeps off the clefs, in staff spaces.
TIME_SIG_GAP_SPACES = 0.35

# The gap the key signature keeps off the clef, in staff spaces.
KEY_SIG_GAP_SPACES = 0.35

# The gap the first note keeps off the time signature's column, in staff spaces.
FIRST_NOTE_GAP_SPACES = 1.15

# A key signature's accidentals, in the order the key signature writes them - the
# sharps go F C G D A E B, the flats B E A D G C F, the circle of fifths - and the
# staff step each one stands on. The treble staff's sharps start on its top line
# (F5) and its flats on its middle line (B4); the bass staff's start a third
# lower, on its fourth line (F3) and its second line (B2).
KEY_SHARP_STEPS = {CLEF_TREBLE: (38, 35, 39, 36, 33, 37, 34),
                   CLEF_BASS: (24, 21, 25, 22, 19, 23, 20)}
KEY_FLAT_STEPS = {CLEF_TREBLE: (34, 37, 33, 36, 32, 35, 31),
                  CLEF_BASS: (20, 23, 19, 22, 18, 21, 24)}

# How far apart a key signature's accidentals stand, in spaces. A sharp's ink is
# 0.98 sp wide in Leland and a flat's 0.81, so a space between them leaves them
# clear of each other the way MuseScore packs them (KeySig::layout).
KEY_SIG_ADVANCE_SP = 1.0


def key_sig_steps(fifths: int, clef: str) -> List[Tuple[int, int]]:
    """
    The accidentals a key signature holds, as (staff step, sign) in the order they
    are written: the sharps of a sharp key, the flats of a flat one.
    """
    if not fifths:
        return []
    steps = KEY_SHARP_STEPS if fifths > 0 else KEY_FLAT_STEPS
    sign = 1 if fifths > 0 else -1
    return [(st, sign) for st in steps[clef][:min(7, abs(fifths))]]


def key_sig_ink_w(sp: float, fifths: int) -> float:
    """How wide one accidental of a key signature is, in spaces."""
    name = "accidentalSharp" if fifths > 0 else "accidentalFlat"
    g = notation.glyph(name, sp, TIME_SIG_COLOR)
    if g is None:                       # no music font: the hand-drawn width
        return 0.98 if fifths > 0 else 0.81
    return g.size[0] / sp


def key_sig_width_sp(fifths: int, sp: float) -> float:
    """How wide the whole key signature stands, in spaces."""
    n = min(7, abs(fifths))
    if not n:
        return 0.0
    return key_sig_ink_w(sp, fifths) + (n - 1) * KEY_SIG_ADVANCE_SP


def key_sig_x(left_x: float, sp: float) -> float:
    """Where a key signature's first accidental stands: clear of the clef."""
    return left_x + 8 + clef_ink_w(sp) + KEY_SIG_GAP_SPACES * sp


def draw_key_signature(surf: pygame.Surface, x: float, mid_y: float, mid_step: int,
                       sp: float, fifths: int, clef: str,
                       color: Tuple[int, int, int] = TIME_SIG_COLOR) -> bool:
    """
    A key signature: the accidentals of the key, each on the staff step the
    tradition gives it, a space apart.

    `x` is where the first accidental's ink starts, `mid_y` the staff's middle line
    and `mid_step` the step on it (the treble staff's B4, the bass staff's D3).
    False when the music font is missing, so the caller can leave the column out.
    """
    if not fifths:
        return False
    name = "accidentalSharp" if fifths > 0 else "accidentalFlat"
    g = notation.glyph(name, sp, color)
    if g is None:
        return False
    for i, (step, _sign) in enumerate(key_sig_steps(fifths, clef)):
        g.draw(surf, x + i * KEY_SIG_ADVANCE_SP * sp,
               mid_y - (step - mid_step) * (sp * 0.5))
    return True


def time_sig_x(left_x: float, sp: float, key_end: float = 0.0) -> float:
    """
    Where the time signature's column sits: `left_x + 4.6 sp`, or further right if the
    clefs would run into it (a 2.66 sp bass clef at `left_x + 8` reaches `left_x + 4.6 sp`
    at the smallest staff sizes, s = 4), or if a key signature stands between the two.
    The left margin is only so wide - the first note is at MARGIN_LEFT - so the column
    does not move further than it has to.
    """
    half = notation.TIME_SIG_HALF_W_SP * sp
    x = max(left_x + round(sp * 4.6),
            left_x + 8 + round(clef_ink_w(sp) + (TIME_SIG_GAP_SPACES * sp + half)))
    if key_end:
        x = max(x, key_end + TIME_SIG_GAP_SPACES * sp + half)
    return x


def header_margin(left_x: float, sp: float, fifths: int) -> float:
    """
    Where the first note of a system stands: past the clef, the key signature and
    the time signature, or at MARGIN_LEFT when they all fit before it.

    A key signature makes the head of the staff wider - the time signature has to
    stand clear of the sharps, and the first bar of the system clear of the digits.
    Music without a key signature keeps the margin it always had.
    """
    if not fifths:
        return MARGIN_LEFT
    key_end = key_sig_x(left_x, sp) + key_sig_width_sp(fifths, sp) * sp
    end = time_sig_x(left_x, sp, key_end) + notation.TIME_SIG_HALF_W_SP * sp
    return max(MARGIN_LEFT, end + FIRST_NOTE_GAP_SPACES * sp)


def draw_arpeggio_wavy_line(surf: pygame.Surface, x: float, y_top: float, y_bottom: float, s: float, color: Tuple[int, int, int]):
    """Draw a vertical squiggly arpeggio line ('con giun') to the left of a rolled chord."""
    if y_bottom <= y_top:
        return
    amp = s * 0.42
    wave_h = s * 0.7
    y_curr = y_bottom
    pts = []
    phase = 0
    while y_curr >= y_top - 2:
        dx = amp if (phase % 2 == 0) else -amp
        pts.append((round(x + dx), round(y_curr)))
        y_curr -= wave_h / 2
        phase += 1
    line_w = max(1, int(s * 0.18))
    if len(pts) >= 2:
        pygame.draw.lines(surf, color, False, pts, line_w)
    # Arrowhead at top
    if pts:
        top_x, top_y = pts[-1]
        ah_pts = [
            (top_x, top_y - round(s * 0.45)),
            (top_x - round(s * 0.35), top_y + round(s * 0.2)),
            (top_x + round(s * 0.35), top_y + round(s * 0.2)),
        ]
        pygame.draw.polygon(surf, color, ah_pts)


def get_octave_transposition(n: Note, clef: str) -> Tuple[int, int, str]:
    """
    Returns (step_draw, octave_shift, label_text) for a note.
    octave_shift is in octaves (-2, -1, 0, +1, +2).
    Thresholds are set so normal piano ranges use standard ledger lines (easy to read),
    and 8va/8vb is reserved for extreme pitches (> 3-4 ledger lines).
    """
    step, _ = written_step(n)
    if clef == CLEF_TREBLE:
        if n.pitch >= 100:            # E7 and above (> 6 ledger lines)
            return step - 14, -2, "15ma"
        elif n.pitch >= 88:           # E6 and above (> 3 ledger lines)
            return step - 7, -1, "8va"
    elif clef == CLEF_BASS:
        if n.pitch <= 16:             # E0 and below (> 7 ledger lines)
            return step + 14, 2, "15mb"
        elif n.pitch <= 28:           # E1 and below (> 4 ledger lines)
            return step + 7, 1, "8vb"
    return step, 0, ""


def resolve_octave_transpositions(sys_notes: List[Note], info: dict, max_gap: float = 2.0) -> None:
    """
    Ensures phrase-level octave consistency: any note falling within an ottava
    bracket is transposed along with the phrase.

    When extreme pitches trigger an octave bracket (8va/15ma or 8vb/15mb), an
    engraver keeps all contiguous notes of that melodic phrase under the same
    bracket rather than breaking or omitting transposition for intermediate notes.
    """
    group_counter = 0
    for clef in (CLEF_TREBLE, CLEF_BASS):
        c_notes = [n for n in sys_notes if info[id(n)]["clef"] == clef]
        c_notes.sort(key=lambda n: (n.start, n.pitch))
        if not c_notes:
            continue

        def is_compatible(n: Note, target_shift: int) -> bool:
            p = n.pitch
            if clef == CLEF_TREBLE:
                if target_shift == -1:    # 8va
                    return p >= 72        # C5 and above
                elif target_shift == -2:  # 15ma
                    return p >= 88        # E6 and above
            elif clef == CLEF_BASS:
                if target_shift == 1:     # 8vb
                    return p <= 48        # C3 and below
                elif target_shift == 2:   # 15mb
                    return p <= 28        # E1 and below
            return False

        i = 0
        n_len = len(c_notes)
        while i < n_len:
            sh = info[id(c_notes[i])]["octave_shift"]
            if sh == 0:
                i += 1
                continue

            target_shift = sh
            target_label = info[id(c_notes[i])]["label"]
            last_trigger_idx = i
            j = i + 1

            while j < n_len:
                prev_n = c_notes[j - 1]
                curr_n = c_notes[j]
                if curr_n.start - prev_n.start > max_gap:
                    break
                if not is_compatible(curr_n, target_shift):
                    break
                curr_sh = info[id(curr_n)]["octave_shift"]
                if curr_sh != 0 and curr_sh != target_shift:
                    break
                if curr_sh == target_shift:
                    last_trigger_idx = j
                j += 1

            group_counter += 1
            final_phrase = c_notes[i : last_trigger_idx + 1]

            def is_extreme(n: Note, target_shift: int) -> bool:
                p = n.pitch
                if abs(target_shift) >= 2:
                    return True   # 15ma / 15mb are always extreme (> 6-7 ledger lines)
                if target_shift == -1:
                    return p >= 96  # C7 and above (> 5 ledger lines)
                if target_shift == 1:
                    return p <= 21  # A0 and below (> 5 ledger lines)
                return False

            # An isolated single note in the 3-4 ledger line range should use standard
            # ledger lines rather than a momentary, flickering 8va/8vb bracket.
            if len(final_phrase) == 1 and not any(is_extreme(fn, target_shift) for fn in final_phrase):
                for fn in final_phrase:
                    step_nat, _ = written_step(fn)
                    info[id(fn)]["step_draw"] = step_nat
                    info[id(fn)]["octave_shift"] = 0
                    info[id(fn)]["label"] = ""
                i += 1
                continue

            for fn in final_phrase:
                step_nat, _ = written_step(fn)
                step_shift = 7 if abs(target_shift) == 1 else 14
                if target_shift < 0:
                    step_draw = step_nat - step_shift
                else:
                    step_draw = step_nat + step_shift
                info[id(fn)]["step_draw"] = step_draw
                info[id(fn)]["octave_shift"] = target_shift
                info[id(fn)]["label"] = target_label
                info[id(fn)]["octave_group_id"] = group_counter

            i = last_trigger_idx + 1


def chord_spread(beat_len: float) -> float:
    """
    How far apart two notes may start and still count as one chord.

    A chord written on a piano roll is often a hair spread out: the same chord
    comes back from a file a few tens of milliseconds apart instead of at exactly
    one moment (and a strummed chord is spread on purpose). That spread is far
    smaller than a rhythm, so anything inside this much of a beat is one chord.
    Notes a real step apart in the beat - a run of sixteenths, say - are further
    apart than this and stay separate notes.
    """
    return max(0.035, 0.2 * beat_len)


def group_chords(sorted_notes: List[Note], beat_len: float) -> List[List[Note]]:
    """Split notes in time order into chords: each chord is one moment's worth of notes."""
    tol = chord_spread(beat_len)
    clusters: List[List[Note]] = []
    curr: List[Note] = []
    for n in sorted_notes:
        if not curr:
            curr.append(n)
        elif n.start - curr[0].start < tol:
            # A chord consists of notes that sound simultaneously (overlap in time).
            # If n starts after notes in curr have already ended, it is a sequential
            # note (e.g. a rapid grace note run), not part of the same chord.
            if n.start > curr[0].start and all(prev.end <= n.start + 0.025 for prev in curr):
                clusters.append(curr)
                curr = [n]
            else:
                curr.append(n)
        else:
            clusters.append(curr)
            curr = [n]
    if curr:
        clusters.append(curr)
    return clusters


def is_beamable(sub: List[Note], beat_of) -> bool:
    """True when a chord is short enough to be joined to its neighbours by a beam."""
    if any(n.is_rest for n in sub):
        return False
    return printed_value(sub[0], sub[0].duration, beat_of(sub[0].start)) in (
        EIGHTH, DOTTED_EIGHTH, SIXTEENTH)


def group_beams(chords: List[List[Note]], notes_info: dict, beat_of, measure_of) -> List[Tuple[str, List[List[Note]]]]:
    """
    Decide which chords a beam joins.

    This follows what an engraver does: a beam gathers the short notes of one beat on
    one hand of one staff, and stops there. A rest, a longer note, a bar line or the
    start of the next beat breaks the group, and a group never grows past
    MAX_BEAM_CHORDS notes - so a beam always reads as one beat's worth of notes and
    never drags along notes that have nothing to do with each other.
    """
    beam_groups: List[Tuple[str, List[List[Note]]]] = []
    for hand in (RIGHT, LEFT):
        for c in (CLEF_TREBLE, CLEF_BASS):
            on_staff = [[n for n in sub if n.hand == hand and notes_info[id(n)]["clef"] == c] for sub in chords]
            on_staff = [sub for sub in on_staff if sub]

            curr: List[List[Note]] = []
            for sub in on_staff:
                n0 = sub[0]
                b_dur = beat_of(n0.start)
                m = measure_of.get(id(n0))
                beat_i = int((n0.start - m.start_t) / b_dur) if m is not None else None

                # A grace note hangs off the note it leans on - it is drawn on its own,
                # with a stroke through its stem, so it never joins a beam and ends one.
                is_grace = any(notes_info[id(n)].get("grace") for n in sub)

                joined = False
                if curr and not is_grace and is_beamable(sub, beat_of):
                    prev = curr[-1][0]
                    p_m = measure_of.get(id(prev))
                    p_beat = int((prev.start - p_m.start_t) / b_dur) if p_m is not None else None
                    # straight on from the note before: no rest and no longer note in between
                    gap_ok = (n0.start - prev.start) <= max(prev.duration, n0.duration) * 1.6
                    joined = (p_m is not None and p_m.index == m.index and p_beat == beat_i
                              and gap_ok and len(curr) < MAX_BEAM_CHORDS)

                if joined:
                    curr.append(sub)
                    continue
                if len(curr) >= 2:
                    beam_groups.append((c, curr))
                curr = [] if is_grace else ([sub] if is_beamable(sub, beat_of) else [])
            if len(curr) >= 2:
                beam_groups.append((c, curr))
    return beam_groups


def group_grace_beams(grace_runs, notes_info, beat_of):
    """
    The grace runs MuseScore would beam: each run that holds two or more grace chords
    short enough to carry flags (eighth or shorter) is joined by a small beam at
    graceNoteMag, and one stroke goes through that beam instead of a stroke on every
    stem (BeamLayout::beamGraceNotes, beamlayout.cpp:553-620; the `beam` branch of
    TLayout::layoutStemSlash, tlayout.cpp:5423).

    Returns one (run, clusters) per beamed run: `run` is the graces in played order and
    `clusters` the chord groups the beam actually joins (a run may open with a quarter
    grace, which is left out and breaks nothing - it simply has no beam).
    """
    beamed = []
    for run, main in grace_runs:
        # notes struck together share an x, like the notes of any chord
        clusters: List[List[Note]] = []
        for g in run:
            if clusters and abs(clusters[-1][0].start - g.start) <= 0.05:
                clusters[-1].append(g)
            else:
                clusters.append([g])
        beamable = [c for c in clusters
                    if is_beamable(c, beat_of) and notes_info[id(c[0])].get("grace")]
        if len(beamable) >= 2:
            beamed.append((run, beamable))
    return beamed


class NoteHeads:
    """
    Where every notehead of a system sits, and a quick way to ask which of them a piece of
    ink could be printed on.

    The boxes are one (x0, x1, y0, y1, id(note)) each, on either staff, sorted by their
    left edge - they are all about a notehead wide, so the right edges are sorted too -
    which turns "what could this stem or bar run over" into a short run of them instead of
    a walk over the whole system. The staff is redrawn every frame and a beam is scored
    against several candidate heights, so that walk is what the frame time is made of.
    """

    def __init__(self, boxes):
        self.boxes = sorted(boxes)
        self._left = [b[0] for b in self.boxes]
        self._widest = max((b[1] - b[0] for b in self.boxes), default=0.0)

    def near(self, x0: float, x1: float):
        """The noteheads a piece of ink from `x0` to `x1` could reach."""
        lo = bisect.bisect_left(self._left, x0)
        hi = bisect.bisect_right(self._left, x1 + self._widest)
        return [b for b in self.boxes[lo:hi] if b[1] >= x0]


def ink_overlap(x0: float, x1: float, y0: float, y1: float, boxes, skip) -> float:
    """
    How much of a piece of ink would be printed on top of the noteheads in `boxes`, and
    `skip` (the ids of the notes the ink belongs to) is left out of it.

    Overlap is measured in pixels, so the two directions a stem or a beam can take can be
    compared with each other. `boxes` is what `NoteHeads.near` hands back - a few noteheads
    around the ink, not the whole system.
    """
    total = 0.0
    for hx0, hx1, hy0, hy1, nid in boxes:
        if nid in skip or x1 <= hx0 or hx1 <= x0:
            continue
        total += max(0.0, min(y1, hy1) - max(y0, hy0))
    return total


# How far a beam may be pushed outboard to get off a notehead, in staff spaces. MuseScore
# slides a beam clear of what it runs over (beamlayout.cpp, Beam::calculateBeamStart); this
# is the bound on that, and past it the beam keeps the least bad height it found.
BEAM_SLIDE_SPACES = 2.0

# What a bar that cannot stay inside the staff panel costs. The panel is the paper: a bar
# with its outer half cut off is worse than a stem that runs over another hand's notehead,
# so a direction whose bar fits wins over one whose bar does not. It is still a cost rather
# than a veto - the stems give way for the panel before the bar leaves it, and only when
# even the shortest stem the stack allows would not fit is a direction rejected.
BEAM_OFF_PANEL_COST = 1e6

# The least white a beam leaves between its stack of bars and the group's own noteheads when
# the panel has to shorten the stems. MuseScore never takes a beamed stem below
# min_stem_spaces; the panel is the one thing that can force it, and even then the notehead
# must not sit on the bars.
BEAM_MIN_HEAD_GAP_SP = 0.35


def layout_beam(stops, default_up, reach, beam_w, gap, nw, boxes, own, s, panel=None):
    """
    Where a beam's bar goes and which way its stems point: (stems_up, bar_y).

    `stops` is one (x, y_top, y_bot, is16) per chord of the group - the note's own x and
    the top and bottom of its noteheads - and `default_up` the direction the engraver
    asked for (`stem_up_for`). `boxes` is the noteheads near the group (`NoteHeads.near`),
    and `panel` the staff panel's (top, bottom) when there is one, so a bar cannot be pushed
    off it.

    A beam is a horizontal line, and the two hands draw at the same time, so the bar has
    to keep off the noteheads of the chords it does not belong to - on either staff,
    since a bar reaching out of one staff can cross the gap and land on the other. It is
    placed one stem length (more for a stack of bars) off the notes' own edge, on the
    side the stems point to, and pushed a little further that way if something is in the
    way. When it still lands on a note - which is what happens when the wrong side of one
    hand's chord is exactly the height the other hand's note is drawn at - the stems are
    turned the other way and the bar goes with them: the bar is only there to join the
    group's stems, and the other side of the notes is the side the other hand is not on.

    The panel is not a veto, only a bound. If the side that is clear asks for a bar that
    pokes out of the panel, the stems give up just enough of their length to put the bar on
    the edge - as long as the stack of bars still clears the group's own noteheads. A beam
    with its bar cut off at the edge of the paper reads worse than a slightly short stem,
    but better than a stem printed across the other hand's note, so the clear side wins
    whenever it can be drawn at all.

    The stems and the second bar of a run of sixteenths are counted too, not just the
    outer bar, so the direction is chosen on what the whole beam would cover.
    """
    half = beam_w / 2.0
    sw = max(1, round(STEM_WIDTH_SPACES * s)) / 2.0
    step = max(1, round(s * 0.25))
    slide = max(step, round(s * BEAM_SLIDE_SPACES))
    any16 = any(st[3] for st in stops)
    bars = 2 if any16 else 1

    # The shortest a stem may get: the whole stack of bars, the notehead, and the little
    # white that keeps the head off the bars - measured from the notehead's centre.
    head_half = notation.NOTEHEAD_H_SP * s / 2.0
    stack = bars * BEAM_STEP_SPACES - 0.25
    tight_reach = head_half + (stack + BEAM_WIDTH_SPACES / 2.0 + BEAM_MIN_HEAD_GAP_SP) * s

    def stem_x(x, up):
        """The x the stems stand on: the notehead's far edge, as `draw_staff_note` puts it."""
        return round(x + nw - 1) if up else round(x - nw + 1)

    def run_on(tip, up):
        """What a bar at `tip`, with its stems `up`, would be printed on."""
        # The second bar of a sixteenth run sits between the first one and the notes, so the
        # stems run through it: that is where MuseScore stacks it, and where the reach the
        # caller asked for already leaves room for it.
        off = gap if up else -gap
        left, right = stem_x(stops[0][0], up), stem_x(stops[-1][0], up)
        total = ink_overlap(left, right, tip - half, tip + half, boxes, own)
        if any16:
            total += ink_overlap(left, right, tip + off - half, tip + off + half, boxes, own)
        for x, y_top, y_bot, _ in stops:
            sx = stem_x(x, up)
            far = y_bot if up else y_top          # the stem starts at the chord's far edge
            total += ink_overlap(sx - sw, sx + sw, min(far, tip), max(far, tip), boxes, own)
        return total

    def clamp(tip):
        """The nearest bar height that keeps the whole bar inside the staff panel."""
        if panel is None:
            return tip
        lo = math.ceil(panel[0] + half)
        hi = math.floor(panel[1] - half)
        return max(lo, min(hi, tip))

    def clears_own(tip, up):
        """True when the stems still hold the group's own noteheads off the bar stack."""
        if up:
            return tip <= min(st[1] for st in stops) - tight_reach
        return tip >= max(st[2] for st in stops) + tight_reach

    def place(up):
        """The bar's height with the stems `up`, and what it would cover there."""
        base = round(min(st[1] for st in stops) - reach) if up \
            else round(max(st[2] for st in stops) + reach)
        away = -1 if up else 1                   # outboard: further from the group's notes
        best, best_hit = None, None
        for k in range(0, slide + step, step):
            tip = clamp(round(base + away * k))
            if not clears_own(tip, up):
                continue
            hit = run_on(tip, up)
            if best_hit is None or hit < best_hit:
                best, best_hit = tip, hit
            if hit <= 0.0:
                return best, best_hit
        if best is not None:
            return best, best_hit
        # Even the shortest stem the bars allow does not fit in the panel. Nothing this
        # direction can draw is acceptable, so it loses to any side that can be drawn.
        return base, run_on(base, up) + BEAM_OFF_PANEL_COST

    first_tip, first_hit = place(default_up)
    other_tip, other_hit = place(not default_up)
    if other_hit < first_hit:
        return not default_up, other_tip
    return default_up, first_tip


def place_stem(up, x, y_top, y_bot, stem_len, flags, sp, boxes, own):
    """
    What a lone stem at `x`, with its flags, would be printed on if it pointed `up`:
    the pixels it covers among the noteheads in `boxes` that are not its own chord's (so
    `boxes` is the short list `NoteHeads.near` hands back, not the whole system).

    A stem is 3.5 spaces long, so it reaches well past its own note. Usually the notes
    beside it are spread far enough not to care (compute_notes_info keeps them a notehead
    and a half apart), but where they are not - the two hands' notes drawn together where
    the clash offsets have run out of room - the stem runs straight across another hand's
    notehead. This is what tells the chord layout which way to turn it.
    """
    head_w = notation.NOTEHEAD_W_SP * sp
    ax, _ = notation.STEM_UP_SE if up else notation.STEM_DOWN_NW
    anchor = x - head_w / 2.0 + ax * sp         # draw_staff_note's stem anchor
    stem_w = max(1, round(notation.STEM_SP * sp)) / 2.0
    sx = anchor - (stem_w if up else 0.0)
    tip = round(y_top - stem_len) if up else round(y_bot + stem_len)
    far = y_bot if up else y_top                 # the stem starts at the chord's far edge
    total = ink_overlap(sx - stem_w, sx + stem_w, min(far, tip), max(far, tip), boxes, own)
    box = notation.flag_extent(sp, flags, up)
    if box is not None:
        total += ink_overlap(anchor + box[0], anchor + box[2],
                             tip + box[1], tip + box[3], boxes, own)
    return total


# A grace note: shorter than 0.15 of a beat and running straight into a note of at
# least 0.4 of a beat - the little notes a pianist flicks just before the beat. The
# bounds are of the beat, so they hold at any tempo: six tenths of a beat is shorter
# than a written sixteenth at every tempo, while 0.15 of one is well short of it too
# and a real pickup note comes out longer than that.
GRACE_MAX = 0.15      # of a beat: above this a note is written out, not a grace
GRACE_MAIN_MIN = 0.4  # of a beat: the length of the note a grace leans on


def find_grace_runs(notes: List[Note], beat_len: float) -> List[Tuple[List[Note], Note]]:
    """
    The grace runs of one system: (the grace notes in played order, the note they
    lead into), found before any layout so they can take their small size and their
    tight place against that note into every measurement that follows.

    A run is a chain of notes shorter than GRACE_MAX of a beat that follow each other
    without a breath (never overlapping by more than a hair) and end on a note of at
    least GRACE_MAIN_MIN of a beat. Only one hand on one staff leads to its own note,
    so a chord struck together with the main note is never taken for a grace.
    """
    runs: List[Tuple[List[Note], Note]] = []
    by_staff: dict = {}
    for n in notes:
        by_staff.setdefault((n.hand, clef_for_note(n)), []).append(n)
    for staff_notes in by_staff.values():
        staff_notes.sort(key=lambda n: (n.start, n.pitch))
        for i, main in enumerate(staff_notes):
            if main.duration < GRACE_MAIN_MIN * beat_len:
                continue
            run: List[Note] = []
            j = i - 1
            while j >= 0:
                g = staff_notes[j]
                nxt = staff_notes[j + 1]
                if g.duration >= GRACE_MAX * beat_len:
                    break
                # straight on with no breath between them - or struck together with the
                # next one, so the two are a grace chord and are small together
                same_onset = 0.0 <= nxt.start - g.start <= 0.05
                if not (same_onset or -0.02 <= nxt.start - g.end <= 0.08):
                    break
                run.append(g)
                j -= 1
            if run:
                run.reverse()
                runs.append((run, main))
    return runs


def drawn_steps(clusters: List[List[Note]], sys_notes: List[Note]) -> dict:
    """
    How high each note is really drawn: {id(note): {"clef", "step_draw", "octave_shift",
    "label"}} plus whatever octave group `resolve_octave_transpositions` gave it.

    The steps come from `get_octave_transposition`, then a phrase under an 8va/8vb keeps
    its transposition together. A shifted note that would land on another note of its own
    chord is put back on its written step: the two heads would otherwise sit on each other.
    `clusters` are the system's `group_chords` moments; only the vertical layout is worked
    out here, so the drawing and `StaffView.staff_geometry` agree exactly.
    """
    info = {}
    for cluster in clusters:
        for n in cluster:
            clef = clef_for_note(n)
            if n.is_rest:
                step_draw = TREBLE_MID_STEP if clef == CLEF_TREBLE else BASS_MID_STEP
                if getattr(n, "step", None) is not None:
                    step_draw = n.step
                oct_shift, label = 0, ""
            else:
                step_draw, oct_shift, label = get_octave_transposition(n, clef)
            info[id(n)] = {
                "clef": clef,
                "step_draw": step_draw,
                "octave_shift": oct_shift,
                "label": label,
                "is_rest": n.is_rest,
                "rest_type": n.printed or printed_value(n, n.duration, 0.5),
            }

    # Resolve phrase-level octave transpositions so intermediate notes under 8va/8vb
    # are transposed together with the phrase
    resolve_octave_transpositions(sys_notes, info)

    # Revoke an octave transposition if it causes notes in the same chord/clef to collide
    for cluster in clusters:
        by_clef = {CLEF_TREBLE: [], CLEF_BASS: []}
        for n in cluster:
            by_clef[info[id(n)]["clef"]].append(n)

        for c, c_notes in by_clef.items():
            if len(c_notes) <= 1:
                continue
            for n in c_notes:
                n_info = info[id(n)]
                if n_info["octave_shift"] != 0:
                    other_steps = [info[id(other)]["step_draw"] for other in c_notes if other is not n]
                    if any(abs(n_info["step_draw"] - ost) <= 1 for ost in other_steps):
                        step_nat, _ = written_step(n)
                        n_info["step_draw"] = step_nat
                        n_info["octave_shift"] = 0
                        n_info["label"] = ""

    return info


def vertical_extents(steps: dict, s: float) -> Tuple[float, float, float, float]:
    """
    (top_sp, inner_t_sp, inner_b_sp, bottom_sp): how far a system's ink reaches from its
    two middle lines, in staff spaces.

    The outer pair is how far the ink stands above the treble middle and below the bass
    one - the staff's own two lines are always there, so they never come closer than
    STAFF_EDGE_SP, and an 8va/15ma or 8vb/15mb bracket reaches OCTAVE_BRACKET_SP past its
    notes. The inner pair is how far the ink reaches into the gap between the staves (0
    when nothing does); that is what decides how far apart the two staves must stand.
    Stems and flags point towards their own staff's middle, away from the edges and away
    from the gap, so only the noteheads are counted.
    """
    head_half = notation.NOTEHEAD_H_SP / 2.0
    top_sp = bottom_sp = STAFF_EDGE_SP
    inner_t_sp = inner_b_sp = 0.0
    has_8va = has_8vb = False
    for i in steps.values():
        step = i["step_draw"]
        if i["clef"] == CLEF_TREBLE:
            above = (step - TREBLE_MID_STEP) * 0.5 + head_half
            below = (TREBLE_MID_STEP - step) * 0.5 + head_half
            top_sp = max(top_sp, above)
            inner_t_sp = max(inner_t_sp, below)
            has_8va = has_8va or i["octave_shift"] < 0
        else:
            above = (step - BASS_MID_STEP) * 0.5 + head_half
            below = (BASS_MID_STEP - step) * 0.5 + head_half
            bottom_sp = max(bottom_sp, below)
            inner_b_sp = max(inner_b_sp, above)
            has_8vb = has_8vb or i["octave_shift"] > 0
    if has_8va:
        top_sp += OCTAVE_BRACKET_SP
    if has_8vb:
        bottom_sp += OCTAVE_BRACKET_SP
    return top_sp, inner_t_sp, inner_b_sp, bottom_sp


def compute_notes_info(sys_notes: List[Note], sys_start: float, sys_duration: float, usable_w: float,
                       margin_left: float, s: float, beat_len: float = 0.5,
                       bar_times: Optional[List[float]] = None,
                       accidentals: Optional[dict] = None) -> dict:
    """
    Computes rendering info for notes in a system:
    x coordinate (with clashing/overlap offset), clef, step_draw, octave_shift, label_text.
    Returns dict mapping id(n) -> dict(...)

    Only a real clash moves a note sideways:
    - notes a second (or unison) apart in the same clef, which would overlap head-on;
    - notes that follow in time so closely that the second one's sharp or the first one's
      dot / flag would run into it;
    - a note whose ink would be printed on a bar line (MuseScore keeps noteBarDistance
      between the two, styledef.cpp). `bar_times` is where the system's bar lines stand -
      the times `StaffView.draw` draws them at; without them no note is moved for a bar.
    The other notes of a chord stay on the same x, so it reads as a chord, not a staircase.

    `accidentals` is the plan of which notes print a sign (plan_accidentals): the room a
    note's accidental takes is the room the sign it actually prints takes.
    """
    info = {}
    if not sys_notes:
        return info

    def acc_of(n: Note) -> Optional[int]:
        """The sign this note prints beside it (None: none)."""
        if accidentals is not None:
            return accidentals.get(id(n))
        alt = written_step(n)[1]
        return alt if alt else None

    nw = round(s * 0.75)
    right_limit = margin_left + usable_w
    sorted_notes = sorted(sys_notes, key=lambda n: (n.start, n.pitch))

    # A chord is one moment: its notes share one x, even when the file staggered
    # their onsets a little, so it is drawn as a chord and not as a little cascade.
    clusters = group_chords(sorted_notes, beat_len)

    # The grace runs come first: marked before anything is spaced, so their small
    # size (graceNoteMag) reaches the clash offsets, the gaps and their own place.
    grace_runs = find_grace_runs(sorted_notes, beat_len)
    grace_ids = {id(g) for run, _main in grace_runs for g in run}

    # The vertical steps first: clef, drawn step and octave shift (drawn_steps), then the
    # x each note stands at and the rest of what the drawing needs.
    info = drawn_steps(clusters, sys_notes)
    for cluster in clusters:
        chord_t = cluster[0].start
        chord_x = margin_left + (chord_t - sys_start) / sys_duration * usable_w
        for n in cluster:
            info[id(n)].update({
                "n": n,
                "base_x": chord_x,
                "x": chord_x,
                "grace": id(n) in grace_ids,
                "value": printed_value(n, n.duration, beat_len),
            })

    # Process each cluster for x-offsets when notes clash in same clef
    for cluster in clusters:
        by_clef = {CLEF_TREBLE: [], CLEF_BASS: []}
        for n in cluster:
            c = info[id(n)]["clef"]
            by_clef[c].append(n)

        for c, c_notes in by_clef.items():
            if len(c_notes) <= 1:
                continue

            # Sort notes in clef by step_draw
            c_notes_sorted = sorted(c_notes, key=lambda n: info[id(n)]["step_draw"])
            for i in range(1, len(c_notes_sorted)):
                prev_n = c_notes_sorted[i - 1]
                curr_n = c_notes_sorted[i]
                p_info = info[id(prev_n)]
                c_info = info[id(curr_n)]

                # Only a second (step_diff == 1) or a unison (0) really clashes: the two
                # noteheads would sit on top of each other. Any wider interval already
                # stacks vertically, so it keeps the chord's common x.
                step_diff = abs(c_info["step_draw"] - p_info["step_draw"])
                if step_diff <= 1:
                    min_x = p_info["x"] + 1.8 * nw
                    if c_info["x"] < min_x:
                        c_info["x"] = min_x

    # Notes that follow in time: keep enough air for the second note's accidental (♯)
    # and the first one's dot or flag, so they don't overlap at fast passages.
    def note_head_reach(n: Note) -> float:
        """How far to the left of its x a note's ink reaches (its accidental, if any)."""
        mag = notation.GRACE_MAG if id(n) in grace_ids else 1.0
        acc_w = accidental_width_of(acc_of(n))
        return (acc_w * s + notation.ACC_DIST_SP * s) * mag if acc_w else 0.0

    def note_tail_reach(n: Note) -> float:
        """How far to the right of its x a note's ink reaches (notehead, dot, flag)."""
        if id(n) in grace_ids:
            # a grace is drawn at graceNoteMag and carries no flag or dot
            head = note_shape(n.duration, beat_len)[0]
            l, _t, r, _b = notation.HEAD_BOX[head]
            return (r - l) * s * 0.5 * notation.GRACE_MAG
        tail = nw
        nv = printed_value(n, n.duration, beat_len)
        if nv in (DOTTED_HALF, DOTTED_QUARTER, DOTTED_EIGHTH):
            tail = nw + s * 0.6
        elif nv in (EIGHTH, SIXTEENTH):
            tail = nw + s * 0.9
        return tail

    def cluster_head(cluster: List[Note]) -> float:
        """How far to the left of their x the notes of `cluster` reach (an accidental)."""
        return max((note_head_reach(n) for n in cluster), default=0.0)

    def cluster_tail(cluster: List[Note]) -> float:
        """How far to the right of their x the notes of `cluster` reach (notehead, dot, flag)."""
        return max((note_tail_reach(n) for n in cluster), default=nw)

    min_gap = nw + 1.0

    # Which grace run leans on which note: a run moves with its note, and the note carries
    # the run's clearance of a bar line with it.
    graces_of = {id(main): run for run, main in grace_runs}

    def grace_left_ink(n: Note, x: float) -> float:
        """How far to the left of its x a grace note's ink reaches."""
        head = note_shape(n.duration, beat_len)[0]
        l, _t, r, _b = notation.HEAD_BOX[head]
        mag = notation.GRACE_MAG
        half = (r - l) * s * 0.5 * mag
        if acc_of(n) is not None:
            acc_w = accidental_extra_of(acc_of(n))
            return x - (half + (r - l) * s * mag + notation.ACC_DIST_SP * s * mag
                        + acc_w * s * mag)
        return x - half

    def squeeze_into_measures() -> None:
        """Fit each measure's notes between the bar lines that enclose it.

        The spacing above only ever moves a note aside to keep it clear of its neighbours,
        and at a crowded measure that can push the last notes past the bar line that ends
        it: a note of one measure is then printed on the next measure's side of the bar,
        and a beam runs through the bar. A grace run at a bar line pushes the same way: it
        takes its room from the measure it opens, so the notes after it travel right with
        it. So each measure is squeezed from its closing bar line backwards - the last note
        stands a noteBarDistance off the bar, each note before it keeps its minimum gap to
        the one after, down to the bar line that opens the measure. A note carries the
        grace run leaning on it along, and never moves further left than the run's own
        clearance allows. A measure with no room left closes up, as an engraver's does.
        """
        if not bar_times:
            return
        bar_gap = NOTE_BAR_DISTANCE_SP * s

        def head_of(cluster: List[Note]) -> float:
            """The cluster's left ink, the grace run leaning on it included."""
            head = cluster_head(cluster)
            for n in cluster:
                run = graces_of.get(id(n))
                if run:
                    nx = info[id(n)]["x"]
                    head = max(head, nx - min(grace_left_ink(g, info[id(g)]["x"])
                                               for g in run))
            return head

        left_edge = margin_left            # the paper's edge, then each bar line
        measure_start = sys_start
        for k, bar_t in enumerate(bar_times):
            bx = margin_left + (bar_t - sys_start) / sys_duration * usable_w
            mine = [c for c in clusters
                    if measure_start - 1e-9 <= c[0].start < bar_t - 1e-9]
            right = bx - bar_gap
            for c in reversed(mine):
                x_min = min(info[id(n)]["x"] for n in c)
                x_max = max(info[id(n)]["x"] for n in c)
                # A grace is placed against the note it leans on, not by this spacing: it
                # stays where it stands and the note before it only keeps off its ink.
                if not any(id(n) in grace_ids for n in c):
                    over = x_max + cluster_tail(c) - right
                    if over > 0:
                        # never left of the paper, the opening bar line, or the grace run
                        if k == 0:
                            over = min(over, x_min - head_of(c) - margin_left)
                        else:
                            over = min(over, x_min - head_of(c) - (left_edge + bar_gap))
                        if over > 0:
                            for n in c:
                                info[id(n)]["x"] -= over
                                for g in graces_of.get(id(n), ()):
                                    info[id(g)]["x"] -= over
                            x_min -= over
                right = min(right, x_min - head_of(c) - min_gap)
            left_edge = bx
            measure_start = bar_t

    # A bar line is music too: a note may not be printed on top of it. MuseScore keeps
    # noteBarDistance (1.5 spaces, styledef.cpp) between a note's ink and the bar line, so a
    # note at the start of a measure stands off it by its accidental's width plus that gap.
    if bar_times:
        bar_gap = NOTE_BAR_DISTANCE_SP * s

        def cluster_left_ink(cluster: List[Note]) -> float:
            """How far to the left of their x the notes of `cluster` really reach."""
            reach = 0.0
            for n in cluster:
                head = note_shape(n.duration, beat_len)[0]
                left, _top, right, _bottom = notation.HEAD_BOX[head]
                mag = notation.GRACE_MAG if id(n) in grace_ids else 1.0
                half = (right - left) * s / 2.0 * mag
                # ledger lines reach a legerLineExtension past the head on each side
                r = half + notation.LEDGER_EXT_SP * s * mag
                if acc_of(n) is not None:        # the accidental, left of the head
                    r = max(r, half + (right - left) * s * mag + notation.ACC_DIST_SP * s * mag
                            + accidental_extra_of(acc_of(n)) * s * mag)
                reach = max(reach, r)
            return reach

        for cluster in clusters:
            # the bar line this cluster starts after (a note exactly on it counts as after)
            before = [bt for bt in bar_times if bt <= cluster[0].start + 1e-9]
            if not before:
                continue
            bx = margin_left + (max(before) - sys_start) / sys_duration * usable_w
            left_ink = min(info[id(n)]["x"] for n in cluster) - cluster_left_ink(cluster)
            want = bx + bar_gap
            if left_ink < want:
                x_max = max(info[id(n)]["x"] for n in cluster)
                shift = min(want - left_ink, max(0.0, right_limit - x_max))
                if shift > 0:
                    for n in cluster:
                        info[id(n)]["x"] += shift

    for i in range(1, len(clusters)):
        prev_cluster, curr_cluster = clusters[i - 1], clusters[i]
        # A grace note's place is against the note it leans on, not at its own moment:
        # the run is placed after all of this, so here it neither pushes nor is pushed.
        if any(id(n) in grace_ids for n in prev_cluster) or any(id(n) in grace_ids for n in curr_cluster):
            continue
        # Notes on different staves are nowhere near each other: nothing to spread.
        prev_clefs = {info[id(n)]["clef"] for n in prev_cluster}
        curr_clefs = {info[id(n)]["clef"] for n in curr_cluster}
        if not prev_clefs & curr_clefs:
            continue

        head = cluster_head(curr_cluster)
        prev_right = max(info[id(n)]["x"] for n in prev_cluster) + cluster_tail(prev_cluster)
        curr_left = min(info[id(n)]["x"] for n in curr_cluster) - head
        if curr_left < prev_right + min_gap:
            shift = (prev_right + min_gap) - curr_left
            curr_x_min = min(info[id(n)]["x"] for n in curr_cluster)
            shift = min(shift, max(0.0, right_limit - curr_x_min))
            if shift > 0:
                for n in curr_cluster:
                    info[id(n)]["x"] += shift

    # A note just *before* a bar line: the next bar may not cut through its flag or its
    # dot either. Proportional spacing can leave only a couple of pixels there, so the
    # note is pulled left - as far as the note before it leaves room, and no further.
    def pull_left_of(i: int, cluster: List[Note], bx: float):
        right_ink = max(info[id(n)]["x"] for n in cluster) + cluster_tail(cluster)
        over = right_ink - (bx - NOTE_BAR_DISTANCE_SP * s)
        if over <= 0:
            return
        room = float("inf")
        clefs = {info[id(n)]["clef"] for n in cluster}
        for j in range(i - 1, -1, -1):
            prev = clusters[j]
            if clefs & {info[id(n)]["clef"] for n in prev}:
                room = (min(info[id(n)]["x"] for n in cluster)
                        - max(info[id(n)]["x"] for n in prev)
                        - cluster_tail(prev) - min_gap)
                break
        shift = min(over, max(0.0, room))
        if shift > 0:
            for n in cluster:
                info[id(n)]["x"] -= shift

    if bar_times:
        for i, cluster in enumerate(clusters):
            after = [bt for bt in bar_times if bt > cluster[0].start + 1e-9]
            if after:
                pull_left_of(i, cluster, margin_left
                             + (min(after) - sys_start) / sys_duration * usable_w)

    # The system's own ending bar line: a note at the very end of the system is drawn at
    # the paper's edge, right on the bar, so it gives up the noteBarDistance too.
    for i, cluster in enumerate(clusters):
        pull_left_of(i, cluster, right_limit - 0.5)

    # Grace notes last: small (graceNoteMag) and tucked against the note they lean on,
    # at MuseScore's distances - graceToMainNoteDist between the run and that note,
    # graceToGraceNoteDist between the grace notes, each measured from edge to edge of
    # the ink on both sides (TLayout::layoutGraceNotesGroup, horizontalspacing.cpp:1512).
    # Like MuseScore's, the run is laid out from its main note leftwards, so it belongs
    # to that note's measure even when the file's own timings put its onsets a hair
    # before the bar line.
    if bar_times:
        bar_gap = NOTE_BAR_DISTANCE_SP * s
    for graces, main in grace_runs:
        m_x = info[id(main)]["x"]
        m_clef = info[id(main)]["clef"]
        m_head = note_shape(main.duration, beat_len)[0]
        ml, _mt, mr, _mb = notation.HEAD_BOX[m_head]
        if acc_of(main) is not None:
            m_left = ((mr - ml) * s * 1.5 + notation.ACC_DIST_SP * s
                      + accidental_extra_of(acc_of(main)) * s)   # its accidental sits left too
        else:
            m_left = (mr - ml) * s * 0.5
        # grace struck together share one x, like the notes of any chord
        chord_groups: List[List[Note]] = []
        for g in reversed(graces):
            if chord_groups and abs(chord_groups[-1][0].start - g.start) <= 0.05:
                chord_groups[-1].append(g)
            else:
                chord_groups.append([g])

        def lay_out(right_edge: float) -> List[Tuple[Note, float]]:
            """Place the run leftwards from `right_edge`, in MuseScore's distances."""
            cursor = right_edge - notation.GRACE_TO_MAIN_SP * s
            placed: List[Tuple[Note, float]] = []
            for grp in chord_groups:
                cx = cursor - max(note_tail_reach(n) for n in grp)
                for g in grp:
                    placed.append((g, cx))
                cursor = (max(grace_left_ink(n, cx) for n in grp)
                          - notation.GRACE_TO_GRACE_SP * s)
            return placed

        placed = lay_out(m_x - m_left)
        # How far the run may reach left: the ink before it (the cluster just before
        # the main note, or, above all, the bar line that opens the main note's
        # measure - the run is the main note's, never the previous measure's).
        run_notes = {id(g) for g in graces}
        cluster_idx = next((i for i, cl in enumerate(clusters)
                            if any(c is main for c in cl)), -1)
        floor = None
        prev_idx = cluster_idx - 1
        while prev_idx >= 0 and any(id(n) in run_notes for n in clusters[prev_idx]):
            prev_idx -= 1                 # the run's own notes are not "the note before"
        if prev_idx >= 0:
            prev = [n for n in clusters[prev_idx] if info[id(n)]["clef"] == m_clef]
            if prev:
                floor = (max(info[id(n)]["x"] for n in prev)
                         + max(note_tail_reach(n) for n in prev) + 1.0)
        if bar_times:
            opened = [bt for bt in bar_times if bt <= main.start + 1e-9]
            if opened:
                bar_x = margin_left + (max(opened) - sys_start) / sys_duration * usable_w
                want = bar_x + bar_gap
                floor = want if floor is None else max(floor, want)

        shift = 0.0
        if floor is not None:
            left_ink = min(grace_left_ink(g, cx) for g, cx in placed)
            if left_ink < floor:
                shift = floor - left_ink
        if shift > 0:
            # Not enough room left of the main note for the run: the run and everything
            # from the main note onwards go right together, exactly as far as the bar line
            # (or the note before) demands - never further, and never off the paper. The
            # notes after the main note travel with it, or the main note would be printed
            # on top of the next one.
            moving = [cl for cl in clusters[cluster_idx:]]
            rightmost = max(info[id(n)]["x"] + note_tail_reach(n)
                            for cl in moving for n in cl)
            # A grace note may not be cut by the bar line, so the run keeps its room even
            # when that means using the right margin: the ink of a note at the very end of
            # the system gives up noteBarDistance before a grace gives up its place.
            headroom = (right_limit + MARGIN_RIGHT) - rightmost
            shift = min(shift, max(0.0, headroom))
            for cl in moving:
                for n in cl:
                    info[id(n)]["x"] += shift
            placed = [(g, cx + shift) for g, cx in placed]
        for g, cx in placed:
            info[id(g)]["x"] = cx

    # The grace runs above pushed the notes after them right (a run at a bar line takes its
    # room from the measure it opens), so the fit is made now, with every note and every
    # grace run standing in its final place.
    squeeze_into_measures()

    return info


def draw_dashed_line(surf: pygame.Surface, color: Tuple[int, int, int], start_pos: Tuple[float, float], end_pos: Tuple[float, float], width: int = 1, dash_len: int = 4, space_len: int = 3):
    x1, y1 = start_pos
    x2, y2 = end_pos
    dist = math.hypot(x2 - x1, y2 - y1)
    if dist < 1e-3:
        return
    dx = (x2 - x1) / dist
    dy = (y2 - y1) / dist
    curr = 0.0
    while curr < dist:
        next_c = min(curr + dash_len, dist)
        p1 = (round(x1 + dx * curr), round(y1 + dy * curr))
        p2 = (round(x1 + dx * next_c), round(y1 + dy * next_c))
        pygame.draw.line(surf, color, p1, p2, width)
        curr += dash_len + space_len


# How far the bracket's dashed line keeps off the ink it covers. A stem turned the other
# way pokes past its notehead, so the line is measured from what the group really draws
# (heads, stems, beams) and then given this much clear air above or below it.
OCTAVE_GAP_SPACES = 0.75


def draw_octave_brackets(surf: pygame.Surface, notes_info: dict, sys_notes: List[Note],
                         treble_mid_y: float, bass_mid_y: float, s: float,
                         local_rect: Optional[pygame.Rect] = None):
    """
    The 8va / 15ma and 8vb / 15mb brackets over each group of transposed notes.

    A group holds notes shifted by their own amount (get_octave_transposition), so it
    covers a range of drawn heights - a chord of E7 + C6 puts the E7 at -1.5 sp and the
    C6 at -2.5 sp from the treble middle line. The line's height is worked out from the ink
    the group really draws - noteheads, and the stems, flags and beams that reach past them
    on the side the bracket is going - so it always clears all of it: fully above for
    8va/15ma, fully below for 8vb/15mb. `notes_info` carries the stem directions the layout
    settled on (call this after the beams are placed); called on its own it falls back to
    the engraver's directions. `local_rect` is the staff panel, and the line is kept inside
    it - for the few pitches whose notes sit right at the panel's edge the clamp wins, since
    a bracket off the panel is worse than one crossing a notehead.
    """
    if not sys_notes:
        return

    # Find contiguous groups of notes that have octave_shift != 0
    sorted_notes = sorted([n for n in sys_notes if notes_info[id(n)]["octave_shift"] != 0], key=lambda n: n.start)
    if not sorted_notes:
        return

    nw = round(s * 0.75)
    line_w = max(1, int(s * 0.15))
    font = ui_font(max(9, int(s * 1.35)), bold=True)
    color = (40, 40, 50)

    # Group notes with same (clef, octave_shift, label, octave_group_id)
    groups = []
    curr_group = []
    for n in sorted_notes:
        ni = notes_info[id(n)]
        grp_id = ni.get("octave_group_id")
        key = (ni["clef"], ni["octave_shift"], ni["label"], grp_id)
        if not curr_group:
            curr_group.append((n, key))
        else:
            prev_ni = notes_info[id(curr_group[-1][0])]
            prev_grp_id = prev_ni.get("octave_group_id")
            prev_key = (prev_ni["clef"], prev_ni["octave_shift"], prev_ni["label"], prev_grp_id)
            same_group = (key == prev_key and (n.start - curr_group[-1][0].start) < 4.0)
            if same_group and grp_id is None:
                # Disallow bridging across unshifted notes of the same clef
                t_prev = curr_group[-1][0].start
                t_curr = n.start
                clef = ni["clef"]
                has_gap_note = any(
                    notes_info[id(other)]["clef"] == clef and
                    notes_info[id(other)]["octave_shift"] == 0 and
                    t_prev < other.start < t_curr
                    for other in sys_notes
                )
                if has_gap_note:
                    same_group = False
            if same_group:
                curr_group.append((n, key))
            else:
                groups.append(curr_group)
                curr_group = [(n, key)]
    if curr_group:
        groups.append(curr_group)

    head_h = notation.NOTEHEAD_H_SP * s / 2.0        # half a notehead's ink height
    head_w = notation.NOTEHEAD_W_SP * s / 2.0
    gap = OCTAVE_GAP_SPACES * s
    stem_len = STEM_LEN_SPACES * s
    beam_half = max(2, round(BEAM_WIDTH_SPACES * s)) / 2.0

    def ink_span(n: Note, clef: str) -> Tuple[float, float]:
        """
        The top and bottom of the ink one note of the group draws.

        A stem reaches well past its notehead, and a beamed one ends on a bar that reaches
        past the stem, so clearing only the noteheads would leave the line sitting on the
        stems when they happen to point at it - which is exactly the case the bracket is
        over. The direction is the one the layout settled on, so a stem turned over to keep
        off the other hand counts where it really is.
        """
        info = notes_info[id(n)]
        y = note_y(info["step_draw"], clef, treble_mid_y, bass_mid_y, s)
        top, bot = y - head_h, y + head_h
        if info.get("value") == WHOLE:                  # a whole note has no stem
            return top, bot
        up = info.get("beam_stem_up", info.get("chord_stem_up"))
        if up is None:                                  # no layout behind us: the engraver's
            up = stem_up_for([info["step_draw"]],
                             TREBLE_MID_STEP if clef == CLEF_TREBLE else BASS_MID_STEP)
        tip = info.get("beam_tip_y")
        if tip is not None:
            return min(top, tip - beam_half), max(bot, tip + beam_half)
        if up:
            return min(top, info.get("y_top", y) - stem_len), bot
        return top, max(bot, info.get("y_bot", y) + stem_len)

    # Every notehead on this system, per staff: the bracket's own height comes from the notes
    # of its group, but its label sits to the left of them and could land on a notehead from
    # another group, so those are what the label is kept off as well.
    ink_bands = {CLEF_TREBLE: [], CLEF_BASS: []}
    for n in sys_notes:
        info = notes_info[id(n)]
        ink_bands[info["clef"]].append((id(n), info["x"] - head_w, info["x"] + head_w,
                                         note_y(info["step_draw"], info["clef"],
                                                treble_mid_y, bass_mid_y, s) - head_h,
                                         note_y(info["step_draw"], info["clef"],
                                                treble_mid_y, bass_mid_y, s) + head_h))

    for grp in groups:
        clef, shift, label, *_ = grp[0][1]
        grp_notes = [item[0] for item in grp]
        grp_ids = {id(n) for n in grp_notes}
        xs = [notes_info[id(n)]["x"] for n in grp_notes]
        x_min = min(xs) - nw - 4
        x_max = max(xs) + nw + 12

        lbl_surf = font.render(label, True, color)
        lbl_w = lbl_surf.get_width()
        lbl_h = lbl_surf.get_height()

        spans = [ink_span(n, clef) for n in grp_notes]
        top_ink = min(sp[0] for sp in spans)
        bot_ink = max(sp[1] for sp in spans)

        # Notes of *other* groups that the label, printed to the left of this group's first
        # note, would print on. (This group's own notes are already accounted for below.)
        touching = [band for band in ink_bands[clef]
                    if band[0] not in grp_ids
                    and band[2] >= x_min - gap and band[1] <= x_min + lbl_w + gap]
        if clef == CLEF_TREBLE:
            # 8va / 15ma: fully above everything the group draws on that side
            bracket_y = top_ink - gap
            # ...and, if the panel has room for it, clear of the noteheads the label would
            # print on. When it does not fit, the height that clears this group's own ink
            # stands: that is the line's job, and pushing further would only land the whole
            # bracket somewhere worse.
            if touching:
                want = min(b[3] for b in touching) - lbl_h * 0.5 - gap
                if local_rect is None or want >= local_rect.top + gap:
                    bracket_y = min(bracket_y, want)
            if local_rect is not None:
                bracket_y = max(bracket_y, local_rect.top + gap)
            edge = top_ink                                # the group's own nearest ink
        else:
            # 8vb / 15mb: fully below everything the group draws on that side
            bracket_y = bot_ink + gap
            if touching:
                want = max(b[4] for b in touching) + lbl_h * 0.5 + gap
                if local_rect is None or want <= local_rect.bottom - gap:
                    bracket_y = max(bracket_y, want)
            if local_rect is not None:
                bracket_y = min(bracket_y, local_rect.bottom - gap)
            edge = bot_ink

        surf.blit(lbl_surf, (round(x_min), round(bracket_y - lbl_h * 0.5)))
        line_x_start = x_min + lbl_w + 3
        if x_max > line_x_start:
            draw_dashed_line(surf, color, (line_x_start, bracket_y), (x_max, bracket_y), width=line_w)
            # The hook points back at the notes: down for 8va, up for 8vb, a short turn that
            # stops just short of the group's nearest ink (never past it, never floating off
            # at the line's other side).
            span = max(s * 0.7, gap * 0.8)
            if clef == CLEF_TREBLE:
                hook_y = min(bracket_y + span, edge - max(1.0, s * 0.1))
            else:
                hook_y = max(bracket_y - span, edge + max(1.0, s * 0.1))
            pygame.draw.line(surf, color, (round(x_max), round(bracket_y)), (round(x_max), round(hook_y)), line_w)

# How far a tuplet's bracket stands off the ink its group draws, in staff spaces:
# clear of the stems' tips and the beams on the side the bracket goes, so the
# number never sits on the notes it counts.
TUPLET_GAP_SPACES = 0.8


def find_tuplet_groups(sys_notes: List[Note], notes_info: dict) -> List[Tuple[List[Note], str]]:
    """
    The tuplet groups of a system: (the notes of one group, the number to print).

    A MusicXML score writes a tuplet on every note of the group -
    <time-modification>'s <actual-notes> is the number (3 for a triplet) - and
    marks the notes that open and close it with <notations><tuplet type="start">
    / type="stop">. So a group is one hand's on one staff's run of notes with the
    same tuplet number, opened by its start mark and closed by its stop; a group
    the score never stopped (or never marked at all) ends where the next note's
    number or place changes. Two groups of the same ratio side by side stay
    apart, each opening with its own start. A grace note is left out: it hangs
    off the group's note, it is not one of the notes being counted.
    """
    open_groups: dict = {}            # (hand, clef, tuplet_num) -> the notes so far
    order: List[tuple] = []
    done: List[Tuple[List[Note], str]] = []

    def close(key) -> None:
        notes = open_groups.pop(key, None)
        if notes:
            done.append((notes, str(key[2])))
        if key in order:
            order.remove(key)

    for n in sorted((n for n in sys_notes
                     if getattr(n, "tuplet_num", None)
                     and not notes_info[id(n)].get("grace")),
                    key=lambda n: (n.start, n.pitch)):
        info = notes_info[id(n)]
        key = (n.hand, info["clef"], n.tuplet_num)
        kind = getattr(n, "tuplet_type", None)
        if kind == "start":
            if key in open_groups and abs(n.start - open_groups[key][-1].start) < 1e-4:
                open_groups[key].append(n)
            else:
                close(key)                # a group the score never stopped: this one takes over
                open_groups[key] = [n]
                order.append(key)
            continue
        if key in open_groups:
            open_groups[key].append(n)
        else:
            # No open group for this hand/staff/ratio: the score wrote no start mark
            # for it (or its start sits in an earlier system), so the run of notes
            # with the same number is the group.
            open_groups[key] = [n]
            order.append(key)
        if kind == "stop":
            close(key)
    for key in list(order):
        close(key)
    return done


def draw_tuplet_brackets(surf: pygame.Surface, notes_info: dict, sys_notes: List[Note],
                         treble_mid_y: float, bass_mid_y: float, s: float,
                         local_rect: Optional[pygame.Rect] = None):
    """
    The tuplets' brackets - the line with the number in it that says three written
    notes take the time of two (a triplet, "liên ba").

    Each group's bracket stands on the side its stems point to, its line kept clear
    of the ink the group really draws - the noteheads, and the stems or beams that
    reach past them - by TUPLET_GAP_SPACES, with the legs dropping towards the
    notes. Call this after the beams are placed: `notes_info` then carries the stem
    directions the layout settled on.
    """
    head_half = notation.NOTEHEAD_W_SP * s / 2.0
    stem_len = STEM_LEN_SPACES * s
    for grp, num_str in find_tuplet_groups(sys_notes, notes_info):
        info = notes_info[id(grp[0])]
        clef = info["clef"]

        up = info.get("beam_stem_up", info.get("chord_stem_up"))
        if up is None:                                  # no layout behind us: the engraver's
            up = stem_up_for([notes_info[id(n)]["step_draw"] for n in grp],
                             TREBLE_MID_STEP if clef == CLEF_TREBLE else BASS_MID_STEP)

        # The bracket spans the group's outer noteheads, a little past them.
        xs = [notes_info[id(n)]["x"] for n in grp]
        x0, x1 = min(xs) - head_half, max(xs) + head_half

        # The group's nearest ink on the side the bracket goes: the noteheads' far
        # edge, or the stem tips / beam that reach past them.
        gap = TUPLET_GAP_SPACES * s
        edge = None
        for n in grp:
            i = notes_info[id(n)]
            y = note_y(i["step_draw"], clef, treble_mid_y, bass_mid_y, s)
            y_top, y_bot = i.get("y_top", y), i.get("y_bot", y)
            tip = i.get("beam_tip_y")
            if tip is not None:
                near = min(y_top, tip) if up else max(y_bot, tip)
            else:
                near = y_top - stem_len if up else y_bot + stem_len
            edge = near if edge is None else (min(edge, near) if up else max(edge, near))

        y = edge - gap if up else edge + gap
        if local_rect is not None:                      # keep the bracket on the paper
            y = max(local_rect.top + s * 0.4, min(local_rect.bottom - s * 0.4, y))
        notation.draw_tuplet_bracket(surf, s, x0, x1, y, num_str,
                                     HAND_COLORS.get(grp[0].hand, HAND_COLORS[RIGHT])[0],
                                     stem_up=up)

def draw_pedal_brackets(surf: pygame.Surface, spans: List[Tuple[float, float]],
                        sys_start: float, sys_end: float, x_of, y: float, s: float,
                        color: Tuple[int, int, int] = (60, 60, 75)) -> None:
    """
    Draw measure pedal brackets below the bass staff for auto pedal mode.

    Each span (a, b) in seconds is clipped to the system [sys_start, sys_end].
    Draws a line with pedal change notches (∧) at change points and 'Ped.' label.
    """
    if not spans or sys_end <= sys_start:
        return

    tick_h = max(3.0, round(0.6 * s))
    line_w = max(1, round(0.12 * s))

    for idx, (sa, sb) in enumerate(spans):
        ca = max(sa, sys_start)
        cb = min(sb, sys_end)
        if cb <= ca + 1e-6:
            continue

        x0 = x_of(ca)
        x1 = x_of(cb)
        if x1 <= x0 + 1.0:
            continue

        py = round(y)
        # Horizontal bracket line
        pygame.draw.line(surf, color, (round(x0), py), (round(x1), py), line_w)

        # Is this span a new press (first span or preceded by a silent gap)?
        is_new_press = (idx == 0 or (sa - spans[idx - 1][1]) > 0.05)
        # Did the span start within this system?
        started_in_system = (sa >= sys_start - 1e-4)

        # Start tick: only drawn when the span actually starts in this system (not continuing across system break)
        if started_in_system:
            pygame.draw.line(surf, color, (round(x0), py), (round(x0), round(py - tick_h)), line_w)
            if is_new_press:
                # Ped. text
                ped_font = ui_font(max(9, int(round(s * 1.15))), bold=True)
                ped_surf = ped_font.render("Ped.", True, color)
                surf.blit(ped_surf, (round(x0 + 2), round(py - ped_surf.get_height() - 1)))

        # End mark: if the span ends within this system
        ended_in_system = (sb <= sys_end + 1e-4)
        if ended_in_system:
            # If followed immediately by next span in the same system, draw notch ∧
            is_change_point = (idx + 1 < len(spans) and (spans[idx + 1][0] - sb) < 0.1 and spans[idx + 1][0] < sys_end - 1e-4)
            if is_change_point:
                nx = round(x1)
                pygame.draw.line(surf, color, (nx - 3, py), (nx, round(py - tick_h)), line_w)
                pygame.draw.line(surf, color, (nx, round(py - tick_h)), (nx + 3, py), line_w)
            else:
                # End tick ┘
                pygame.draw.line(surf, color, (round(x1), py), (round(x1), round(py - tick_h)), line_w)


class StaffView:
    """
    Renders a Grand Staff sheet music system with dynamic cursor and paging.
    Supports clean paper white background, custom opacity, and accurate note values.

    Zoom: `target_dur` is how many seconds of the song share one system (smaller = the
    notes spread out and zoom in), `scale` how big the staff itself is drawn (smaller
    leaves more room above and below for the notes that reach past the staves).
    """
    def __init__(self, song=None, target_dur: float = 10.0, scale: float = 1.0):
        self.song = None
        self.systems: List[StaffSystem] = []
        self._system_starts: List[float] = []
        self.target_dur = clamp_target_dur(target_dur)
        self.scale = clamp_scale(scale)
        self._cached_static_surf: Optional[pygame.Surface] = None
        self._cached_heads: List[tuple] = []
        self._cached_rolled: List[tuple] = []
        self._cached_geo = None
        self._cached_key: Optional[tuple] = None
        self._scratch: Optional[pygame.Surface] = None   # (the paper plus the overlays, one size)
        if song is not None:
            self.set_song(song, target_dur=self.target_dur, scale=self.scale)

    def set_song(self, song, target_dur: float = 10.0, scale: float = 1.0):
        self.target_dur = clamp_target_dur(target_dur)
        self.scale = clamp_scale(scale)
        self.song = song
        self.systems = build_systems(song, self.target_dur)
        self._system_starts = [sys.start_t for sys in self.systems]
        self.clear_cache()

    def clear_cache(self):
        """Forget the system's cached paper: the next draw engraves it again.

        `draw` keeps the static surface of the system on the page (see
        `_render_static_system_surf`), so anything that changes what the paper looks like
        without touching the system, the panel's size or the zoom calls this.
        """
        self._cached_static_surf = None
        self._cached_heads = []
        self._cached_rolled = []
        self._cached_geo = None
        self._cached_key = None

    def system_for_time(self, t: float) -> Optional[StaffSystem]:
        """Return the active system for playback time t."""
        if not self.systems:
            return None
        if t <= 0.0:
            return self.systems[0]
        idx = bisect.bisect_right(self._system_starts, t) - 1
        idx = max(0, min(len(self.systems) - 1, idx))
        return self.systems[idx]

    def cursor_x(self, t: float, staff_rect: pygame.Rect,
                 margin_left: Optional[float] = None,
                 usable_w: Optional[float] = None) -> float:
        """Compute the X coordinate of the cursor within staff_rect at song time t."""
        sys = self.system_for_time(t)
        if margin_left is None or usable_w is None:
            geo = self.staff_geometry(sys, staff_rect)
            margin_left = geo.margin_left if margin_left is None else margin_left
            usable_w = geo.usable_w if usable_w is None else usable_w
        if sys is None or usable_w <= 10:
            return float(staff_rect.x + margin_left)

        if t < sys.start_t:
            frac = 0.0
        elif t >= sys.end_t:
            frac = 1.0
        else:
            frac = (t - sys.start_t) / sys.duration
        return staff_rect.x + margin_left + frac * usable_w

    def _staff_size(self, rect: pygame.Rect) -> float:
        """How big the staff itself is drawn at zoom 1, in pixels per space.

        The panel's height sets it: a grand staff stands about 10 spaces tall (the two
        staves plus their baseline gap), so a taller panel draws a bigger score. The
        user's zoom multiplies this later, in `staff_geometry`.
        """
        return max(4.0, min(12.0, (rect.h - 26) / 14.0))

    def _pedal_spans(self, sys) -> list:
        """The auto-pedal spans this system shows (empty when it would be too crowded)."""
        spans = getattr(self.song, "pedal_spans", None) if self.song is not None else None
        if not spans or sys is None:
            return []
        sys_spans = [sp for sp in spans if sp[1] > sys.start_t and sp[0] < sys.end_t]
        return sys_spans if len(sys_spans) <= 12 else []

    def staff_geometry(self, sys, rect: pygame.Rect) -> StaffGeometry:
        """
        How this system is drawn in `rect`: its size, the two staff middle lines, the
        room its head leaves before the notes, and where its pedal bracket goes.

        The size starts from the panel's own size and the user's zoom. The treble and
        bass staves stand BASE_MID_GAP_SP apart, and further apart when the two hands'
        ink would crowd or collide in the gap - the way MuseScore spaces a grand staff.
        If that spread no longer fits the panel, the whole system is drawn smaller
        (never below MIN_FIT_SCALE of the user's size) and its content block is centred
        between the measure-number band at the top and STAFF_PAD_Y at the bottom. The
        space a pedal bracket needs below the lowest ink is reserved as well, so the
        bracket never sits on a note.

        Zooming in past what the panel holds is allowed: like any zoom it then runs off
        the paper rather than being silently ignored.
        """
        base_s = self._staff_size(rect)
        zoom = clamp_scale(self.scale)
        if sys is None:
            s = max(4.0, base_s * zoom)
            return StaffGeometry(
                s,
                rect.centery - round(3.0 * s),
                rect.centery + round(3.0 * s),
                *self._header(rect, None, s),
                None,
            )

        bar_times = getattr(self.song, "bar_times", [])
        beat_times = getattr(self.song, "beat_times", None)
        sys_pieces, _ties = split_at_bar_lines(sys.notes, bar_times, beat_times)
        # only the pieces that open in this system are drawn on it (see draw)
        kept = [p for p in sys_pieces if sys.start_t - 1e-9 <= p.start < sys.end_t - 1e-9]
        beat_len = get_beat_duration(self.song)
        clusters = group_chords(sorted(kept, key=lambda n: (n.start, n.pitch)), beat_len)
        steps = drawn_steps(clusters, kept)

        top_sp, inner_t_sp, inner_b_sp, bottom_sp = vertical_extents(steps, base_s)
        gap_sp = max(BASE_MID_GAP_SP, inner_t_sp + inner_b_sp + INNER_CLEARANCE_SP)
        ink_sp = gap_sp + top_sp + bottom_sp
        pedal_sp = (PEDAL_GAP_SP + PEDAL_BAND_SP) if self._pedal_spans(sys) else 0.0
        total_sp = ink_sp + pedal_sp

        top_pad = STAFF_PAD_Y + MEASURE_NUM_PAD_PX
        avail = max(1.0, rect.h - top_pad - STAFF_PAD_Y)
        fit = max(MIN_FIT_SCALE, min(1.0, avail / (total_sp * base_s)))
        s = max(4.0, base_s * zoom * fit)
        block_top = rect.y + top_pad + (avail - total_sp * s) / 2.0
        treble_mid_y = block_top + top_sp * s
        bass_mid_y = treble_mid_y + gap_sp * s
        pedal_y = block_top + (ink_sp + PEDAL_GAP_SP) * s if pedal_sp else None
        margin_left, usable_w = self._header(rect, sys, s)
        return StaffGeometry(s, treble_mid_y, bass_mid_y, margin_left, usable_w, pedal_y)

    def _key_fifths(self, sys) -> int:
        """
        The key signature in force at the head of this system: how many sharps
        (positive) or flats (negative) it holds.

        A key change inside a system is drawn at the head of the next one, so the
        signature and the accidentals of a system always belong to the same key.
        """
        if sys is None or self.song is None:
            return 0
        key_at = getattr(self.song, "key_at", None)
        return int(key_at(sys.start_t)) if key_at else 0

    def _header(self, rect: pygame.Rect, sys,
                s: Optional[float] = None) -> Tuple[float, float]:
        """
        (margin_left, usable_w): where the system's notes start and how much room
        they have before the right margin.

        The head of the system holds the clef, the key signature and the time
        signature, and a key signature makes it wider - so the notes start further
        right, and the music between them is a little narrower. `s` is the size the
        system is drawn at; when omitted, the panel's unzoomed size is used.
        """
        if s is None:
            s = self._staff_size(rect)
        margin_left = header_margin(STAFF_LEFT_X, s, self._key_fifths(sys))
        return margin_left, rect.w - margin_left - MARGIN_RIGHT

    def _render_static_system_surf(self, sys, local_rect: pygame.Rect):
        """
        Draw the system's paper once: everything that does not move while the cursor crosses it.

        The staff lines, the clefs, the key and time signatures, the bar lines and measure
        numbers, every notehead with its stem, flags, beams, ties, brackets and the pedal line
        are the same on every frame of a system, so they are drawn here and cached. `draw` then
        blits this surface and adds only what moves: the sounding notes' halo and the cursor.

        Returns `(surface, heads, rolled, geo)`: the finished paper, one `(note, x, y,
        half_w, half_h, pad, light)` per notehead (where the halo goes, and a callable
        that redraws the note in the sounding colour), the rolled chords as
        `(notes, x, y_top, y_bot)` so a chord being swept can be marked in the cursor's
        red, and the geometry the paper was drawn with (so `draw` need not work it out
        again).
        """
        staff_surf = pygame.Surface(local_rect.size)
        staff_surf.fill(STAFF_BG)
        heads: List[tuple] = []
        rolled_chords: List[tuple] = []

        geo = self.staff_geometry(sys, local_rect)
        s = geo.s
        treble_mid_y = geo.treble_mid_y
        bass_mid_y = geo.bass_mid_y

        line_color = STAFF_LINE
        left_x = local_rect.x + STAFF_LEFT_X
        right_x = local_rect.right - 12

        # The system being drawn: its key signature stands at the head of the staff
        # and decides how much room the head takes before the first note.
        key_fifths = self._key_fifths(sys)
        margin_left, usable_w = geo.margin_left, geo.usable_w

        # 3. Staff lines (5 for Treble, 5 for Bass) - MuseScore's thickness (staffLineThickness)
        line_w = max(1, round(notation.STAFF_LINE_SP * s))
        for i in range(-2, 3):
            ty = round(treble_mid_y + i * s)
            pygame.draw.line(staff_surf, line_color, (left_x, ty), (right_x, ty), line_w)
            by = round(bass_mid_y + i * s)
            pygame.draw.line(staff_surf, line_color, (left_x, by), (right_x, by), line_w)

        # Left connecting bar and bracket
        treble_top = round(treble_mid_y - 2 * s)
        bass_bot = round(bass_mid_y + 2 * s)
        pygame.draw.line(staff_surf, line_color, (left_x, treble_top), (left_x, bass_bot), 1)
        # Bracket on the far left
        br_x = local_rect.x + 12
        pygame.draw.line(staff_surf, line_color, (br_x, treble_top), (br_x, bass_bot), 3)
        pygame.draw.line(staff_surf, line_color, (br_x - 3, treble_top - 2), (br_x + 3, treble_top), 2)
        pygame.draw.line(staff_surf, line_color, (br_x - 3, bass_bot + 2), (br_x + 3, bass_bot), 2)

        # 4. Clefs - Leland's SMuFL glyphs, each on the staff line it names
        draw_treble_clef(staff_surf, left_x + 8, treble_mid_y, s, CLEF_COLOR)
        draw_bass_clef(staff_surf, left_x + 8, bass_mid_y, s, CLEF_COLOR)

        # 4b. Key signature - the sharps or flats of the key, each on the staff step
        # the tradition gives it, between the clef and the time signature.
        key_end = 0.0
        if key_fifths:
            key_x = key_sig_x(left_x, s)
            drawn_key = [
                draw_key_signature(staff_surf, key_x, mid_y, mid_step, s, key_fifths, clef)
                for mid_y, mid_step, clef in ((treble_mid_y, TREBLE_MID_STEP, CLEF_TREBLE),
                                              (bass_mid_y, BASS_MID_STEP, CLEF_BASS))]
            if all(drawn_key):
                key_end = key_x + key_sig_width_sp(key_fifths, s) * s

        # 5. Time signature - the numerator on the line above the middle one and the
        # denominator on the line below (notation.draw_time_signature places them). The
        # old UI-font digits are the fallback for a build without the music font.
        ts_x = time_sig_x(left_x, s, key_end)
        ts_num, ts_den = getattr(self.song, "time_signature", (4, 4)) if self.song else (4, 4)
        drawn = [notation.draw_time_signature(staff_surf, ts_x, ts_mid, s, ts_num, ts_den, TIME_SIG_COLOR)
                 for ts_mid in (treble_mid_y, bass_mid_y)]
        if not all(drawn):
            ts_font = ui_font(max(10, int(s * 1.6)), bold=True)
            ts_num_img = ts_font.render(str(ts_num), True, TIME_SIG_COLOR)
            ts_den_img = ts_font.render(str(ts_den), True, TIME_SIG_COLOR)
            for ts_mid in (treble_mid_y, bass_mid_y):
                staff_surf.blit(ts_num_img, ts_num_img.get_rect(center=(ts_x, round(ts_mid - s * 0.9))))
                staff_surf.blit(ts_den_img, ts_den_img.get_rect(center=(ts_x, round(ts_mid + s * 0.9))))

        if self.song and self.systems and sys is not None and usable_w > 10:
            # Measure bar lines
            num_font = ui_font(10)
            for m in sys.measures:
                if m.index > sys.measures[0].index:
                    frac_bar = (m.start_t - sys.start_t) / sys.duration
                    bx = round(local_rect.x + margin_left + frac_bar * usable_w)
                    pygame.draw.line(staff_surf, BAR_LINE_COLOR, (bx, treble_top), (bx, bass_bot), 1)
                    # Measure number at the top of the bar line
                    num_surf = num_font.render(str(m.index + 1), True, MEASURE_NUM_COLOR)
                    staff_surf.blit(num_surf, (bx + 3, treble_top - num_surf.get_height() - 1))

            # System ending bar line
            sys_end_x = round(local_rect.x + margin_left + usable_w)
            is_song_end = (sys.index == len(self.systems) - 1)
            if is_song_end:
                pygame.draw.line(staff_surf, BAR_LINE_COLOR, (sys_end_x - 4, treble_top), (sys_end_x - 4, bass_bot), 1)
                pygame.draw.line(staff_surf, BAR_LINE_COLOR, (sys_end_x, treble_top), (sys_end_x, bass_bot), 3)
            else:
                pygame.draw.line(staff_surf, BAR_LINE_COLOR, (sys_end_x, treble_top), (sys_end_x, bass_bot), 1)

            # 7. Draw Notes
            nw = round(s * 0.75)
            nh = round(s * 0.52)
            stem_len = round(STEM_LEN_SPACES * s)

            # A note that runs past a bar line is cut at the bar and the pieces tied,
            # so no measure shows more ink than it holds. The split happens once, here:
            # everything below - layout, beams, brackets, the draw loop - works on the
            # pieces, never on the raw notes. `sys_ties` pairs the pieces to join.
            sys_pieces, sys_ties = split_at_bar_lines(
                sys.notes,
                getattr(self.song, "bar_times", []),
                getattr(self.song, "beat_times", None),
            )
            if getattr(self.song, "xml_ties", None):
                # The score's own ties. Each side is resolved on its own: when a
                # tie runs over a system break only one half is on this system,
                # and the draw loop then carries the tie out to the edge (or in
                # from it) instead of dropping the pair.
                def tie_piece(note, last):
                    found = [p for p in sys_pieces
                             if p.pitch == note.pitch and p.track == note.track
                             and p.voice == note.voice
                             and p.start >= note.start - 1e-3 and p.end <= note.end + 1e-3
                             and sys.start_t - 1e-9 <= p.start < sys.end_t - 1e-9]
                    if not found:
                        return None
                    return max(found, key=lambda p: p.start) if last \
                        else min(found, key=lambda p: p.start)
                for l, r in self.song.xml_ties:
                    l_piece = tie_piece(l, last=True)
                    r_piece = tie_piece(r, last=False)
                    if l_piece is None and r_piece is None:
                        continue
                    pair = (l_piece or l, r_piece or r)
                    if pair not in sys_ties:
                        sys_ties.append(pair)
            # Keep only the pieces that belong to this system (starting on or after
            # sys.start_t and before sys.end_t). A note crossing system boundaries is
            # split at the bar line; the head piece is rendered on the earlier system
            # and the tail piece opens the next system at its first bar line.
            kept = [p for p in sys_pieces if sys.start_t - 1e-9 <= p.start < sys.end_t - 1e-9]
            kept_ids = {id(p) for p in kept}
            sys_ties = [(l, r) for l, r in sys_ties
                        if id(l) in kept_ids or id(r) in kept_ids]
            sys_pieces = kept

            # Which notes print an accidental: the key signature already covers
            # the notes it alters, and a sign holds for the rest of its measure.
            accidentals = plan_accidentals(
                sys_pieces,
                [m.start_t for m in sys.measures] + [sys.start_t + sys.duration],
                key_fifths)

            notes_info = compute_notes_info(sys_pieces, sys.start_t, sys.duration, usable_w,
                                            margin_left, s, get_beat_duration(self.song),
                                            [m.start_t for m in sys.measures[1:]]
                                            + [sys.start_t + sys.duration],
                                            accidentals)

            # Group the pieces into chords, the same way compute_notes_info did
            sorted_notes = sorted(sys_pieces, key=lambda n: (n.start, n.pitch))
            beat_len = get_beat_duration(self.song)
            time_clusters = group_chords(sorted_notes, beat_len)

            # Where every notehead sits. A stem or a beam is not allowed to run over
            # one of these that belongs to another chord - not even one on the other
            # staff, since a bar reaching out of one staff can cross the gap and
            # land on the other. MuseScore runs the same check (beamlayout.cpp,
            # checkBeamNoteOverlap) and moves the beam clear of the note; here it is
            # read before the chords are drawn, so a stem that would be printed on
            # another hand's note can be turned the other way as well.
            all_heads = []
            for n in sys_pieces:
                i = notes_info[id(n)]
                ny_ = note_y(i["step_draw"], i["clef"], treble_mid_y, bass_mid_y, s)
                hw = round(nw * 1.15) if printed_value(n, n.duration,
                                                      get_beat_duration(self.song, n.start)) == WHOLE else nw
                all_heads.append((i["x"] - hw, i["x"] + hw, ny_ - nh, ny_ + nh, id(n)))
            heads_boxes = NoteHeads(all_heads)

            # Beaming: short notes of one hand on one staff are joined, but only the
            # ones that follow straight on from each other, and only a few at a
            # time. Notes that are a real step apart in the rhythm (a rest, a
            # longer note, a bar line) end the group, so no beam ever reaches
            # across notes that have nothing to do with each other. Grouped here,
            # before the chords: a beamed chord's stems are settled with its group,
            # so its own stem is not looked at twice.
            beamed_note_ids = set()
            measure_of = {}
            for m in sys.measures:
                for n in sys_pieces:
                    if m.start_t <= n.start < m.end_t:
                        measure_of[id(n)] = m

            beam_groups = group_beams(time_clusters, notes_info,
                                      lambda st_: get_beat_duration(self.song, st_), measure_of)
            for bg in (g[1] for g in beam_groups):
                beamed_note_ids.update(id(n) for sub in bg for n in sub)

            # Compute chord-level stem direction and y-bounds for each cluster
            for tc_idx, tc in enumerate(time_clusters):
                by_hc = {}
                for n in tc:
                    c = notes_info[id(n)]["clef"]
                    by_hc.setdefault((n.hand, c), []).append(n)

                for (h, c), c_notes in by_hc.items():
                    if not c_notes:
                        continue
                    mid_step = TREBLE_MID_STEP if c == CLEF_TREBLE else BASS_MID_STEP
                    chord_stem_up = stem_up_for(
                        [notes_info[id(n)]["step_draw"] for n in c_notes], mid_step)

                    ny_list = [note_y(notes_info[id(n)]["step_draw"], c, treble_mid_y, bass_mid_y, s) for n in c_notes]
                    y_top = min(ny_list)
                    y_bot = max(ny_list)

                    # A stem 3.5 spaces long reaches well past its own notehead, and
                    # where the notes around it are too close - the two hands'
                    # notes drawn together, with the clash offsets out of room - it
                    # runs across the other hand's. Turn it over when that happens
                    # and the other side is clear, and only then, so a chord with
                    # nothing in the way keeps the direction the engraver chose.
                    # A beamed chord is settled with its group instead (below).
                    if (printed_value(c_notes[0], c_notes[0].duration, beat_len) != WHOLE
                            and not any(id(n) in beamed_note_ids for n in c_notes)):
                        own_c = {id(n) for n in c_notes}
                        cx = notes_info[id(c_notes[0])]["x"]
                        flags = max(printed_shape(n, n.duration, beat_len)[1]
                                    for n in c_notes)
                        near = heads_boxes.near(cx - nw - s, cx + nw + 2 * s)
                        up_hit = place_stem(chord_stem_up, cx, y_top, y_bot,
                                            stem_len, flags, s, near, own_c)
                        down_hit = place_stem(not chord_stem_up, cx, y_top, y_bot,
                                              stem_len, flags, s, near, own_c)
                        if down_hit < up_hit:
                            chord_stem_up = not chord_stem_up

                    for n in c_notes:
                        info_n = notes_info[id(n)]
                        info_n["chord_stem_up"] = chord_stem_up
                        info_n["y_top"] = y_top
                        info_n["y_bot"] = y_bot
                        info_n["chord_size"] = len(c_notes)
                        # the notes struck together on one staff share a single stem,
                        # except a grace note - it keeps its own stem and its own
                        # height, even in the rare cluster it shares with the main
                        # note it leans on (group_chords split them by time)
                        if info_n.get("grace"):
                            info_n["stem_key"] = (tc_idx, h, c, id(n))
                            my = note_y(info_n["step_draw"], c, treble_mid_y, bass_mid_y, s)
                            info_n["y_top"] = info_n["y_bot"] = my
                        else:
                            info_n["stem_key"] = (tc_idx, h, c)

                    # A rolled chord ("arpeggio") is written with its notes
                    # slightly staggered - they are plucked or swept one after
                    # another, not all together. That stagger is what marks it,
                    # so a plain block chord of three or more notes is left alone.
                    rolled = len(c_notes) >= 3 and (c_notes[-1].start - c_notes[0].start) >= 0.04 * beat_len
                    if rolled:
                        chord_x = min(notes_info[id(n)]["x"] for n in c_notes)
                        draw_arpeggio_wavy_line(staff_surf, chord_x - nw - round(s * 0.6),
                                                y_top - s * 0.4, y_bot + s * 0.4, s, (60, 60, 75))
                        # kept so `draw` can turn it red while the chord sounds
                        rolled_chords.append((tuple(c_notes), chord_x - nw - round(s * 0.6),
                                              y_top - s * 0.4, y_bot + s * 0.4))

            # Draw the beams: a bar along the stems, plus a second, shorter bar
            # inwards for the 16th notes of the run. The groups were decided above.
            for c, bg in beam_groups:
                notes_here = [n for sub in bg for n in sub]

                mid_step = TREBLE_MID_STEP if c == CLEF_TREBLE else BASS_MID_STEP
                default_up = stem_up_for(
                    {notes_info[id(n)]["step_draw"] for n in notes_here}, mid_step)

                # Where each chord's noteheads are, and whether it needs a second bar.
                stops = []
                for sub in bg:
                    x = notes_info[id(sub[0])]["x"]
                    y_top = min(notes_info[id(n)]["y_top"] for n in sub)
                    y_bot = max(notes_info[id(n)]["y_bot"] for n in sub)
                    is16 = any(printed_value(n, n.duration, get_beat_duration(self.song, n.start)) == SIXTEENTH
                               for n in sub)
                    stops.append((x, y_top, y_bot, is16))

                # The bar is dead level, so it is the stems that give way. How far
                # it stands off the notes depends on how many bars are stacked: a
                # run of sixteenths carries a second bar under the first, and the
                # stem has to be long enough that the notehead never sits on them.
                bars = 2 if any(st[3] for st in stops) else 1
                reach = max(stem_len, round(min_stem_spaces(bars) * s))
                beam_w = max(2, round(BEAM_WIDTH_SPACES * s))
                gap = round(BEAM_STEP_SPACES * s)
                own = {id(n) for n in notes_here}

                # The engraver's direction first; turned over only if the bar it
                # puts there would run over another chord's notehead (the other
                # hand's, most of the time - see layout_beam).
                near = heads_boxes.near(min(st[0] for st in stops) - nw,
                                        max(st[0] for st in stops) + nw)
                beam_stem_up, tip = layout_beam(stops, default_up, reach, beam_w,
                                                gap, nw, near, own, s,
                                                (local_rect.top, local_rect.bottom))

                outer, inner = [], []
                run = []
                for sub, (x, _y_top, _y_bot, is16) in zip(bg, stops):
                    sx = round(x + nw - 1) if beam_stem_up else round(x - nw + 1)
                    outer.append((sx, tip))
                    for n in sub:
                        notes_info[id(n)]["beam_stem_up"] = beam_stem_up
                        notes_info[id(n)]["beam_tip_y"] = tip
                    # the second bar covers only the stretch of 16th notes
                    if is16:
                        run.append((sx, tip))
                    elif run:
                        inner.append(run)
                        run = []
                if run:
                    inner.append(run)

                pygame.draw.lines(staff_surf, STEM_COLOR, False, outer, beam_w)
                # A run of sixteenths carries a second bar: it goes between the first
                # bar and the notes, so the stems run through it (MuseScore stacks
                # the beams inward, and `reach` already leaves room for the stack).
                off = gap if beam_stem_up else -gap
                for run in inner:
                    shifted = [(x, y + off) for x, y in run]
                    if len(run) >= 2:
                        pygame.draw.lines(staff_surf, STEM_COLOR, False, shifted, beam_w)
                    else:      # a lone 16th gets a short stub of its own
                        x, y = shifted[0]
                        pygame.draw.line(staff_surf, STEM_COLOR, (x - round(s * 0.6), y), (x, y), beam_w)

            # Grace runs are beamed together too, at graceNoteMag, and the whole
            # run carries one stroke through its beam instead of a stroke on every
            # stem - MuseScore's look for two flicked notes (BeamLayout::
            # beamGraceNotes, beamlayout.cpp:553-620). The notes join the beamed
            # set so they draw no flags of their own, and the beam's tip is their
            # stem's end.
            for _run, g_clusters in group_grace_beams(
                    find_grace_runs(sorted_notes, beat_len), notes_info,
                    lambda st_: get_beat_duration(self.song, st_)):
                g_notes = [n for c in g_clusters for n in c]
                mid_step = TREBLE_MID_STEP if notes_info[id(g_notes[0])]["clef"] == CLEF_TREBLE \
                    else BASS_MID_STEP
                up = stem_up_for([notes_info[id(n)]["step_draw"] for n in g_notes], mid_step)

                g_stem_len = STEM_LEN_SPACES * s * notation.GRACE_MAG
                g_beam_w = max(2, round(BEAM_WIDTH_SPACES * s * notation.GRACE_MAG))
                tips = []
                for c in g_clusters:
                    y_top = min(notes_info[id(n)]["y_top"] for n in c)
                    y_bot = max(notes_info[id(n)]["y_bot"] for n in c)
                    tip = round(y_top - g_stem_len) if up else round(y_bot + g_stem_len)
                    tips.append((notes_info[id(c[0])]["x"], tip))
                # one level bar for the whole run - a grace run is a flick, it never
                # bothers with the stacked bars a slow run of sixteenths carries
                bar_y = round(sum(t for _x, t in tips) / len(tips))

                outer = []
                for c, (x, _tip) in zip(g_clusters, tips):
                    sx = round(x + nw * notation.GRACE_MAG - 1) if up \
                        else round(x - nw * notation.GRACE_MAG + 1)
                    outer.append((sx, bar_y))
                    for n in c:
                        notes_info[id(n)]["beam_stem_up"] = up
                        notes_info[id(n)]["beam_tip_y"] = bar_y
                        beamed_note_ids.add(id(n))
                pygame.draw.lines(staff_surf, STEM_COLOR, False, outer, g_beam_w)
                notation.beam_slash(staff_surf, s * notation.GRACE_MAG,
                                    outer[0][0], outer[-1][0], bar_y, up,
                                    HAND_COLORS[g_notes[0].hand][0])

            # The 8va / 8vb brackets come after the stems: their line is kept off the
            # ink the group really draws, stems and beams included, so it has to know
            # which way each stem ended up pointing.
            draw_octave_brackets(staff_surf, notes_info, sys_pieces, treble_mid_y, bass_mid_y,
                                 s, local_rect)

            # The tuplet brackets (3 over the line: a triplet) come after the stems
            # and beams for the same reason: the bracket stands on the side the
            # group's stems point to.
            draw_tuplet_brackets(staff_surf, notes_info, sys_pieces,
                                 treble_mid_y, bass_mid_y, s, local_rect)

            # Draw individual notes - through notation.py, which draws Leland's
            # SMuFL glyphs at MuseScore's metrics (see docs/ARCHITECTURE.md)
            rendered_chord_stems = set()
            for n in sorted_notes:
                n_info = notes_info[id(n)]
                nx = n_info["x"]
                clef = n_info["clef"]
                step = n_info["step_draw"]
                accidental = accidentals.get(id(n))
                ny = note_y(step, clef, treble_mid_y, bass_mid_y, s)

                hand_col = HAND_COLORS.get(n.hand, HAND_COLORS[RIGHT])[0]

                # A grace note is drawn at graceNoteMag - the head, its accidental,
                # the stem and the stroke across it all shrink together, while the
                # note stays on its line (staff.py's layout gave it its place).
                is_grace = n_info.get("grace", False)
                g_s = s * notation.GRACE_MAG if is_grace else s

                if n.is_rest:
                    notation.draw_rest(staff_surf, nx, ny, g_s, n_info.get("rest_type", "quarter"), hand_col)
                    continue

                nh_g = round(g_s * 0.52)

                b_len = get_beat_duration(self.song, n.start)
                shape = printed_shape(n, n.duration, b_len)
                if is_grace:
                    shape = (shape[0], 0, shape[2])   # the stroke replaces its flags
                head_half = notation.HEAD_BOX[shape[0]][2] * 0.5 * g_s

                # Ledger lines: MuseScore's thickness, reaching a ledgerLineLength
                # past the notehead on each side (ChordLayout::updateLedgerLines)
                ledger_col = line_color
                ext = notation.LEDGER_EXT_SP * g_s
                lx0, lx1 = nx - head_half - ext, nx + head_half + ext
                if clef == CLEF_TREBLE:
                    if step <= 28:  # Middle C and below
                        for st in range(28, step - 1, -2):
                            ly = round(treble_mid_y - (st - TREBLE_MID_STEP) * (s * 0.5))
                            notation.ledger_line(staff_surf, lx0, lx1, ly, s, ledger_col)
                    elif step >= 40:  # A5 and above
                        for st in range(40, step + 1, 2):
                            ly = round(treble_mid_y - (st - TREBLE_MID_STEP) * (s * 0.5))
                            notation.ledger_line(staff_surf, lx0, lx1, ly, s, ledger_col)
                else:  # Bass clef
                    if step >= 28:  # Middle C and above
                        for st in range(28, step + 1, 2):
                            ly = round(bass_mid_y - (st - BASS_MID_STEP) * (s * 0.5))
                            notation.ledger_line(staff_surf, lx0, lx1, ly, s, ledger_col)
                    elif step <= 16:  # E2 and below
                        for st in range(16, step - 1, -2):
                            ly = round(bass_mid_y - (st - BASS_MID_STEP) * (s * 0.5))
                            notation.ledger_line(staff_surf, lx0, lx1, ly, s, ledger_col)

                # Stem and flags
                stem_up = n_info.get("beam_stem_up", n_info.get("chord_stem_up", (step < TREBLE_MID_STEP if clef == CLEF_TREBLE else step < BASS_MID_STEP)))
                y_top = n_info.get("y_top", ny)
                y_bot = n_info.get("y_bot", ny)
                g_stem_len = round(STEM_LEN_SPACES * g_s) if is_grace else stem_len

                # One stem for the whole chord: notes struck together on one staff
                # share a single stem, all pointing the same way.
                stem_key = n_info.get("stem_key", (id(n), clef))
                stem = None
                if shape[0] != "whole" and stem_key not in rendered_chord_stems:
                    rendered_chord_stems.add(stem_key)
                    if stem_up:
                        tip_y = n_info.get("beam_tip_y", round(y_top - g_stem_len))
                        stem = (round(y_bot), tip_y)
                    else:
                        tip_y = n_info.get("beam_tip_y", round(y_bot + g_stem_len))
                        stem = (round(y_top), tip_y)

                notation.draw_staff_note(staff_surf, nx, ny, g_s, shape, hand_col, stem_up,
                                         False, stem=stem,
                                         beamed=id(n) in beamed_note_ids,
                                         accidental=accidental, on_line=(step % 2 == 0),
                                         grace=is_grace)

                # What `draw` adds while this note sounds: the ring (its box and pad) and
                # the same note in the sounding colour, drawn over the cached paper. The
                # old per-frame pass passed `sounding=True` here; this closure does the
                # same without working the note's shape, stem or accidental out again.
                light = (lambda surf_,
                                nx=nx, ny=ny, g_s=g_s, shape=shape, hand_col=hand_col,
                                stem_up=stem_up, stem=stem, beamed=id(n) in beamed_note_ids,
                                accidental=accidental, on_line=(step % 2 == 0), is_grace=is_grace:
                         notation.draw_staff_note(surf_, nx, ny, g_s, shape, hand_col, stem_up,
                                                  True, stem=stem, beamed=beamed,
                                                  accidental=accidental, on_line=on_line,
                                                  grace=is_grace))
                heads.append((n, nx, ny, head_half, nh_g, max(2, round(g_s * 0.25)), light))

            # Ties: after the noteheads, so the crescent reads over the ink, the way
            # MuseScore draws it (TDraw::draw(const TieSegment*), tdraw.cpp:3199).
            # The tie joins the two heads' facing edges and arches away from the
            # stems - MuseScore ties a single note opposite its chord's stem
            # (SlurTieLayout::computeUp, slurtielayout.cpp:3086: setUp(!chord->up())).
            #
            # A tie cut by the system's edge is drawn in full on both systems: the
            # earlier one carries it out to the closing bar line, the later one opens
            # it at the staff's left edge, so the break still reads as a tie instead
            # of a pair of stubs.
            limit = margin_left + usable_w
            for left, right in sys_ties:
                li = notes_info.get(id(left))
                ri = notes_info.get(id(right))
                if li is None and ri is None:
                    continue
                if li is not None and ri is not None:
                    if li["x"] >= limit or ri["x"] > limit:
                        continue
                    left_head = printed_shape(left, left.duration,
                                              get_beat_duration(self.song, left.start))[0]
                    right_head = printed_shape(right, right.duration,
                                               get_beat_duration(self.song, right.start))[0]
                    x0 = li["x"] + notation.HEAD_BOX[left_head][2] * 0.25 * s
                    x1 = ri["x"] - notation.HEAD_BOX[right_head][2] * 0.25 * s
                    ref_note, info_ref = left, li
                elif li is not None:
                    # The tie leaves this system: run it to the closing bar line.
                    if li["x"] >= limit:
                        continue
                    left_head = printed_shape(left, left.duration,
                                              get_beat_duration(self.song, left.start))[0]
                    x0 = li["x"] + notation.HEAD_BOX[left_head][2] * 0.25 * s
                    x1 = limit
                    ref_note, info_ref = left, li
                else:
                    # The tie comes in from the break: open it at the staff's left
                    # edge, before the clef and key, and land it on the first note.
                    if ri["x"] > limit:
                        continue
                    right_head = printed_shape(right, right.duration,
                                               get_beat_duration(self.song, right.start))[0]
                    x0 = left_x
                    x1 = ri["x"] - notation.HEAD_BOX[right_head][2] * 0.25 * s
                    ref_note, info_ref = right, ri

                up = not info_ref.get("beam_stem_up", info_ref.get("chord_stem_up", False))
                head = printed_shape(ref_note, ref_note.duration,
                                     get_beat_duration(self.song, ref_note.start))[0]
                # The head's half height (HEAD_BOX is (left, top, right, bottom)) keeps
                # the crescent clear of the ink it leaves from.
                head_half_h = (notation.HEAD_BOX[head][1]
                               - notation.HEAD_BOX[head][3]) * 0.5 * s
                side = -1.0 if up else 1.0
                y = note_y(info_ref["step_draw"], info_ref["clef"], treble_mid_y, bass_mid_y, s) \
                    + side * (head_half_h + notation.TIE_END_SP * s)
                col = HAND_COLORS.get(ref_note.hand, HAND_COLORS[RIGHT])[0]
                if x1 > x0:
                    notation.draw_tie(staff_surf, s, x0, x1, y, up, col)

            # 7b. Pedal brackets: the geometry reserved room for them below the
            # lowest ink, so the line never sits on a note.
            if self._pedal_spans(sys):
                draw_pedal_brackets(staff_surf, self.song.pedal_spans, sys.start_t, sys.end_t,
                                    lambda st_: local_rect.x + margin_left + (st_ - sys.start_t) / sys.duration * usable_w,
                                    geo.pedal_y, s)

        return staff_surf, heads, rolled_chords, geo

    def time_at_x(self, x: float, staff_rect: pygame.Rect, t: float) -> float:
        """
        The song time under screen x on the system drawn at time t (the inverse of `cursor_x`).

        A click on the paper lands on the nearest note or beat of that system, so playback
        starts on the note the click is pointing at instead of a hair before or after it;
        a click out in the empty space keeps its own place. The click never jumps to another
        system: it is the one on the page that answers.
        """
        sys = self.system_for_time(t) if (self.song and self.systems) else None
        if sys is None:
            return 0.0
        geo = self.staff_geometry(sys, staff_rect)
        span = max(1.0, geo.usable_w)
        frac = min(1.0, max(0.0, (x - staff_rect.x - geo.margin_left) / span))
        when = sys.start_t + frac * sys.duration

        # The places worth landing on: where a note starts, and where a beat falls.
        places = [n.start for n in sys.notes if sys.start_t - 1e-9 <= n.start < sys.end_t]
        beats = getattr(self.song, "beat_times", None) or []
        lo = bisect.bisect_left(beats, sys.start_t - 1e-9)
        hi = bisect.bisect_right(beats, sys.end_t + 1e-9)
        places.extend(beats[lo:hi])
        if places:
            best = min(places, key=lambda p: abs(p - when))
            # snap only when the click is close enough to mean that place: 12 px of time
            if abs(best - when) <= sys.duration / span * 12:
                when = best
        return max(0.0, when)

    def draw(self, surf: pygame.Surface, staff_rect: pygame.Rect, t: float, active_notes: Optional[list] = None, opacity: float = 1.0):
        """
        Draw the sheet music: the system's cached paper, the notes that are sounding, the cursor.

        The paper (lines, clefs, signatures, every notehead, stem, beam and tie) is rendered
        once per system by `_render_static_system_surf` and cached, so a frame here costs one
        blit plus the few ellipses of the notes being heard - not a whole engraving pass.
        """
        if staff_rect.h <= 0 or staff_rect.w <= 0:
            return

        opacity = max(0.0, min(1.0, opacity))
        local_rect = pygame.Rect(0, 0, staff_rect.w, staff_rect.h)

        sys = self.system_for_time(t) if (self.song and self.systems) else None

        # The paper is the same on every frame of a system: draw it again only when the
        # system, the panel's size or the zoom changed.
        key = (id(sys), local_rect.w, local_rect.h, self.scale, self.target_dur, id(self.song))
        if self._cached_key != key or self._cached_static_surf is None:
            self._cached_static_surf, self._cached_heads, self._cached_rolled, self._cached_geo = \
                self._render_static_system_surf(sys, local_rect)
            self._cached_key = key

        # One reusable surface the size of the panel: the paper is blitted into it and the
        # moving parts drawn on top, so a frame never allocates (and never copies) a surface.
        if self._scratch is None or self._scratch.get_size() != local_rect.size:
            self._scratch = pygame.Surface(local_rect.size)
        staff_surf = self._scratch
        staff_surf.blit(self._cached_static_surf, (0, 0))

        if self.song and self.systems and sys is not None:
            geo = self._cached_geo
            s = geo.s
            active_ids = {id(n) for n in active_notes} if active_notes is not None else set()

            # 8. What moves: the notes being heard (lighter ink and a ring), then the cursor.
            for n, hx, hy, half_w, half_h, pad, light in self._cached_heads:
                if (id(n) in active_ids) or (n.start <= t < n.end):
                    light(staff_surf)
                    halo = pygame.Rect(round(hx - half_w - pad), round(hy - half_h - pad),
                                       round(2 * half_w + 2 * pad), round(2 * half_h + 2 * pad))
                    pygame.draw.ellipse(staff_surf, CURSOR_COLOR, halo, 2)

            # A rolled chord being swept is marked in the cursor's red, as it was before
            # the paper was cached (the wave itself lives on the cached surface in grey).
            for chord, wx, wy0, wy1 in self._cached_rolled:
                if any((id(n) in active_ids) or (n.start <= t < n.end) for n in chord):
                    draw_arpeggio_wavy_line(staff_surf, wx, wy0, wy1, s, CURSOR_COLOR)

            treble_top = round(geo.treble_mid_y - 2 * s)
            bass_bot = round(geo.bass_mid_y + 2 * s)
            cx = round(self.cursor_x(t, local_rect, geo.margin_left, geo.usable_w))
            cy_top = round(treble_top - s * 0.8)
            cy_bot = round(bass_bot + s * 0.8)
            ch = cy_bot - cy_top

            # Translucent cursor glow band
            glow_surf = pygame.Surface((8, ch), pygame.SRCALPHA)
            glow_surf.fill((*CURSOR_COLOR, 50))
            staff_surf.blit(glow_surf, (cx - 4, cy_top))

            # Main cursor line
            pygame.draw.line(staff_surf, CURSOR_COLOR, (cx, cy_top), (cx, cy_bot), 2)

            # Cursor triangles at top and bottom
            tri_top = [(cx - 4, cy_top - 3), (cx + 4, cy_top - 3), (cx, cy_top + 3)]
            tri_bot = [(cx - 4, cy_bot + 3), (cx + 4, cy_bot + 3), (cx, cy_bot - 3)]
            pygame.draw.polygon(staff_surf, CURSOR_COLOR, tri_top)
            pygame.draw.polygon(staff_surf, CURSOR_COLOR, tri_bot)

        # Blit staff_surf with requested opacity (set_alpha(None) keeps the opaque blit fast)
        if opacity < 0.999:
            staff_surf.set_alpha(max(0, min(255, int(round(opacity * 255)))))
        else:
            staff_surf.set_alpha(None)
        surf.blit(staff_surf, staff_rect.topleft)

        # Panel edge border
        pygame.draw.line(surf, PANEL_EDGE, (staff_rect.left, staff_rect.bottom - 1),
                         (staff_rect.right, staff_rect.bottom - 1), 1)
