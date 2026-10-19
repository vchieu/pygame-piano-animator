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
from typing import List, Optional, Tuple

import pygame

from common import (ACCENT, BAR_BG, BAR_LINE, BG, HAND_COLORS, PANEL_EDGE,
                    TEXT, TEXT_DIM, ui_font)
from midi_loader import LEFT, RIGHT, Note
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

# Diatonic step in octave (C=0, D=1, E=2, F=3, G=4, A=5, B=6) and sharp flag (0 or 1)
# MIDI pitch % 12 -> (diatonic_step_in_octave, is_sharp)
PITCH_MAP = {
    0: (0, 0),   # C
    1: (0, 1),   # C#
    2: (1, 0),   # D
    3: (1, 1),   # D#
    4: (2, 0),   # E
    5: (3, 0),   # F
    6: (3, 1),   # F#
    7: (4, 0),   # G
    8: (4, 1),   # G#
    9: (5, 0),   # A
    10: (5, 1),  # A#
    11: (6, 0),  # B
}

# Diatonic step definitions:
# Treble middle line = B4 = step 34 (Lines: E4=30, G4=32, B4=34, D5=36, F5=38)
# Bass middle line = D3 = step 22 (Lines: G2=18, B2=20, D3=22, F3=24, A3=26)
# Middle C = C4 = step 28 (1 ledger line below Treble staff, 1 ledger line above Bass staff)
TREBLE_MID_STEP = 34
BASS_MID_STEP = 22
MIDDLE_C_STEP = 28

MARGIN_LEFT = 85
MARGIN_RIGHT = 20

# Zoom of the staff: MIN/MAX_TARGET_DUR bound how many seconds of music may share one system
# (MIN_SCALE / MAX_SCALE bound how big the staff itself is drawn).
MIN_TARGET_DUR = 3.0
MAX_TARGET_DUR = 20.0
MIN_SCALE = 0.6
MAX_SCALE = 1.5

# Upper limit on the notes one beam may join. In practice a beat decides it and this only
# stops a very fast beat from turning into one endless bar across the whole measure.
MAX_BEAM_CHORDS = 8

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

    # Distribute notes into systems
    sys_starts = [sys.start_t for sys in systems]
    for n in getattr(song, "notes", []):
        if not systems:
            break
        idx = bisect.bisect_right(sys_starts, n.start) - 1
        idx = max(0, min(len(systems) - 1, idx))
        systems[idx].notes.append(n)

    return systems


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


def time_sig_x(left_x: float, sp: float) -> float:
    """
    Where the time signature's column sits: `left_x + 4.6 sp`, or further right if the
    clefs would run into it (a 2.66 sp bass clef at `left_x + 8` reaches `left_x + 4.6 sp`
    at the smallest staff sizes, s = 4). The left margin is only so wide - the first note
    is at MARGIN_LEFT - so the column does not move further than it has to.
    """
    half = notation.TIME_SIG_HALF_W_SP * sp
    return max(left_x + round(sp * 4.6),
               left_x + 8 + round(clef_ink_w(sp) + (TIME_SIG_GAP_SPACES * sp + half)))


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
    step, _ = diatonic_step(n.pitch)
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
            curr.append(n)
        else:
            clusters.append(curr)
            curr = [n]
    if curr:
        clusters.append(curr)
    return clusters


def is_beamable(sub: List[Note], beat_of) -> bool:
    """True when a chord is short enough to be joined to its neighbours by a beam."""
    return note_value(sub[0].duration, beat_of(sub[0].start)) in (EIGHTH, DOTTED_EIGHTH, SIXTEENTH)


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

                joined = False
                if curr and is_beamable(sub, beat_of):
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
                curr = [sub] if is_beamable(sub, beat_of) else []
            if len(curr) >= 2:
                beam_groups.append((c, curr))
    return beam_groups


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

# What a bar hanging off the top or bottom of the staff panel costs, per pixel it pokes
# out. Set past any plausible overlap with a notehead (a notehead is ~1 space, tens of
# pixels, tall), so a bar that would leave the panel only does so when nothing inside it
# is better - a beam drawn over the falling notes above the staff is worse than one drawn
# across a note, but it is still a last resort.
BEAM_OFF_PANEL_W = 4.0


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

    The stems and the second bar of a run of sixteenths are counted too, not just the
    outer bar, so the direction is chosen on what the whole beam would cover.
    """
    half = beam_w / 2.0
    sw = max(1, round(STEM_WIDTH_SPACES * s)) / 2.0
    step = max(1, round(s * 0.25))
    slide = max(step, round(s * BEAM_SLIDE_SPACES))
    any16 = any(st[3] for st in stops)

    def stem_x(x, up):
        """The x the stems stand on: the notehead's far edge, as `draw_staff_note` puts it."""
        return round(x + nw - 1) if up else round(x - nw + 1)

    def off_panel(tip):
        """How far a bar at `tip` would hang outside the staff panel (0 when it fits)."""
        if panel is None:
            return 0.0
        return max(0.0, panel[0] - (tip - half)) + max(0.0, (tip + half) - panel[1])

    def run_on(tip, up):
        """What a bar at `tip`, with its stems `up`, would be printed on."""
        off = -gap if up else gap
        left, right = stem_x(stops[0][0], up), stem_x(stops[-1][0], up)
        total = ink_overlap(left, right, tip - half, tip + half, boxes, own)
        if any16:
            total += ink_overlap(left, right, tip + off - half, tip + off + half, boxes, own)
        for x, y_top, y_bot, _ in stops:
            sx = stem_x(x, up)
            far = y_bot if up else y_top          # the stem starts at the chord's far edge
            total += ink_overlap(sx - sw, sx + sw, min(far, tip), max(far, tip), boxes, own)
        return total + BEAM_OFF_PANEL_W * off_panel(tip)

    def place(up):
        """The bar's height with the stems `up`, and what it would cover there."""
        base = round(min(st[1] for st in stops) - reach) if up \
            else round(max(st[2] for st in stops) + reach)
        best, best_hit = base, run_on(base, up)
        if best_hit <= 0.0:
            return best, best_hit
        away = -1 if up else 1                   # outboard: further from the group's notes
        for k in range(step, slide + step, step):
            cand = round(base + away * k)
            hit = run_on(cand, up)
            if hit <= 0.0:
                return cand, hit
            if hit < best_hit:
                best, best_hit = cand, hit
        return best, best_hit

    first_tip, first_hit = place(default_up)
    other_tip, other_hit = place(not default_up)
    # A bar drawn off the staff panel is worse than one drawn across a note, so turning
    # the stems over is not a licence to leave the panel: a direction whose bar fits beats
    # one whose bar does not, whatever it would have covered. When both fit - the usual
    # case - it is the covering that decides, and the engraver's direction wins a tie.
    if off_panel(first_tip) <= 0.0 and off_panel(other_tip) > 0.0:
        return default_up, first_tip
    if off_panel(other_tip) <= 0.0 and off_panel(first_tip) > 0.0:
        return not default_up, other_tip
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


def compute_notes_info(sys_notes: List[Note], sys_start: float, sys_duration: float, usable_w: float,
                       margin_left: float, s: float, beat_len: float = 0.5) -> dict:
    """
    Computes rendering info for notes in a system:
    x coordinate (with clashing/overlap offset), clef, step_draw, octave_shift, label_text.
    Returns dict mapping id(n) -> dict(...)

    Only a real clash moves a note sideways:
    - notes a second (or unison) apart in the same clef, which would overlap head-on;
    - notes that follow in time so closely that the second one's sharp or the first one's
      dot / flag would run into it.
    The other notes of a chord stay on the same x, so it reads as a chord, not a staircase.
    """
    info = {}
    if not sys_notes:
        return info

    nw = round(s * 0.75)
    right_limit = margin_left + usable_w
    sorted_notes = sorted(sys_notes, key=lambda n: (n.start, n.pitch))

    # A chord is one moment: its notes share one x, even when the file staggered
    # their onsets a little, so it is drawn as a chord and not as a little cascade.
    clusters = group_chords(sorted_notes, beat_len)

    # Pre-calculate base attributes
    for cluster in clusters:
        chord_t = cluster[0].start
        chord_x = margin_left + (chord_t - sys_start) / sys_duration * usable_w
        for n in cluster:
            clef = clef_for_note(n)
            step_draw, oct_shift, label = get_octave_transposition(n, clef)
            info[id(n)] = {
                "n": n,
                "base_x": chord_x,
                "x": chord_x,
                "clef": clef,
                "step_draw": step_draw,
                "octave_shift": oct_shift,
                "label": label,
            }

    # Process each cluster for x-offsets when notes clash in same clef
    for cluster in clusters:
        by_clef = {CLEF_TREBLE: [], CLEF_BASS: []}
        for n in cluster:
            c = info[id(n)]["clef"]
            by_clef[c].append(n)

        for c, c_notes in by_clef.items():
            if len(c_notes) <= 1:
                continue
            # Revoke octave transposition if it causes notes in the same chord/clef to collide
            for n in c_notes:
                n_info = info[id(n)]
                if n_info["octave_shift"] != 0:
                    other_steps = [info[id(other)]["step_draw"] for other in c_notes if other is not n]
                    if any(abs(n_info["step_draw"] - ost) <= 1 for ost in other_steps):
                        step_nat, _ = diatonic_step(n.pitch)
                        n_info["step_draw"] = step_nat
                        n_info["octave_shift"] = 0
                        n_info["label"] = ""

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
    def cluster_head(cluster: List[Note]) -> float:
        """How far to the left of their x the notes of `cluster` reach (an accidental)."""
        return max((s * 0.8 for n in cluster if diatonic_step(n.pitch)[1]), default=0.0)

    def cluster_tail(cluster: List[Note]) -> float:
        """How far to the right of their x the notes of `cluster` reach (notehead, dot, flag)."""
        tail = nw
        for n in cluster:
            nv = note_value(n.duration, beat_len)
            if nv in (DOTTED_HALF, DOTTED_QUARTER, DOTTED_EIGHTH):
                tail = max(tail, nw + s * 0.6)
            elif nv in (EIGHTH, SIXTEENTH):
                tail = max(tail, nw + s * 0.9)
        return tail

    min_gap = nw + 1.0
    for i in range(1, len(clusters)):
        prev_cluster, curr_cluster = clusters[i - 1], clusters[i]
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


# How far the bracket's dashed line keeps off the notes it covers: a bit under a third of a
# staff space, so it sits close enough to read as part of them and far enough to be clear.
OCTAVE_GAP_SPACES = 0.35


def draw_octave_brackets(surf: pygame.Surface, notes_info: dict, sys_notes: List[Note],
                         treble_mid_y: float, bass_mid_y: float, s: float,
                         local_rect: Optional[pygame.Rect] = None):
    """
    The 8va / 15ma and 8vb / 15mb brackets over each group of transposed notes.

    A group holds notes shifted by their own amount (get_octave_transposition), so it
    covers a range of drawn heights - a chord of E7 + C6 puts the E7 at -1.5 sp and the
    C6 at -2.5 sp from the treble middle line. The line's height is worked out from the
    group's own notes, so it always clears all of them: fully above for 8va/15ma (and
    above an up-stem too, which pokes higher), fully below for 8vb/15mb. `local_rect` is
    the staff panel, and the line is kept inside it - for the few pitches whose notes sit
    right at the panel's edge the clamp wins, since a bracket off the panel is worse than
    one crossing a notehead.
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

    # Group notes with same (clef, octave_shift, label)
    groups = []
    curr_group = []
    for n in sorted_notes:
        ni = notes_info[id(n)]
        key = (ni["clef"], ni["octave_shift"], ni["label"])
        if not curr_group:
            curr_group.append((n, key))
        else:
            prev_ni = notes_info[id(curr_group[-1][0])]
            prev_key = (prev_ni["clef"], prev_ni["octave_shift"], prev_ni["label"])
            # If same key and start times close (< 4.0s)
            if key == prev_key and (n.start - curr_group[-1][0].start) < 4.0:
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
        clef, shift, label = grp[0][1]
        grp_notes = [item[0] for item in grp]
        grp_ids = {id(n) for n in grp_notes}
        xs = [notes_info[id(n)]["x"] for n in grp_notes]
        x_min = min(xs) - nw - 4
        x_max = max(xs) + nw + 12

        lbl_surf = font.render(label, True, color)
        lbl_w = lbl_surf.get_width()
        lbl_h = lbl_surf.get_height()

        steps = [notes_info[id(n)]["step_draw"] for n in grp_notes]
        ys = [note_y(st, clef, treble_mid_y, bass_mid_y, s) for st in steps]
        mid_step = TREBLE_MID_STEP if clef == CLEF_TREBLE else BASS_MID_STEP
        up = stem_up_for(steps, mid_step)

        # Notes of *other* groups that the label, printed to the left of this group's first
        # note, would print on. (This group's own notes are already accounted for below.)
        touching = [band for band in ink_bands[clef]
                    if band[0] not in grp_ids
                    and band[2] >= x_min - gap and band[1] <= x_min + lbl_w + gap]
        if clef == CLEF_TREBLE:
            # 8va / 15ma: fully above the notes, and above a stem that pokes further up
            bracket_y = min(ys) - head_h - (stem_len if up else 0.0) - gap
            # ...and, if the panel has room for it, clear of the noteheads the label would
            # print on. When it does not fit, the height that clears this group's own notes
            # stands: that is the line's job, and pushing further would only land the whole
            # bracket somewhere worse.
            if touching:
                want = min(b[3] for b in touching) - lbl_h * 0.5 - gap
                if local_rect is None or want >= local_rect.top + gap:
                    bracket_y = min(bracket_y, want)
            if local_rect is not None:
                bracket_y = max(bracket_y, local_rect.top + gap)
            edge = min(ys) - head_h - gap                 # where the hook should stop
        else:
            # 8vb / 15mb: fully below the notes, and below a stem that pokes further down
            bracket_y = max(ys) + head_h + (0.0 if up else stem_len) + gap
            if touching:
                want = max(b[4] for b in touching) + lbl_h * 0.5 + gap
                if local_rect is None or want <= local_rect.bottom - gap:
                    bracket_y = max(bracket_y, want)
            if local_rect is not None:
                bracket_y = min(bracket_y, local_rect.bottom - gap)
            edge = max(ys) + head_h + gap

        surf.blit(lbl_surf, (round(x_min), round(bracket_y - lbl_h * 0.5)))
        line_x_start = x_min + lbl_w + 3
        if x_max > line_x_start:
            draw_dashed_line(surf, color, (line_x_start, bracket_y), (x_max, bracket_y), width=line_w)
            # The hook points back at the notes: down for 8va, up for 8vb, its tip stopping
            # just short of the group's nearest ink (never past it, never floating off it).
            hook_y = (edge if (bracket_y - edge) > s * 0.7 else bracket_y - s * 0.7) \
                if clef == CLEF_TREBLE else \
                (edge if (edge - bracket_y) > s * 0.7 else bracket_y + s * 0.7)
            pygame.draw.line(surf, color, (round(x_max), round(bracket_y)), (round(x_max), round(hook_y)), line_w)

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
        if song is not None:
            self.set_song(song, target_dur=self.target_dur, scale=self.scale)

    def set_song(self, song, target_dur: float = 10.0, scale: float = 1.0):
        self.target_dur = clamp_target_dur(target_dur)
        self.scale = clamp_scale(scale)
        self.song = song
        self.systems = build_systems(song, self.target_dur)
        self._system_starts = [sys.start_t for sys in self.systems]

    def system_for_time(self, t: float) -> Optional[StaffSystem]:
        """Return the active system for playback time t."""
        if not self.systems:
            return None
        if t <= 0.0:
            return self.systems[0]
        idx = bisect.bisect_right(self._system_starts, t) - 1
        idx = max(0, min(len(self.systems) - 1, idx))
        return self.systems[idx]

    def cursor_x(self, t: float, staff_rect: pygame.Rect) -> float:
        """Compute the X coordinate of the cursor within staff_rect at song time t."""
        sys = self.system_for_time(t)
        if sys is None:
            return float(staff_rect.x + MARGIN_LEFT)

        usable_w = staff_rect.w - MARGIN_LEFT - MARGIN_RIGHT
        if usable_w <= 10:
            return float(staff_rect.x + MARGIN_LEFT)

        if t < sys.start_t:
            frac = 0.0
        elif t >= sys.end_t:
            frac = 1.0
        else:
            frac = (t - sys.start_t) / sys.duration
        return staff_rect.x + MARGIN_LEFT + frac * usable_w

    def draw(self, surf: pygame.Surface, staff_rect: pygame.Rect, t: float, active_notes: Optional[list] = None, opacity: float = 1.0):
        """Draw the sheet music Grand Staff, active system notes, and playback cursor."""
        if staff_rect.h <= 0 or staff_rect.w <= 0:
            return

        opacity = max(0.0, min(1.0, opacity))

        # Render staff offscreen to handle transparency cleanly
        staff_surf = pygame.Surface(staff_rect.size)
        staff_surf.fill(STAFF_BG)

        local_rect = pygame.Rect(0, 0, staff_rect.w, staff_rect.h)

        # 2. Staff geometry (the user's zoom scales the staff itself)
        s = max(4.0, min(12.0, ((local_rect.h - 26) / 14.0) * self.scale))
        center_y = local_rect.centery
        treble_mid_y = center_y - round(3.0 * s)
        bass_mid_y = center_y + round(3.0 * s)

        line_color = STAFF_LINE
        left_x = local_rect.x + 18
        right_x = local_rect.right - 12

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

        # 5. Time signature '4/4' - the numerator on the line above the middle one and the
        # denominator on the line below (notation.draw_time_signature places them). The
        # old UI-font digits are the fallback for a build without the music font.
        ts_x = time_sig_x(left_x, s)
        drawn = [notation.draw_time_signature(staff_surf, ts_x, ts_mid, s, 4, 4, TIME_SIG_COLOR)
                 for ts_mid in (treble_mid_y, bass_mid_y)]
        if not all(drawn):
            ts_font = ui_font(max(10, int(s * 1.6)), bold=True)
            ts_img = ts_font.render("4", True, TIME_SIG_COLOR)
            for ts_mid in (treble_mid_y, bass_mid_y):
                staff_surf.blit(ts_img, ts_img.get_rect(center=(ts_x, round(ts_mid - s * 0.9))))
                staff_surf.blit(ts_img, ts_img.get_rect(center=(ts_x, round(ts_mid + s * 0.9))))

        if self.song and self.systems:
            # 6. Active system
            sys = self.system_for_time(t)
            if sys is not None:
                usable_w = local_rect.w - MARGIN_LEFT - MARGIN_RIGHT
                if usable_w > 10:
                    # Measure bar lines
                    num_font = ui_font(10)
                    for m in sys.measures:
                        if m.index > sys.measures[0].index:
                            frac_bar = (m.start_t - sys.start_t) / sys.duration
                            bx = round(local_rect.x + MARGIN_LEFT + frac_bar * usable_w - 10)
                            pygame.draw.line(staff_surf, BAR_LINE_COLOR, (bx, treble_top), (bx, bass_bot), 1)
                            # Measure number at the top of the bar line
                            num_surf = num_font.render(str(m.index + 1), True, MEASURE_NUM_COLOR)
                            staff_surf.blit(num_surf, (bx + 3, treble_top - num_surf.get_height() - 1))

                    # System ending bar line
                    sys_end_x = round(local_rect.x + MARGIN_LEFT + usable_w)
                    is_song_end = (sys.index == len(self.systems) - 1)
                    if is_song_end:
                        pygame.draw.line(staff_surf, BAR_LINE_COLOR, (sys_end_x - 4, treble_top), (sys_end_x - 4, bass_bot), 1)
                        pygame.draw.line(staff_surf, BAR_LINE_COLOR, (sys_end_x, treble_top), (sys_end_x, bass_bot), 3)
                    else:
                        pygame.draw.line(staff_surf, BAR_LINE_COLOR, (sys_end_x, treble_top), (sys_end_x, bass_bot), 1)

                    # 7. Draw Notes
                    active_ids = {id(n) for n in active_notes} if active_notes is not None else set()
                    nw = round(s * 0.75)
                    nh = round(s * 0.52)
                    stem_len = round(STEM_LEN_SPACES * s)

                    notes_info = compute_notes_info(sys.notes, sys.start_t, sys.duration, usable_w,
                                                    MARGIN_LEFT, s, get_beat_duration(self.song))
                    draw_octave_brackets(staff_surf, notes_info, sys.notes, treble_mid_y, bass_mid_y,
                                         s, local_rect)

                    # Group sys.notes into chords, the same way compute_notes_info did
                    sorted_notes = sorted(sys.notes, key=lambda n: (n.start, n.pitch))
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
                    for n in sys.notes:
                        i = notes_info[id(n)]
                        ny_ = note_y(i["step_draw"], i["clef"], treble_mid_y, bass_mid_y, s)
                        hw = round(nw * 1.15) if note_value(n.duration,
                                                             get_beat_duration(self.song, n.start)) == WHOLE else nw
                        all_heads.append((i["x"] - hw, i["x"] + hw, ny_ - nh, ny_ + nh, id(n)))
                    heads = NoteHeads(all_heads)

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
                        for n in sys.notes:
                            if m.start_t <= n.start < m.end_t:
                                measure_of[id(n)] = m

                    beam_groups = group_beams(time_clusters, notes_info,
                                              lambda st_: get_beat_duration(self.song, st_), measure_of)
                    for bg in (g[1] for g in beam_groups):
                        beamed_note_ids.update(id(n) for sub in bg for n in sub)

                    # Compute chord-level stem direction and y-bounds for each cluster
                    for tc_idx, tc in enumerate(time_clusters):
                        by_c = {CLEF_TREBLE: [], CLEF_BASS: []}
                        for n in tc:
                            c = notes_info[id(n)]["clef"]
                            by_c[c].append(n)

                        for c, c_notes in by_c.items():
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
                            if (note_value(c_notes[0].duration, beat_len) != WHOLE
                                    and not any(id(n) in beamed_note_ids for n in c_notes)):
                                own_c = {id(n) for n in c_notes}
                                cx = notes_info[id(c_notes[0])]["x"]
                                flags = max(notation.note_shape(n.duration, beat_len)[1]
                                            for n in c_notes)
                                near = heads.near(cx - nw - s, cx + nw + 2 * s)
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
                                # the notes struck together on one staff share a single stem
                                info_n["stem_key"] = (tc_idx, c)

                            # A rolled chord ("arpeggio") is written with its notes
                            # slightly staggered - they are plucked or swept one after
                            # another, not all together. That stagger is what marks it,
                            # so a plain block chord of three or more notes is left alone.
                            rolled = len(c_notes) >= 3 and (c_notes[-1].start - c_notes[0].start) >= 0.04 * beat_len
                            if rolled:
                                chord_x = min(notes_info[id(n)]["x"] for n in c_notes)
                                arp_col = CURSOR_COLOR if any((id(n) in active_ids) or (n.start <= t < n.end)
                                                              for n in c_notes) else (60, 60, 75)
                                draw_arpeggio_wavy_line(staff_surf, chord_x - nw - round(s * 0.6),
                                                        y_top - s * 0.4, y_bot + s * 0.4, s, arp_col)

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
                            is16 = any(note_value(n.duration, get_beat_duration(self.song, n.start)) == SIXTEENTH
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
                        near = heads.near(min(st[0] for st in stops) - nw,
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
                        off = -gap if beam_stem_up else gap
                        for run in inner:
                            shifted = [(x, y + off) for x, y in run]
                            if len(run) >= 2:
                                pygame.draw.lines(staff_surf, STEM_COLOR, False, shifted, beam_w)
                            else:      # a lone 16th gets a short stub of its own
                                x, y = shifted[0]
                                pygame.draw.line(staff_surf, STEM_COLOR, (x - round(s * 0.6), y), (x, y), beam_w)

                    # Draw individual notes - through notation.py, which draws Leland's
                    # SMuFL glyphs at MuseScore's metrics (see docs/ARCHITECTURE.md)
                    rendered_chord_stems = set()
                    for n in sorted_notes:
                        n_info = notes_info[id(n)]
                        nx = n_info["x"]
                        clef = n_info["clef"]
                        step = n_info["step_draw"]
                        _, is_sharp = diatonic_step(n.pitch)
                        ny = note_y(step, clef, treble_mid_y, bass_mid_y, s)

                        sounding = (id(n) in active_ids) or (n.start <= t < n.end)
                        hand_col = HAND_COLORS.get(n.hand, HAND_COLORS[RIGHT])[0]

                        b_len = get_beat_duration(self.song, n.start)
                        shape = note_shape(n.duration, b_len)
                        head_half = notation.HEAD_BOX[shape[0]][2] * 0.5 * s

                        # A sounding note keeps the red halo it had before (drawn under
                        # the notehead, so it shows around it)
                        if sounding:
                            pad = max(2, round(s * 0.25))
                            halo = pygame.Rect(round(nx - head_half - pad), round(ny - nh - pad),
                                               round(2 * head_half + 2 * pad), round(2 * nh + 2 * pad))
                            pygame.draw.ellipse(staff_surf, CURSOR_COLOR, halo, 2)

                        # Ledger lines: MuseScore's thickness, reaching a ledgerLineLength
                        # past the notehead on each side (ChordLayout::updateLedgerLines)
                        ledger_col = line_color
                        ext = notation.LEDGER_EXT_SP * s
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

                        # One stem for the whole chord: notes struck together on one staff
                        # share a single stem, all pointing the same way.
                        stem_key = n_info.get("stem_key", (id(n), clef))
                        stem = None
                        if shape[0] != "whole" and stem_key not in rendered_chord_stems:
                            rendered_chord_stems.add(stem_key)
                            if stem_up:
                                tip_y = n_info.get("beam_tip_y", round(y_top - stem_len))
                                stem = (round(y_bot), tip_y)
                            else:
                                tip_y = n_info.get("beam_tip_y", round(y_bot + stem_len))
                                stem = (round(y_top), tip_y)

                        notation.draw_staff_note(staff_surf, nx, ny, s, shape, hand_col, stem_up,
                                                 sounding, stem=stem,
                                                 beamed=id(n) in beamed_note_ids,
                                                 sharp=is_sharp, on_line=(step % 2 == 0))

                    # 8. Playback cursor
                    cx = round(self.cursor_x(t, local_rect))
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

        # Blit staff_surf with requested opacity
        if opacity < 0.999:
            staff_surf.set_alpha(max(0, min(255, int(round(opacity * 255)))))
        surf.blit(staff_surf, staff_rect.topleft)

        # Panel edge border
        pygame.draw.line(surf, PANEL_EDGE, (staff_rect.left, staff_rect.bottom - 1),
                         (staff_rect.right, staff_rect.bottom - 1), 1)
