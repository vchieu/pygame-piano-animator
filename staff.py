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
                    TEXT, TEXT_DIM, mix, ui_font)
from midi_loader import LEFT, RIGHT, Note

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
    """Classify note value string based on duration and beat duration."""
    beats = duration / max(0.001, beat_len)
    if beats >= 3.2:
        return WHOLE
    elif beats >= 2.4:
        return DOTTED_HALF
    elif beats >= 1.6:
        return HALF
    elif beats >= 1.2:
        return DOTTED_QUARTER
    elif beats >= 0.7:
        return QUARTER
    elif beats >= 0.55:
        return DOTTED_EIGHTH
    elif beats >= 0.35:
        return EIGHTH
    else:
        return SIXTEENTH


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

    avg_bar = max(0.5, song.duration / max(1, len(measures)))
    bars_per_sys = max(2, min(5, round(target_dur / avg_bar)))

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
    """Draw a G clef centered around B4 (mid_y) with inner spiral wrapping G4 (mid_y + s)."""
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
    """Draw an F clef with starting dot on F3 (mid_y - s) and two dots in surrounding spaces."""
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


def draw_sharp(surf: pygame.Surface, x: float, y: float, s: float, color: Tuple[int, int, int]):
    """Draw a musical sharp (♯) accidental at (x, y)."""
    h = s * 0.9
    w = s * 0.5
    # Two vertical lines
    pygame.draw.line(surf, color, (round(x - w * 0.3), round(y - h)), (round(x - w * 0.3), round(y + h)), 1)
    pygame.draw.line(surf, color, (round(x + w * 0.3), round(y - h)), (round(x + w * 0.3), round(y + h)), 1)
    # Two slanted cross bars
    pygame.draw.line(surf, color, (round(x - w * 0.8), round(y - h * 0.2)), (round(x + w * 0.8), round(y - h * 0.5)), 2)
    pygame.draw.line(surf, color, (round(x - w * 0.8), round(y + h * 0.4)), (round(x + w * 0.8), round(y + h * 0.1)), 2)


def draw_flags(surf: pygame.Surface, tip_x: float, tip_y: float, s: float, stem_up: bool, color: Tuple[int, int, int], count: int = 1):
    """Draw 1 or 2 flags on the tip of a stem swooping to the right using elegant curved lines."""
    flag_spacing = max(3.5, s * 0.48)
    line_w = max(1, round(s * 0.18))
    for f in range(count):
        dy = (f * flag_spacing) if stem_up else (-f * flag_spacing)
        y0 = tip_y + dy
        if stem_up:
            p0 = (tip_x, y0)
            p1 = (tip_x + s * 0.85, y0 + s * 0.5)
            p2 = (tip_x + s * 0.65, y0 + s * 1.8)
        else:
            p0 = (tip_x, y0)
            p1 = (tip_x + s * 0.85, y0 - s * 0.5)
            p2 = (tip_x + s * 0.65, y0 - s * 1.8)

        pts = []
        for i in range(16):
            t = i / 15.0
            bx = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t ** 2 * p2[0]
            by = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t ** 2 * p2[1]
            pts.append((round(bx), round(by)))

        if len(pts) >= 2:
            pygame.draw.lines(surf, color, False, pts, line_w)


def get_octave_transposition(n: Note, clef: str) -> Tuple[int, int, str]:
    """
    Returns (step_draw, octave_shift, label_text) for a note.
    octave_shift is in octaves (-2, -1, 0, +1, +2).
    """
    step, _ = diatonic_step(n.pitch)
    if clef == CLEF_TREBLE:
        if n.pitch >= 93:
            return step - 14, -2, "15ma"
        elif n.pitch >= 81:
            return step - 7, -1, "8va"
    elif clef == CLEF_BASS:
        if n.pitch <= 31:
            return step + 14, 2, "15mb"
        elif n.pitch <= 43:
            return step + 7, 1, "8vb"
    return step, 0, ""


def compute_notes_info(sys_notes: List[Note], sys_start: float, sys_duration: float, usable_w: float, margin_left: float, s: float) -> dict:
    """
    Computes rendering info for notes in a system:
    x coordinate (with clashing/overlap offset), clef, step_draw, octave_shift, label_text.
    Returns dict mapping id(n) -> dict(...)
    """
    info = {}
    if not sys_notes:
        return info

    nw = round(s * 0.75)
    sorted_notes = sorted(sys_notes, key=lambda n: (n.start, n.pitch))

    # Pre-calculate base attributes
    for n in sorted_notes:
        frac_n = (n.start - sys_start) / sys_duration
        base_x = margin_left + frac_n * usable_w
        clef = clef_for_note(n)
        step_draw, oct_shift, label = get_octave_transposition(n, clef)
        info[id(n)] = {
            "n": n,
            "base_x": base_x,
            "x": base_x,
            "clef": clef,
            "step_draw": step_draw,
            "octave_shift": oct_shift,
            "label": label,
        }

    # Group notes into time clusters (|n1.start - n2.start| < 0.03)
    clusters = []
    curr_cluster = []
    for n in sorted_notes:
        if not curr_cluster:
            curr_cluster.append(n)
        else:
            if abs(n.start - curr_cluster[0].start) < 0.03:
                curr_cluster.append(n)
            else:
                clusters.append(curr_cluster)
                curr_cluster = [n]
    if curr_cluster:
        clusters.append(curr_cluster)

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

                # Check if same step or adjacent step (second interval |step1 - step2| <= 1)
                step_diff = abs(c_info["step_draw"] - p_info["step_draw"])
                if step_diff <= 1 or abs(c_info["x"] - p_info["x"]) < (2 * nw + 2):
                    min_x = p_info["x"] + 1.8 * nw
                    if c_info["x"] < min_x:
                        c_info["x"] = min_x

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


def draw_octave_brackets(surf: pygame.Surface, notes_info: dict, sys_notes: List[Note], treble_mid_y: float, bass_mid_y: float, s: float):
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

    for grp in groups:
        clef, shift, label = grp[0][1]
        grp_notes = [item[0] for item in grp]
        xs = [notes_info[id(n)]["x"] for n in grp_notes]
        x_min = min(xs) - nw - 4
        x_max = max(xs) + nw + 12

        lbl_surf = font.render(label, True, color)
        lbl_w = lbl_surf.get_width()
        lbl_h = lbl_surf.get_height()

        if clef == CLEF_TREBLE:
            # Above treble staff
            bracket_y = treble_mid_y - 2.8 * s
            surf.blit(lbl_surf, (round(x_min), round(bracket_y - lbl_h * 0.5)))
            line_x_start = x_min + lbl_w + 3
            if x_max > line_x_start:
                draw_dashed_line(surf, color, (line_x_start, bracket_y), (x_max, bracket_y), width=line_w)
                # Downward hook
                hook_y = bracket_y + s * 0.7
                pygame.draw.line(surf, color, (round(x_max), round(bracket_y)), (round(x_max), round(hook_y)), line_w)
        else:
            # Below bass staff
            bracket_y = bass_mid_y + 2.8 * s
            surf.blit(lbl_surf, (round(x_min), round(bracket_y - lbl_h * 0.5)))
            line_x_start = x_min + lbl_w + 3
            if x_max > line_x_start:
                draw_dashed_line(surf, color, (line_x_start, bracket_y), (x_max, bracket_y), width=line_w)
                # Upward hook
                hook_y = bracket_y - s * 0.7
                pygame.draw.line(surf, color, (round(x_max), round(bracket_y)), (round(x_max), round(hook_y)), line_w)

class StaffView:
    """
    Renders a Grand Staff sheet music system with dynamic cursor and paging.
    Supports clean paper white background, custom opacity, and accurate note values.
    """
    def __init__(self, song=None):
        self.song = None
        self.systems: List[StaffSystem] = []
        self._system_starts: List[float] = []
        if song is not None:
            self.set_song(song)

    def set_song(self, song):
        self.song = song
        self.systems = build_systems(song)
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

        # 2. Staff geometry
        s = max(5.0, min(9.0, (local_rect.h - 26) / 14.0))
        center_y = local_rect.centery
        treble_mid_y = center_y - round(3.0 * s)
        bass_mid_y = center_y + round(3.0 * s)

        line_color = STAFF_LINE
        left_x = local_rect.x + 18
        right_x = local_rect.right - 12

        # 3. Staff lines (5 for Treble, 5 for Bass)
        for i in range(-2, 3):
            ty = round(treble_mid_y + i * s)
            pygame.draw.line(staff_surf, line_color, (left_x, ty), (right_x, ty), 1)
            by = round(bass_mid_y + i * s)
            pygame.draw.line(staff_surf, line_color, (left_x, by), (right_x, by), 1)

        # Left connecting bar and bracket
        treble_top = round(treble_mid_y - 2 * s)
        bass_bot = round(bass_mid_y + 2 * s)
        pygame.draw.line(staff_surf, line_color, (left_x, treble_top), (left_x, bass_bot), 1)
        # Bracket on the far left
        br_x = local_rect.x + 12
        pygame.draw.line(staff_surf, line_color, (br_x, treble_top), (br_x, bass_bot), 3)
        pygame.draw.line(staff_surf, line_color, (br_x - 3, treble_top - 2), (br_x + 3, treble_top), 2)
        pygame.draw.line(staff_surf, line_color, (br_x - 3, bass_bot + 2), (br_x + 3, bass_bot), 2)

        # 4. Clefs
        draw_treble_clef(staff_surf, left_x + 8, treble_mid_y, s, CLEF_COLOR)
        draw_bass_clef(staff_surf, left_x + 8, bass_mid_y, s, CLEF_COLOR)

        # 5. Time signature '4/4'
        ts_x = left_x + round(s * 4.6)
        ts_font = ui_font(max(10, int(s * 1.6)), bold=True)
        ts_img = ts_font.render("4", True, TIME_SIG_COLOR)
        # Treble staff 4/4
        staff_surf.blit(ts_img, ts_img.get_rect(center=(ts_x, round(treble_mid_y - s * 0.9))))
        staff_surf.blit(ts_img, ts_img.get_rect(center=(ts_x, round(treble_mid_y + s * 0.9))))
        # Bass staff 4/4
        staff_surf.blit(ts_img, ts_img.get_rect(center=(ts_x, round(bass_mid_y - s * 0.9))))
        staff_surf.blit(ts_img, ts_img.get_rect(center=(ts_x, round(bass_mid_y + s * 0.9))))

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
                    stem_len = round(3.2 * s)

                    notes_info = compute_notes_info(sys.notes, sys.start_t, sys.duration, usable_w, MARGIN_LEFT, s)
                    draw_octave_brackets(staff_surf, notes_info, sys.notes, treble_mid_y, bass_mid_y, s)

                    # Sort notes so lower notes draw first
                    notes_sorted = sorted(sys.notes, key=lambda n: (n.start, n.pitch))
                    for n in notes_sorted:
                        n_info = notes_info[id(n)]
                        nx = n_info["x"]
                        clef = n_info["clef"]
                        step = n_info["step_draw"]
                        _, is_sharp = diatonic_step(n.pitch)
                        ny = note_y(step, clef, treble_mid_y, bass_mid_y, s)

                        sounding = (id(n) in active_ids) or (n.start <= t < n.end)

                        hand_col = HAND_COLORS.get(n.hand, HAND_COLORS[RIGHT])[0]
                        color = mix(hand_col, (255, 255, 255), 0.5) if sounding else hand_col

                        b_len = get_beat_duration(self.song, n.start)
                        nv = note_value(n.duration, b_len)

                        is_hollow = nv in (WHOLE, DOTTED_HALF, HALF)
                        has_stem = (nv != WHOLE)
                        has_dot = nv in (DOTTED_HALF, DOTTED_QUARTER, DOTTED_EIGHTH)
                        flags = 2 if nv == SIXTEENTH else (1 if nv in (EIGHTH, DOTTED_EIGHTH) else 0)

                        # Accidental (sharp ♯)
                        if is_sharp:
                            sharp_col = CURSOR_COLOR if sounding else (30, 30, 40)
                            draw_sharp(staff_surf, nx - nw - round(s * 0.8), ny, s, sharp_col)

                        # Ledger lines
                        ledger_col = line_color
                        lx0, lx1 = round(nx - nw - 3), round(nx + nw + 3)
                        if clef == CLEF_TREBLE:
                            if step <= 28:  # Middle C and below
                                for st in range(28, step - 1, -2):
                                    ly = round(treble_mid_y - (st - TREBLE_MID_STEP) * (s * 0.5))
                                    pygame.draw.line(staff_surf, ledger_col, (lx0, ly), (lx1, ly), 1)
                            elif step >= 40:  # A5 and above
                                for st in range(40, step + 1, 2):
                                    ly = round(treble_mid_y - (st - TREBLE_MID_STEP) * (s * 0.5))
                                    pygame.draw.line(staff_surf, ledger_col, (lx0, ly), (lx1, ly), 1)
                        else:  # Bass clef
                            if step >= 28:  # Middle C and above
                                for st in range(28, step + 1, 2):
                                    ly = round(bass_mid_y - (st - BASS_MID_STEP) * (s * 0.5))
                                    pygame.draw.line(staff_surf, ledger_col, (lx0, ly), (lx1, ly), 1)
                            elif step <= 16:  # E2 and below
                                for st in range(16, step - 1, -2):
                                    ly = round(bass_mid_y - (st - BASS_MID_STEP) * (s * 0.5))
                                    pygame.draw.line(staff_surf, ledger_col, (lx0, ly), (lx1, ly), 1)

                        # Stem and flags
                        stem_up = (step < TREBLE_MID_STEP) if clef == CLEF_TREBLE else (step < BASS_MID_STEP)
                        stem_col = mix(STEM_COLOR, color, 0.5) if sounding else STEM_COLOR

                        if has_stem:
                            if stem_up:
                                sx = round(nx + nw - 1)
                                tip_y = round(ny - stem_len)
                                pygame.draw.line(staff_surf, stem_col, (sx, round(ny)), (sx, tip_y), max(1, int(s * 0.16)))
                                if flags > 0:
                                    draw_flags(staff_surf, sx, tip_y, s, True, stem_col, flags)
                            else:
                                sx = round(nx - nw + 1)
                                tip_y = round(ny + stem_len)
                                pygame.draw.line(staff_surf, stem_col, (sx, round(ny)), (sx, tip_y), max(1, int(s * 0.16)))
                                if flags > 0:
                                    draw_flags(staff_surf, sx, tip_y, s, False, stem_col, flags)

                        # Notehead
                        head_w = round(nw * 1.15) if nv == WHOLE else nw
                        note_rect = pygame.Rect(round(nx - head_w), round(ny - nh), round(2 * head_w), round(2 * nh))

                        if is_hollow:
                            th = max(2, int(s * 0.28))
                            border_col = mix(color, (0, 0, 0), 0.3)
                            inner_col = mix(color, (255, 255, 255), 0.75) if sounding else STAFF_BG
                            pygame.draw.ellipse(staff_surf, border_col, note_rect)
                            inner_rect = note_rect.inflate(-round(2 * th), -round(2 * th))
                            if inner_rect.w > 0 and inner_rect.h > 0:
                                pygame.draw.ellipse(staff_surf, inner_col, inner_rect)
                            if sounding:
                                glow_rect = note_rect.inflate(4, 4)
                                pygame.draw.ellipse(staff_surf, CURSOR_COLOR, glow_rect, 2)
                        else:  # solid
                            pygame.draw.ellipse(staff_surf, color, note_rect)
                            border_col = (25, 25, 35) if sounding else mix(color, (0, 0, 0), 0.4)
                            pygame.draw.ellipse(staff_surf, border_col, note_rect, 1)
                            if sounding:
                                center_rect = note_rect.inflate(-round(nw * 0.7), -round(nh * 0.7))
                                if center_rect.w > 0 and center_rect.h > 0:
                                    pygame.draw.ellipse(staff_surf, (255, 255, 255), center_rect)
                                glow_rect = note_rect.inflate(4, 4)
                                pygame.draw.ellipse(staff_surf, CURSOR_COLOR, glow_rect, 2)

                        # Dot (chấm dôi)
                        if has_dot:
                            dot_x = round(nx + head_w + max(3, int(s * 0.35)))
                            # On a line -> place dot in space above
                            dot_y = round(ny - s * 0.5) if (step % 2 == 0) else round(ny)
                            dot_r = max(2, int(s * 0.22))
                            dot_col = CURSOR_COLOR if sounding else mix(color, (0, 0, 0), 0.2)
                            pygame.draw.circle(staff_surf, dot_col, (dot_x, dot_y), dot_r)

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
