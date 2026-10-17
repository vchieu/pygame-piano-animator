"""
staff.py - Sheet music Grand Staff (Treble and Bass clefs) rendering for Hand-thesia.

Features:
- Grand Staff (Treble clef + Bass clef) with standard diatonic pitch mapping.
- Paged systems (MuseScore style): static staff systems with playback cursor moving
  smoothly from left to right. When cursor reaches the end of the system, it flips
  to the next system and resets to the left.
- Hand-colored noteheads (green for right hand, blue for left hand) with active
  note highlighting/glow when sounding.
- Ledger lines, accidentals (sharps), stems, measure bar lines, clefs, and bracket.
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


# --------------------------------------------------------------------------- #
# Main StaffView
# --------------------------------------------------------------------------- #

class StaffView:
    """
    Renders a Grand Staff sheet music system with dynamic cursor and paging.
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

    def draw(self, surf: pygame.Surface, staff_rect: pygame.Rect, t: float, active_notes: Optional[list] = None):
        """Draw the sheet music Grand Staff, active system notes, and playback cursor."""
        if staff_rect.h <= 0 or staff_rect.w <= 0:
            return

        # 1. Background panel & border
        bg_panel = pygame.Surface(staff_rect.size)
        bg_panel.fill((22, 22, 28))
        surf.blit(bg_panel, staff_rect.topleft)
        pygame.draw.line(surf, PANEL_EDGE, (staff_rect.left, staff_rect.bottom - 1),
                         (staff_rect.right, staff_rect.bottom - 1), 1)

        prev_clip = surf.get_clip()
        surf.set_clip(staff_rect)

        try:
            # 2. Staff geometry
            s = max(5.0, min(9.0, (staff_rect.h - 26) / 14.0))
            center_y = staff_rect.centery
            treble_mid_y = center_y - round(3.0 * s)
            bass_mid_y = center_y + round(3.0 * s)

            line_color = (130, 130, 145)
            left_x = staff_rect.x + 18
            right_x = staff_rect.right - 12

            # 3. Staff lines (5 for Treble, 5 for Bass)
            for i in range(-2, 3):
                ty = round(treble_mid_y + i * s)
                pygame.draw.line(surf, line_color, (left_x, ty), (right_x, ty), 1)
                by = round(bass_mid_y + i * s)
                pygame.draw.line(surf, line_color, (left_x, by), (right_x, by), 1)

            # Left connecting bar and bracket
            treble_top = round(treble_mid_y - 2 * s)
            bass_bot = round(bass_mid_y + 2 * s)
            pygame.draw.line(surf, line_color, (left_x, treble_top), (left_x, bass_bot), 1)
            # Bracket on the far left
            br_x = staff_rect.x + 12
            pygame.draw.line(surf, line_color, (br_x, treble_top), (br_x, bass_bot), 3)
            pygame.draw.line(surf, line_color, (br_x - 3, treble_top - 2), (br_x + 3, treble_top), 2)
            pygame.draw.line(surf, line_color, (br_x - 3, bass_bot + 2), (br_x + 3, bass_bot), 2)

            # 4. Clefs
            clef_color = TEXT
            draw_treble_clef(surf, left_x + 8, treble_mid_y, s, clef_color)
            draw_bass_clef(surf, left_x + 8, bass_mid_y, s, clef_color)

            # 5. Time signature '4/4'
            ts_x = left_x + round(s * 4.6)
            ts_font = ui_font(max(10, int(s * 1.6)), bold=True)
            ts_img = ts_font.render("4", True, TEXT)
            # Treble staff 4/4
            surf.blit(ts_img, ts_img.get_rect(center=(ts_x, round(treble_mid_y - s * 0.9))))
            surf.blit(ts_img, ts_img.get_rect(center=(ts_x, round(treble_mid_y + s * 0.9))))
            # Bass staff 4/4
            surf.blit(ts_img, ts_img.get_rect(center=(ts_x, round(bass_mid_y - s * 0.9))))
            surf.blit(ts_img, ts_img.get_rect(center=(ts_x, round(bass_mid_y + s * 0.9))))

            if not self.song or not self.systems:
                return

            # 6. Active system
            sys = self.system_for_time(t)
            if sys is None:
                return

            usable_w = staff_rect.w - MARGIN_LEFT - MARGIN_RIGHT
            if usable_w <= 10:
                return

            # Measure bar lines
            num_font = ui_font(10)
            for m in sys.measures:
                if m.index > sys.measures[0].index:
                    frac_bar = (m.start_t - sys.start_t) / sys.duration
                    bx = round(staff_rect.x + MARGIN_LEFT + frac_bar * usable_w - 10)
                    pygame.draw.line(surf, BAR_LINE, (bx, treble_top), (bx, bass_bot), 1)
                    # Measure number at the top of the bar line
                    num_surf = num_font.render(str(m.index + 1), True, TEXT_DIM)
                    surf.blit(num_surf, (bx + 3, treble_top - num_surf.get_height() - 1))

            # System ending bar line
            sys_end_x = round(staff_rect.x + MARGIN_LEFT + usable_w)
            is_song_end = (sys.index == len(self.systems) - 1)
            if is_song_end:
                pygame.draw.line(surf, BAR_LINE, (sys_end_x - 4, treble_top), (sys_end_x - 4, bass_bot), 1)
                pygame.draw.line(surf, BAR_LINE, (sys_end_x, treble_top), (sys_end_x, bass_bot), 3)
            else:
                pygame.draw.line(surf, BAR_LINE, (sys_end_x, treble_top), (sys_end_x, bass_bot), 1)

            # 7. Draw Notes
            active_ids = {id(n) for n in active_notes} if active_notes is not None else set()
            nw = round(s * 0.75)
            nh = round(s * 0.52)
            stem_len = round(3.2 * s)

            # Sort notes so lower notes draw first and clashing notes can be spaced
            notes_sorted = sorted(sys.notes, key=lambda n: (n.start, n.pitch))
            for i, n in enumerate(notes_sorted):
                frac_n = (n.start - sys.start_t) / sys.duration
                nx = staff_rect.x + MARGIN_LEFT + frac_n * usable_w
                clef = clef_for_note(n)
                step, is_sharp = diatonic_step(n.pitch)
                ny = note_y(step, clef, treble_mid_y, bass_mid_y, s)

                # Check if note is currently sounding
                sounding = (id(n) in active_ids) or (n.start <= t < n.end)

                # Note color: hand colors
                hand_col = HAND_COLORS.get(n.hand, HAND_COLORS[RIGHT])[0]
                if sounding:
                    color = mix(hand_col, (255, 255, 255), 0.65)
                else:
                    color = hand_col

                # Accidental (sharp ♯)
                if is_sharp:
                    sharp_color = (255, 255, 255) if sounding else TEXT_DIM
                    draw_sharp(surf, nx - nw - round(s * 0.8), ny, s, sharp_color)

                # Ledger lines
                ledger_col = line_color
                lx0, lx1 = round(nx - nw - 3), round(nx + nw + 3)
                if clef == CLEF_TREBLE:
                    if step <= 28:  # Middle C and below
                        for st in range(28, step - 1, -2):
                            ly = round(treble_mid_y - (st - TREBLE_MID_STEP) * (s * 0.5))
                            pygame.draw.line(surf, ledger_col, (lx0, ly), (lx1, ly), 1)
                    elif step >= 40:  # A5 and above
                        for st in range(40, step + 1, 2):
                            ly = round(treble_mid_y - (st - TREBLE_MID_STEP) * (s * 0.5))
                            pygame.draw.line(surf, ledger_col, (lx0, ly), (lx1, ly), 1)
                else:  # Bass clef
                    if step >= 28:  # Middle C and above
                        for st in range(28, step + 1, 2):
                            ly = round(bass_mid_y - (st - BASS_MID_STEP) * (s * 0.5))
                            pygame.draw.line(surf, ledger_col, (lx0, ly), (lx1, ly), 1)
                    elif step <= 16:  # E2 and below
                        for st in range(16, step - 1, -2):
                            ly = round(bass_mid_y - (st - BASS_MID_STEP) * (s * 0.5))
                            pygame.draw.line(surf, ledger_col, (lx0, ly), (lx1, ly), 1)

                # Stem: up if below middle line, down if on or above middle line
                stem_up = (step < TREBLE_MID_STEP) if clef == CLEF_TREBLE else (step < BASS_MID_STEP)
                stem_col = (240, 240, 240) if sounding else (160, 160, 175)
                if stem_up:
                    sx = round(nx + nw - 1)
                    pygame.draw.line(surf, stem_col, (sx, round(ny)), (sx, round(ny - stem_len)), 1)
                else:
                    sx = round(nx - nw + 1)
                    pygame.draw.line(surf, stem_col, (sx, round(ny)), (sx, round(ny + stem_len)), 1)

                # Notehead (ellipse)
                note_rect = pygame.Rect(round(nx - nw), round(ny - nh), round(2 * nw), round(2 * nh))
                pygame.draw.ellipse(surf, color, note_rect)
                border_col = (255, 255, 255) if sounding else mix(color, (0, 0, 0), 0.4)
                pygame.draw.ellipse(surf, border_col, note_rect, 1)

                # Sounding glow ring
                if sounding:
                    glow_rect = note_rect.inflate(4, 4)
                    pygame.draw.ellipse(surf, (255, 255, 255), glow_rect, 1)

            # 8. Playback cursor
            cx = round(self.cursor_x(t, staff_rect))
            cy_top = round(treble_top - s * 0.8)
            cy_bot = round(bass_bot + s * 0.8)
            ch = cy_bot - cy_top

            # Translucent cursor glow band
            glow_surf = pygame.Surface((8, ch), pygame.SRCALPHA)
            glow_surf.fill((*ACCENT, 65))
            surf.blit(glow_surf, (cx - 4, cy_top))

            # Main cursor line
            pygame.draw.line(surf, ACCENT, (cx, cy_top), (cx, cy_bot), 2)

            # Cursor triangles at top and bottom
            tri_top = [(cx - 4, cy_top - 3), (cx + 4, cy_top - 3), (cx, cy_top + 3)]
            tri_bot = [(cx - 4, cy_bot + 3), (cx + 4, cy_bot + 3), (cx, cy_bot - 3)]
            pygame.draw.polygon(surf, ACCENT, tri_top)
            pygame.draw.polygon(surf, ACCENT, tri_bot)

        finally:
            surf.set_clip(prev_clip)
