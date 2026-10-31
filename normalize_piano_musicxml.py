#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
normalize_piano_musicxml.py

Chuẩn hóa nhạc piano về dạng "sạch": 2 tay, mỗi tay đúng 1 voice, ô nhịp đúng độ dài.
Một file duy nhất, nhận cả MIDI lẫn MusicXML.

    input         output        làm gì
    ------------  ------------  ----------------------------------------------------------
    .mid / .midi  .mid          MIDI chơi tay -> MIDI sạch trên lưới chính xác (engine MIDI)
    .mid / .midi  .xml          như trên, rồi xuất MusicXML 2 part (P1 phải, P2 trái), voice 1
    .xml / .musicxml  .xml      MusicXML nhiều voice/staff -> 2 part, mỗi part voice 1

Engine MIDI (cần numpy): tìm ô lưới (cell) và tempo từ chỗ người chơi nhấn, bám theo tempo
trôi, đặt mỗi hợp âm lên lưới, tìm ô nhịp và nốt lấy đà (pickup), rồi mỗi tay chỉ còn một chuỗi
hợp âm không chồng nhau. Bước `tidy` (mặc định bật) còn đảm bảo độ dài mọi nốt là giá trị nốt
ghi được và nốt dài không kết thúc giữa hai ô lưới, để phần mềm chấm nhạc không tự tách voice 2.

Mặc định không ghi nốt ngắn hơn 16th: khi ô lưới là 16th hoặc ngắn hơn, mọi hợp âm và độ dài nốt
nằm trên Ô LƯỚI NGUYÊN, nên MIDI chơi tay không còn nốt 32nd vụn / hợp âm bị tách làm hai. Khi ô lưới
là 8th hoặc dài hơn thì nửa ô vẫn còn là 16th, nên nốt 16th (ornament) được giữ. `--fine` cho phép
nửa ô trong mọi trường hợp (hành vi cũ).
Nốt cực ngắn (rác của bộ chuyển audio -> MIDI) bỏ bằng `--min-dur`.
Đơn giản hóa: `--lh-max N` / `--rh-max N` (tối đa N nốt mỗi hợp âm, tay trái giữ bass, tay phải giữ
giai điệu), `--drop-ornaments` (bỏ nốt láy), `--simplify` (= 3 / 1 / bỏ nốt láy).

Đường MusicXML chỉ dùng thư viện chuẩn (không cần numpy).

Cách dùng:
    python normalize_piano_musicxml.py bai.mid bai_clean.mid
    python normalize_piano_musicxml.py bai.mid bai_clean.xml
    python normalize_piano_musicxml.py ban_goc.xml ban_chuan.xml --hand staff

Nếu bỏ output: <input>_clean.mid hoặc <input>_clean.xml.

Dùng như thư viện:
    import normalize_piano_musicxml as N
    clean = N.normalize(notes, controls=..., beats_per_bar=4)   # notes có .hand "R"/"L"
    N.write_midi("out.mid", clean)
    N.write_musicxml("out.xml", clean)
"""

from __future__ import annotations

import argparse
import copy
import math
import os
import re
import struct
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, replace
from fractions import Fraction
from typing import List, Optional, Sequence, Tuple

try:                                  # chỉ engine MIDI cần numpy; đường MusicXML thì không
    import numpy as np
except ImportError:                   # pragma: no cover
    np = None


@dataclass
class Note:
    """Một nốt: giây trên đồng hồ của file. `hand` là "R", "L" hoặc None (chưa phân tay)."""
    pitch: int
    start: float
    end: float
    velocity: int = 90
    track: int = 0
    hand: Optional[str] = None
    lyric: Optional[tuple] = None    # ((number, syllabic, text), ...) từ MusicXML; ghi lại thành <lyric>




# --------------------------------------------------------------------------- #
# Engine MIDI (cần numpy)
# --------------------------------------------------------------------------- #

LEFT, RIGHT = "L", "R"
MIDDLE_C = 60

CHORD_TOL = 0.045        # s: onsets this close to a chord's first note belong to it
MIN_CELL = 0.14          # s: the finest cell worth looking for (a hand can't play cleaner)
MAX_CELL = 0.80
SEARCH_T = 40.0          # s of music the coarse search for the cell looks at
CELL_KEEP = 0.90         # the largest period reaching this share of the best comb score is the cell
PLL_GAIN = 0.15          # how fast the grid follows the player (share of each error taken up)
FINE_TOL = 0.28          # of a cell: further from the coarse grid than this, a half cell may fit better
FINE_RATIO = 0.5         # ... when its error is under this share of the coarse error
MAX_BPM = 130.0          # the beat (quarter) is the multiple of the cell that keeps the tempo under this
REST_MIN_BEAT = 0.75     # a gap shorter than this share of a beat between a hand's chords is not written as a rest
MIN_DUR = 0.0            # s: notes shorter than this are dropped before snapping (0 = keep all)
ORN_LEN = 1.5            # units (half cell): a chord whose notes are all shorter than this may be an ornament
ORN_GAP = 2              # units: ... if a longer chord of the same hand starts this close, a step (1-2 semitones) away
PPQ = 480
# Written note values, in units (half a cell): 32nd, 16th, dotted 16th, 8th, dotted 8th, quarter,
# dotted quarter, half, dotted half, whole. A notation program shows anything else (10, 13, ...)
# as tied pieces, and it is there that it starts a second voice.
STD_LEN = (1, 2, 3, 4, 6, 8, 12, 16, 24, 32)


# --------------------------------------------------------------------------- #
# Result
# --------------------------------------------------------------------------- #

@dataclass
class Clean:
    """A cleaned score: notes on an exact clock, and what a writer or a staff view needs."""
    notes: list                          # note objects (hands kept), start/end in seconds on the new clock
    bpm: float                           # the tempo of that clock (a quarter note)
    beats_per_bar: int
    unit: float                          # seconds per unit (a half cell: a 32nd at the default tempo)
    units_per_beat: int
    bar_times: List[float]
    beat_times: List[float]
    pickup_end: float = 0.0
    report: List[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    controls: list = field(default_factory=list)     # [(time, controller, value)] on the new clock
    # kept for mapping other times (pedal) from the old clock to the new
    _origin: float = 0.0
    _unit_old: float = 0.0
    _u0: int = 0
    _warp_t: Optional[np.ndarray] = None
    _warp_d: Optional[np.ndarray] = None

    def map_time(self, t: float) -> float:
        """A time on the player's clock -> the same moment on the cleaned clock (seconds)."""
        d = float(np.interp(t, self._warp_t, self._warp_d)) if self._warp_t is not None and len(self._warp_t) else 0.0
        return ((t - self._origin - d) / self._unit_old - self._u0) * self.unit


# --------------------------------------------------------------------------- #
# 1. The pulse
# --------------------------------------------------------------------------- #

def _chords(notes: Sequence, tol: float = CHORD_TOL) -> List[list]:
    """Notes in start order, grouped: a note joins the group when it starts within tol of its first."""
    out: List[list] = []
    for n in sorted(notes, key=lambda n: (n.start, n.pitch)):
        if out and n.start - out[-1][0].start <= tol:
            out[-1].append(n)
        else:
            out.append([n])
    return out


def _comb(ts: np.ndarray, periods: np.ndarray) -> np.ndarray:
    """z(P) = mean of exp(2 pi i t / P) for every period (chunked: a long piece has thousands of onsets)."""
    out = np.empty(len(periods), dtype=complex)
    step = max(1, 2_000_000 // max(1, len(ts)))
    for i in range(0, len(periods), step):
        p = periods[i:i + step]
        out[i:i + step] = np.exp(2j * np.pi * ts[None, :] / p[:, None]).mean(axis=1)
    return out


def find_cell(times: Sequence[float]) -> Tuple[float, float, float]:
    """
    (cell, origin, strength): the grid the onsets sit on.

    `cell` is the largest period whose onsets bunch up nearly as well as the best one's
    (so a piece of quarter notes gets the quarter, a piece with 16ths gets the 16th, not
    its half), `origin` a time on the grid, `strength` the comb score of it (0..1).
    """
    ts = np.asarray(sorted(times), dtype=float)
    if len(ts) < 8:
        raise ValueError("too few notes to find a pulse")
    # Coarse search on the first SEARCH_T seconds. A period P is only told from its neighbours
    # when the phase over the window moves by well under a cell: step <= 0.1 P^2 / window
    # (so the steps grow with P; a fixed step would lose the peak on a long piece).
    window = ts[ts <= ts[0] + SEARCH_T]
    if len(window) < 8:
        window = ts[:64]
    span = max(1.0, float(window[-1] - window[0]))
    periods = []
    p = MIN_CELL
    while p < MAX_CELL:
        periods.append(p)
        p += 0.1 * p * p / span
    periods = np.asarray(periods)
    r = np.abs(_comb(window, periods))
    ok = np.nonzero(r >= CELL_KEEP * r.max())[0]
    j = int(ok[-1])
    while j > 0 and r[j - 1] > r[j]:                     # up to the top of that peak
        j -= 1
    # Then the whole piece, finely: +-1.2 % in steps of 1e-5 (a cell moves 0.09 over 1200 s at 0.14 s)
    fine = periods[j] * (1.0 + np.linspace(-0.012, 0.012, 2401))
    z = _comb(ts, fine)
    k = int(np.argmax(np.abs(z)))
    cell = float(fine[k])
    origin = float((np.angle(z[k]) / (2 * np.pi) * cell) % cell)
    return cell, origin, float(abs(z[k]))


# --------------------------------------------------------------------------- #
# 2. Snapping, following the player
# --------------------------------------------------------------------------- #

def _snap(groups: List[list], cell: float, origin: float, fine: bool = False):
    """
    Put every group of notes on the grid, in units of half a cell.

    fine=False: every group goes to the nearest WHOLE cell (even units), so a hand never gets a
    32nd crumb or a chord split over two units. fine=True: a group far from the coarse grid may
    use the half cell when that fits clearly better (ornaments). `normalize` passes fine=True by
    default when the cell is an 8th or longer: half a cell is then still a 16th or longer.

    Returns (units, drift, stats): the unit each group lands on, the grid's offset from the
    origin at that group (seconds - how far the player had drifted) and counters.
    """
    half = cell / 2.0
    # The grid was fitted to the whole piece; the player may have started off it (tempo
    # drift moves the start and the end opposite ways). Begin where the first chords sit.
    head = np.array([sum(n.start for n in g) / len(g) for g in groups[:24]])
    z0 = np.exp(2j * np.pi * (head - origin) / cell).mean()
    d = float(np.angle(z0) / (2 * np.pi) * cell) if abs(z0) > 0.3 else 0.0
    prev = -10 ** 9
    units, drift = [], []
    stats = {"coarse": 0, "fine": 0, "outliers": 0, "on_grid": 0}
    for g in groups:
        t = sum(n.start for n in g) / len(g)
        x = t - origin - d
        k = math.floor(x / cell + 0.5)
        e = x - k * cell
        if abs(e) <= FINE_TOL * cell:
            u = 2 * k
            d += PLL_GAIN * e
            stats["coarse"] += 1
            stats["on_grid"] += 1
        elif fine:
            uf = math.floor(x / half + 0.5)
            ef = x - uf * half
            if uf % 2 and abs(ef) < FINE_RATIO * abs(e):
                u = uf
                stats["fine"] += 1
            else:
                u = 2 * k
                stats["outliers"] += 1
        else:
            u = 2 * k
            stats["outliers"] += 1
        u = max(u, prev)
        prev = u
        units.append(u)
        drift.append(d)
    return units, drift, stats


# --------------------------------------------------------------------------- #
# 3. Tempo, bars
# --------------------------------------------------------------------------- #

def pick_beat(cell: float, max_bpm: float = MAX_BPM) -> Tuple[int, float]:
    """(cells per beat, bpm): the smallest power of two of cells that keeps the quarter note under max_bpm."""
    c = 1
    while 60.0 / (cell * c) > max_bpm and c < 16:
        c *= 2
    raw = 60.0 / (cell * c)
    nearest = round(raw)
    bpm = float(nearest) if nearest and abs(raw - nearest) / raw < 0.0025 else round(raw, 2)
    return c, bpm


def _downbeat(items: List[tuple], bar_u: int, upb: int) -> int:
    """
    Where the bar lines fall: the unit (mod bar_u, a whole cell) a bar starts on.

    The grid only fixes the cells, not which of them opens a bar, so every cell of a bar is
    a candidate. `items` = (unit, hand, pitch, length in units) of every note. Each note
    votes for the cell it sits on: left-hand notes count more, the lowest quarter of them
    (the bass lands on 1) and long ones more still. The first note's own cell wins unless
    another one beats it by a quarter - a piece starting on a pickup is the rarer case.
    """
    lh = sorted(p for _, h, p, _ in items if h == LEFT)
    low = lh[len(lh) // 4] if lh else -1
    score = [0.0] * bar_u
    for u, hand, pitch, length in items:
        if u % 2:
            continue
        w = 1.0 + 0.5 * min(length / upb, 4.0)
        if hand == LEFT:
            w += 1.5 + (2.0 if pitch <= low else 0.0)
        score[u % bar_u] += w
    first = min(u for u, _, _, _ in items) % bar_u
    first -= first % 2
    best = max(range(0, bar_u, 2), key=lambda r: score[r])
    return best if score[best] > 1.25 * score[first] + 1e-9 else first


# --------------------------------------------------------------------------- #
# 4. One voice per hand
# --------------------------------------------------------------------------- #

def _tidy_len(u: int, e: int) -> int:
    """
    The end of a note that starts at unit u and was meant to end at e, made easy to write.

    A long note must not end between two cells (an odd unit): a notation program rounds its
    end up to the next cell and the little notes that follow (an ornament) then start while it
    still sounds - which it prints as a second voice. So it ends one unit earlier, and the
    length is brought down to a written note value; the difference is a short rest.
    """
    n = e - u
    if n >= 4 and e % 2:
        n -= 1
    if n > STD_LEN[-1]:
        return u + n
    return u + max(v for v in STD_LEN if v <= n)


def _voice(by_u: dict, voices: str, chord_end: str, rest_min: int, counters: dict,
           tidy: bool = True, cells_only: bool = False) -> List[tuple]:
    """
    [(start unit, end unit, [(note, end_raw)])] for one hand.

    "single": a chord has one length and ends where the hand's next chord starts, unless a
    gap of rest_min units or more is left (then it is a rest). "keep": every note keeps its own
    length (so overlaps stay), gaps shorter than rest_min are still filled.
    cells_only: a written length is always a whole number of cells (never a 32nd crumb).
    """
    us = sorted(by_u)
    out = []
    pick = {"max": max, "min": min, "median": lambda v: sorted(v)[len(v) // 2]}[chord_end]
    for i, u in enumerate(us):
        group = []
        seen = set()
        for n, e in by_u[u]:                                  # one note per key
            if n.pitch not in seen:
                seen.add(n.pitch)
                group.append((n, e))
        nxt = us[i + 1] if i + 1 < len(us) else None

        def coarse(e_float: float) -> int:
            """A written length: whole cells when it is long enough to be one, else the nearest unit."""
            if cells_only or e_float - u >= 1.5:
                return max(u + 2, 2 * math.floor(e_float / 2.0 + 0.5))
            return max(u + 1, math.floor(e_float + 0.5))

        ends = [coarse(e) for _, e in group]
        if voices == "single":
            e = pick(ends)
            if nxt is not None:
                if e > nxt:
                    counters["cut"] += sum(1 for x in ends if x > nxt)
                    e = nxt
                elif nxt - e < rest_min:
                    e = nxt
                elif nxt - e > 0:
                    counters["rests"] += 1
            if tidy:
                t = _tidy_len(u, e)
                if t != e:
                    counters["tidied"] += 1
                    e = t
            ends = [e] * len(group)
        else:
            if nxt is not None:
                ends = [nxt if 0 < nxt - x < rest_min else x for x in ends]
        out.append((u, ends, [n for n, _ in group]))
    return out


# --------------------------------------------------------------------------- #
# Simplifying: fewer notes per chord, no ornaments
# --------------------------------------------------------------------------- #

def _limit_chord(chord: list, k: int, bass_first: bool) -> list:
    """
    At most k notes of a chord [(note, end_raw)], in their original order.
    Left hand (bass_first): the lowest note first (the bass), then the highest, then the longest held.
    Right hand: the highest note first (the melody), then the lowest, then the longest held.
    """
    if k <= 0 or len(chord) <= k:
        return chord
    lo = min(chord, key=lambda c: c[0].pitch)
    hi = max(chord, key=lambda c: c[0].pitch)
    first, second = (lo, hi) if bass_first else (hi, lo)
    rest = sorted((c for c in chord if c is not first and c is not second),
                  key=lambda c: (-(c[1]), -c[0].velocity))
    keep = ([first, second] + rest)[:k]
    ids = {id(c) for c in keep}
    return [c for c in chord if id(c) in ids]


def _simplify(per_hand: dict, lh_max: int = 0, rh_max: int = 0,
              drop_ornaments: bool = False) -> dict:
    """
    Thin out `per_hand` ({hand: {unit: [(note, end_raw_unit)]}}) in place; returns counters.

    drop_ornaments: an ornament (grace note, mordent, trill note) is a very short note (< ORN_LEN
        units) that sits a step (1-2 semitones) from a longer note. It is dropped when it shares a
        unit with a longer note (fine=False merges the ornament onto the beat) and when it sits in
        a short chord within ORN_GAP units of a longer chord of the same hand (fine=True). A
        repeated key (distance 0) is never an ornament.
    lh_max / rh_max: at most that many notes in a left / right hand chord (0 = no limit).
        Right hand 1 = the melody note only; left hand 1 = the bass only.
    """
    out = {"ornaments": 0, "thinned": 0}
    for hand, k in ((RIGHT, rh_max), (LEFT, lh_max)):
        by_u = per_hand[hand]
        if drop_ornaments:
            # Same unit: the short note a step from a longer one on the same beat is the ornament.
            for u in list(by_u):
                chord = by_u[u]
                long_notes = [c for c in chord if c[1] - u >= ORN_LEN]
                if not long_notes:
                    continue
                kept = []
                for c in chord:
                    if c[1] - u < ORN_LEN and any(1 <= abs(c[0].pitch - b[0].pitch) <= 2
                                                  for b in long_notes):
                        out["ornaments"] += 1
                    else:
                        kept.append(c)
                by_u[u] = kept
            # Neighbouring units: a short chord a step from a longer chord of the same hand.
            us = sorted(by_u)
            drop = []
            for i, u in enumerate(us):
                chord = by_u[u]
                if not chord or not all(e - u < ORN_LEN for _, e in chord):
                    continue
                for j in (i - 1, i + 1):
                    if not 0 <= j < len(us) or abs(us[j] - u) > ORN_GAP:
                        continue
                    other = by_u[us[j]]
                    if all(e - us[j] < ORN_LEN for _, e in other):
                        continue                              # neighbour is itself short
                    d = min(abs(a[0].pitch - b[0].pitch) for a in chord for b in other)
                    if 1 <= d <= 2:
                        drop.append(u)
                        break
            for u in drop:
                out["ornaments"] += len(by_u.pop(u))
        if k > 0:
            for u in list(by_u):
                kept = _limit_chord(by_u[u], k, bass_first=(hand == LEFT))
                out["thinned"] += len(by_u[u]) - len(kept)
                by_u[u] = kept
    return out


# --------------------------------------------------------------------------- #
# The whole thing
# --------------------------------------------------------------------------- #

def normalize(notes: Sequence, controls: Optional[Sequence[tuple]] = None, beats_per_bar: int = 4,
              max_bpm: float = MAX_BPM, voices: str = "single", chord_end: str = "median",
              rest_min: Optional[int] = None, cells_per_beat: Optional[int] = None,
              downbeat: str = "auto", tidy: bool = True, fine: bool = False,
              min_dur: float = MIN_DUR, lh_max: int = 0, rh_max: int = 0,
              drop_ornaments: bool = False, bar_anchor: Optional[float] = None) -> Clean:
    """
    Clean `notes` (objects with .pitch .start .end .velocity .hand, hands already decided).

    beats_per_bar:   the time signature's numerator over quarter notes (4 for 4/4).
    max_bpm:         the quarter note is the multiple of the cell that keeps the tempo below this.
    cells_per_beat:  force it (2: eighth-note cells, 4: sixteenth-note cells ...).
    voices:          "single" (one voice a hand, legato) or "keep" (overlaps stay).
    chord_end:       how long a chord is when its notes were held for different times.
    rest_min:        gaps (in units, half a cell) shorter than this are filled; default 3/4 of a beat.
    downbeat:        "auto", or "first" to make the first note beat 1.
    tidy:            (voices="single") every note gets a written note value and no long note ends
                     between two cells, so a notation program prints one voice per staff.
    fine:            False (default): never write a note shorter than a 16th. Whole cells are used
                     when the cell is a 16th or shorter; when it is an 8th or longer, a half cell
                     (still a 16th or longer) is allowed too. True: ornaments may always use the
                     half cell (the old behaviour).
    min_dur:         notes shorter than this many seconds are dropped first (0: keep all).
    lh_max, rh_max:  at most this many notes in a left / right hand chord (0: no limit). Left hand keeps
                     the bass first; right hand keeps the highest (melody) note first.
    drop_ornaments:  drop grace / mordent / trill notes (very short chords a step from a longer one).
    bar_anchor:      a time (seconds on the input clock) known to be a bar line (e.g. from a MusicXML
                     source); with downbeat="auto" it fixes where bars fall instead of guessing.
    controls:        [(time, controller, value)], mapped onto the new clock.
    """
    if np is None:
        raise RuntimeError("engine MIDI cần numpy: pip install numpy")
    notes = [n for n in notes]
    if not notes:
        raise ValueError("no notes")
    report: List[str] = []
    n_raw = len(notes)
    if min_dur > 0:
        kept = [n for n in notes if n.end - n.start >= min_dur]
        if len(kept) >= 8:
            notes = kept
    dropped_short = n_raw - len(notes)
    groups = _chords(notes)
    gtimes = [sum(n.start for n in g) / len(g) for g in groups]
    cell, origin, strength = find_cell(gtimes)
    cpb, bpm = pick_beat(cell, max_bpm) if not cells_per_beat else \
        (cells_per_beat, round(60.0 / (cell * cells_per_beat), 2))
    # A half cell may only be written when it is still at least a 16th: with a cell of an 8th or
    # longer (cpb < 4) half a cell is a 16th or longer, so the fine positions are safe by default.
    fine_snap = fine or cpb < 4
    units, drift, st = _snap(groups, cell, origin, fine=fine_snap)
    fit = st["on_grid"] / max(1, len(groups))
    half = cell / 2.0

    upb = 2 * cpb                                           # units per beat
    if PPQ % upb:
        raise ValueError("cells_per_beat %d does not divide the resolution" % cpb)
    unit_new = 60.0 / (bpm * upb)
    if rest_min is None:
        rest_min = max(2, round(REST_MIN_BEAT * upb))

    # --- hands, with each note's unit and the end it asked for
    wt = np.array([sum(n.start for n in g) / len(g) for g in groups])
    wd = np.array(drift)
    per_hand = {RIGHT: {}, LEFT: {}}
    for g, u, d in zip(groups, units, drift):
        for n in g:
            hand = n.hand or (LEFT if n.pitch < MIDDLE_C else RIGHT)
            e_float = (n.end - origin - d) / half
            per_hand[hand].setdefault(u, []).append((n, e_float))
    simp = _simplify(per_hand, lh_max, rh_max, drop_ornaments)
    items = [(u, hand, n.pitch, max(1.0, e - u))
             for hand in (RIGHT, LEFT) for u, lst in per_hand[hand].items() for n, e in lst]
    if not items:
        raise ValueError("no notes left after simplifying")

    bar_u = beats_per_bar * upb
    first_u = min(u for u, _, _, _ in items)
    if downbeat == "first":
        phase = first_u % bar_u
    elif bar_anchor is not None:
        phase = (2 * int(round((bar_anchor - origin) / cell))) % bar_u
    else:
        phase = _downbeat(items, bar_u, upb)
    # bar lines fall on the units congruent to `phase` (mod bar_u): u0 is the last one at or
    # before the first note, and becomes time 0
    u0 = first_u - ((first_u - phase) % bar_u)
    lead = first_u - u0                                      # units of silence in the first bar

    counters = {"cut": 0, "rests": 0, "merged": 0, "tidied": 0}
    out_notes = []
    for hand in (RIGHT, LEFT):
        before = sum(len(v) for v in per_hand[hand].values())
        for u, ends, ns in _voice(per_hand[hand], voices, chord_end, rest_min, counters, tidy,
                                  cells_only=not fine and cpb >= 4):
            for n, e in zip(ns, ends):
                kw = dict(start=(u - u0) * unit_new, end=(e - u0) * unit_new, hand=hand,
                          track=0 if hand == RIGHT else 1)
                if hasattr(n, "tuplet_num"):
                    kw.update(tuplet_num=None, tuplet_type=None, printed=None)
                out_notes.append(replace(n, **kw))
        counters["merged"] += before - sum(1 for o in out_notes if o.hand == hand)
    out_notes.sort(key=lambda n: (n.start, n.pitch))

    # --- the clock: bars and beats from the first bar line to past the last note
    end_u = max(round(n.end / unit_new) for n in out_notes)
    nbars = max(1, math.ceil(end_u / bar_u))
    bar_times = [i * bar_u * unit_new for i in range(nbars + 1)]
    beat_times = [i * upb * unit_new for i in range(nbars * beats_per_bar + 1)]
    pickup_end = bar_times[1] if lead >= upb else 0.0

    clean = Clean(out_notes, bpm, beats_per_bar, unit_new, upb, bar_times, beat_times, pickup_end,
                  report, {}, [], origin, half, u0, wt, wd)
    if controls:
        sw = [(clean.map_time(t), c, v) for t, c, v in controls]
        clean.controls = sorted(sw, key=lambda x: (x[0], x[1]))

    # --- what was done
    rh = sum(1 for n in out_notes if n.hand == RIGHT)
    clean.stats = dict(cell=cell, strength=strength, fit=fit, bpm=bpm, notes_in=n_raw,
                       notes_out=len(out_notes), right=rh, left=len(out_notes) - rh,
                       fine=st["fine"], outliers=st["outliers"], cut=counters["cut"],
                       rests=counters["rests"], merged=counters["merged"], tidied=counters["tidied"],
                       dropped_short=dropped_short, ornaments=simp["ornaments"], thinned=simp["thinned"],
                       pickup_units=lead,
                       phase=phase)
    report.append("grid: cell %.4f s, %d cells to the beat -> %.2f bpm, %d/4; %.0f%% of the chords sit "
                  "within a quarter cell of it" % (cell, cpb, bpm, beats_per_bar, 100 * fit))
    if dropped_short:
        report.append("dropped %d note(s) shorter than %.3f s" % (dropped_short, min_dur))
    if simp["ornaments"]:
        report.append("dropped %d ornament note(s) (short, a step from a longer chord)" % simp["ornaments"])
    if simp["thinned"]:
        report.append("thinned chords: removed %d note(s) (left hand max %s, right hand max %s)"
                      % (simp["thinned"], lh_max or "-", rh_max or "-"))
    if st["fine"]:
        report.append("%d chord(s) put on the half-cell grid (ornaments)" % st["fine"])
    if st["outliers"]:
        report.append("%d chord(s) far from every cell, put on the nearest coarse one" % st["outliers"])
    if counters["cut"]:
        report.append("cut %d held note(s) where their hand's next chord begins" % counters["cut"])
    if counters["tidied"]:
        report.append("shortened %d note(s) to a written note value / a cell boundary (a short rest follows)"
                      % counters["tidied"])
    if counters["merged"]:
        report.append("merged %d note(s) that landed on the same key and unit" % counters["merged"])
    if lead:
        report.append("the first note is %d unit(s) into its bar%s" % (lead, " (a pickup)" if pickup_end else ""))
    if fit < 0.8:
        report.append("WARNING: only %.0f%% of the chords fit the grid - this is freer than a fixed pulse; "
                      "check the result" % (100 * fit))
    return clean


# --------------------------------------------------------------------------- #
# Output: a MIDI file with two staves
# --------------------------------------------------------------------------- #

def _vlq(n: int) -> bytes:
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append(0x80 | (n & 0x7F))
        n >>= 7
    return bytes(reversed(out))


def _track(events: List[tuple]) -> bytes:
    """events: (tick, order, bytes) -> an MTrk chunk. Order breaks ties: offs, controllers, ons."""
    body = bytearray()
    last = 0
    for tick, _, data in sorted(events, key=lambda e: (e[0], e[1])):
        body += _vlq(tick - last) + data
        last = tick
    body += _vlq(0) + b"\xff\x2f\x00"
    return b"MTrk" + struct.pack(">I", len(body)) + bytes(body)


def _meta(kind: int, data: bytes) -> bytes:
    return bytes([0xFF, kind]) + _vlq(len(data)) + data


def revoice(clean: Clean, voices: str = "single", chord_end: str = "median", tidy: bool = True,
            fine: bool = False):
    """
    Voice `clean.notes` again, per hand as they now carry it. Needed after the notes were edited
    on the clean clock (a note moved to the other hand may overlap that hand's held notes, which
    a notation program prints as a second voice). Returns (Clean, [(old note, new note)]); a note
    that lands on a key its hand already plays at that unit is merged away (no pair).

    fine: same meaning as in `normalize`. False (default) keeps every written length a whole
    number of cells (never a 32nd crumb); True allows half-cell lengths (the old behaviour).
    """
    unit = clean.unit
    rest_min = max(2, round(REST_MIN_BEAT * clean.units_per_beat))
    per = {RIGHT: {}, LEFT: {}}
    for n in clean.notes:
        hand = n.hand or (LEFT if n.pitch < MIDDLE_C else RIGHT)
        per[hand].setdefault(int(round(n.start / unit)), []).append((n, n.end / unit))
    counters = {"cut": 0, "rests": 0, "merged": 0, "tidied": 0}
    pairs = []
    for hand in (RIGHT, LEFT):
        for u, ends, ns in _voice(per[hand], voices, chord_end, rest_min, counters, tidy,
                                  cells_only=not fine and clean.units_per_beat >= 8):   # giống normalize: ô lưới 8th thì nửa ô (16th) vẫn hợp lệ
            for n, e in zip(ns, ends):
                pairs.append((n, replace(n, start=u * unit, end=e * unit, hand=hand,
                                         track=0 if hand == RIGHT else 1)))
    pairs.sort(key=lambda p: (p[1].start, p[1].pitch))
    notes = [new for _, new in pairs]
    rh = sum(1 for n in notes if n.hand == RIGHT)
    stats = dict(clean.stats, notes_out=len(notes), right=rh, left=len(notes) - rh)
    return replace(clean, notes=notes, stats=stats), pairs


def write_midi(path: str, clean: Clean, key_fifths: int = 0, program: int = 0,
               flat_velocity: Optional[int] = None, fingers: Optional[dict] = None) -> int:
    """
    Write `clean` as a format-1 Standard MIDI File: a conductor track (tempo, time signature,
    key), "Right hand" on channel 0 and "Left hand" on channel 1, one voice each. The sustain
    (and other) controllers, if any, go to both hands' channels. Returns the number of notes.

    `fingers` (optional) maps a note's id() to its fingering label ("R3", "L1", "Rg"); each is
    written as a text event right before that note, in the form midi_loader.read_markers reads
    (the fingering editor's markers), so an exported score keeps the fingering.
    """
    if PPQ % clean.units_per_beat:
        raise ValueError("units_per_beat %d does not divide PPQ %d" % (clean.units_per_beat, PPQ))
    tpu = PPQ // clean.units_per_beat
    tick = lambda t: max(0, int(round(t / clean.unit)) * tpu)
    tempo = int(round(60_000_000 / clean.bpm))
    conductor = [(0, 0, _meta(0x03, b"Conductor")),
                 (0, 1, _meta(0x51, tempo.to_bytes(3, "big"))),
                 (0, 2, _meta(0x58, bytes([clean.beats_per_bar, 2, 24, 8]))),
                 (0, 3, _meta(0x59, bytes([key_fifths & 0xFF, 0])))]
    tracks = [_track(conductor)]
    total = 0
    for hand, ch, name in ((RIGHT, 0, b"Right hand"), (LEFT, 1, b"Left hand")):
        ev = [(0, 0, _meta(0x03, name)), (0, 1, bytes([0xC0 | ch, program]))]
        k = 0                       # each note's marker and its note-on share a tie-break pair,
        for n in clean.notes:       # so a chord's markers stay with their own notes
            if n.hand != hand:
                continue
            vel = flat_velocity or max(1, min(127, int(n.velocity)))
            a, b = tick(n.start), max(tick(n.start) + 1, tick(n.end))
            ev.append((b, 0, bytes([0x80 | ch, n.pitch, 0])))
            label = (fingers or {}).get(id(n))
            if label:
                ev.append((a, 2 * k + 1, _meta(0x01, str(label).encode("ascii"))))
            ev.append((a, 2 * k + 2, bytes([0x90 | ch, n.pitch, vel])))
            k += 1
            total += 1
        for t, c, v in clean.controls:
            ev.append((tick(t), 1, bytes([0xB0 | ch, c, v])))
        tracks.append(_track(ev))
    data = b"MThd" + struct.pack(">IHHH", 6, 1, len(tracks), PPQ) + b"".join(tracks)
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)
    return total


# --------------------------------------------------------------------------- #
# For the app
# --------------------------------------------------------------------------- #

def clean_song(song, **options):
    """
    A MidiSong made of `song`'s notes, cleaned: on the new clock, two tracks (the right hand,
    the left), the bars and beats of that clock, the pedal moved with the notes. The hands are
    those the song's notes carry. `options` go to `normalize`.
    """
    from midi_loader import MidiSong, TrackInfo
    options.setdefault("controls", getattr(song, "raw_controls", None))
    clean = normalize(song.notes, **options)
    rh = sum(1 for n in clean.notes if n.hand == RIGHT)
    tracks = [TrackInfo(0, "Right hand", 0, rh, RIGHT), TrackInfo(1, "Left hand", 0, len(clean.notes) - rh, LEFT)]
    duration = max((n.end for n in clean.notes), default=0.0)
    out = MidiSong(clean.notes, tracks, duration, clean.bar_times, clean.beat_times, song.path, clean.controls,
                   key_fifths=getattr(song, "key_fifths", 0))
    out.quarter_times = [(0.0, 60.0 / clean.bpm)]
    out.pickup_end = clean.pickup_end
    out.cleanup = list(getattr(song, "cleanup", [])) + clean.report
    # So the app knows this song sits on the cleaned clock, and an editor's
    # export can write the two-staff MIDI of exactly what it cleaned.
    out.normalized = True
    out.clean = clean
    return out




# --------------------------------------------------------------------------- #
# MIDI reader (thư viện chuẩn) - dùng khi không có midi_loader của project
# --------------------------------------------------------------------------- #

def _read_vlq(b: bytes, i: int):
    v = 0
    while True:
        c = b[i]
        i += 1
        v = (v << 7) | (c & 0x7F)
        if not c & 0x80:
            return v, i


def _chunks(data: bytes):
    """(header 6 byte, [(tag, body)]) của một file MIDI."""
    if data[:4] != b"MThd":
        raise ValueError("không phải file MIDI (thiếu MThd)")
    hlen = struct.unpack(">I", data[4:8])[0]
    header = data[8:8 + hlen]
    i = 8 + hlen
    out = []
    while i + 8 <= len(data):
        ln = struct.unpack(">I", data[i + 4:i + 8])[0]
        out.append((data[i:i + 4], data[i + 8:i + 8 + ln]))
        i += 8 + ln
    return header, out


def _events(body: bytes):
    """[(tick, "meta", (kiểu, dữ liệu)) | (tick, "midi", bytes(status, d1[, d2]))]."""
    out, j, t, rs = [], 0, 0, None
    while j < len(body):
        dt, j = _read_vlq(body, j)
        t += dt
        s = body[j]
        if s == 0xFF:
            kind = body[j + 1]
            ln, j2 = _read_vlq(body, j + 2)
            out.append((t, "meta", (kind, body[j2:j2 + ln])))
            j = j2 + ln
            continue
        if s in (0xF0, 0xF7):
            ln, j = _read_vlq(body, j + 1)
            j += ln
            continue
        if s & 0x80:
            rs = s
            j += 1
        else:
            s = rs
        if s & 0xF0 in (0xC0, 0xD0):
            out.append((t, "midi", bytes([s, body[j]])))
            j += 1
        else:
            out.append((t, "midi", bytes([s, body[j], body[j + 1]])))
            j += 2
    return out


@dataclass
class MidiInput:
    notes: list
    controls: list
    key_fifths: Optional[int]
    source: str


def estimate_key(notes: Sequence) -> int:
    """Số dấu hóa (-6..6) mà gam trưởng/thứ tương ứng chứa nhiều thời lượng nốt nhất."""
    w = [0.0] * 12
    for n in notes:
        w[n.pitch % 12] += max(0.05, n.end - n.start)
    best, best_s = 0, -1.0
    for f in sorted(range(-6, 7), key=abs):
        tonic = (7 * f) % 12
        s = sum(w[(tonic + d) % 12] for d in (0, 2, 4, 5, 7, 9, 11))
        if s > best_s + 1e-9:
            best, best_s = f, s
    return best


def split_hands(notes: Sequence, split: int = MIDDLE_C, span: int = 14, tol: float = 0.04,
                follow: float = 0.15) -> None:
    """
    Gán `.hand` cho nốt của file 1 track bằng cách theo dõi vị trí hai tay. Mỗi hợp âm (các nốt cách nhau
    < tol giây) được cắt ở chỗ rẻ nhất: nốt thấp cho tay trái, nốt cao cho tay phải, giá = khoảng cách tới
    tâm của tay đó (tâm đi theo các nốt vừa chơi, tốc độ `follow`) + phạt nặng nếu một tay phải giãn quá
    `span` bán cung. Tâm ban đầu cách `split` 12 và 14 bán cung (C4 -> 48 và 74).
    """
    order = sorted(range(len(notes)), key=lambda i: (notes[i].start, notes[i].pitch))
    groups: List[list] = []
    for i in order:
        if groups and notes[i].start - notes[groups[-1][0]].start <= tol:
            groups[-1].append(i)
        else:
            groups.append([i])
    cl, cr = float(split - 12), float(split + 14)
    for g in groups:
        ps = sorted({notes[i].pitch for i in g})
        best = None
        for k in range(len(ps) + 1):
            low, high = ps[:k], ps[k:]
            cost = sum(abs(p - cl) for p in low) + sum(abs(p - cr) for p in high)
            for h in (low, high):
                if h and max(h) - min(h) > span:
                    cost += 40 * (max(h) - min(h) - span)
            if best is None or cost < best[0]:
                best = (cost, k)
        low, high = ps[:best[1]], ps[best[1]:]
        left = set(low)
        for i in g:
            notes[i].hand = LEFT if notes[i].pitch in left else RIGHT
        if low:
            cl = (1 - follow) * cl + follow * sum(low) / len(low)
        if high:
            cr = (1 - follow) * cr + follow * sum(high) / len(high)


def read_midi(path: str, threshold: int = MIDDLE_C) -> MidiInput:
    """
    Đọc .mid thành Note (giây) + pedal + key. Phân tay: nếu file có >= 2 track/channel có nốt thì
    tên "Right"/"Left" quyết định, không thì nhóm cao hơn là tay phải; còn 1 nhóm thì cắt theo pitch
    theo dõi vị trí hai tay (`split_hands`, tâm xuất phát quanh `threshold`).
    """
    data = open(path, "rb").read()
    header, chunks = _chunks(data)
    _fmt, _n, div = struct.unpack(">HHH", header[:6])
    if div & 0x8000:
        raise ValueError("MIDI dùng SMPTE time division, chưa hỗ trợ")
    tracks = [_events(b) for tag, b in chunks if tag == b"MTrk"]

    tempos = sorted({(t, int.from_bytes(p[1], "big")) for ev in tracks for t, k, p in ev
                     if k == "meta" and p[0] == 0x51})
    pts, sec, last_t, last_us = [], 0.0, 0, 500000
    for t, us in tempos:
        sec += (t - last_t) * last_us / 1e6 / div
        pts.append((t, sec, us))
        last_t, last_us = t, us
    if not pts or pts[0][0] > 0:
        pts.insert(0, (0, 0.0, 500000))

    def to_sec(tick):
        base = pts[0]
        for p in pts:
            if p[0] <= tick:
                base = p
            else:
                break
        return base[1] + (tick - base[0]) * base[2] / 1e6 / div

    key = None
    names, groups, controls = {}, {}, []
    for ti, ev in enumerate(tracks):
        on, end_tick = {}, max((t for t, _, _ in ev), default=0)
        for t, k, p in ev:
            if k == "meta":
                if p[0] == 0x03 and ti not in names:
                    names[ti] = p[1].decode("latin-1", "replace")
                elif p[0] == 0x59 and key is None and len(p[1]) >= 1:
                    key = struct.unpack("b", p[1][:1])[0]
                continue
            st = p[0] & 0xF0
            ch = p[0] & 0x0F
            if st == 0x90 and p[2] > 0:
                on.setdefault((ch, p[1]), []).append((t, p[2]))
            elif st == 0x80 or st == 0x90:
                q = on.get((ch, p[1]))
                if q:
                    t0, vel = q.pop(0)
                    groups.setdefault((ti, ch), []).append(
                        Note(p[1], to_sec(t0), max(to_sec(t), to_sec(t0) + 0.01), vel, 0, None))
            elif st == 0xB0 and p[1] in (64, 66, 67):
                controls.append((to_sec(t), p[1], p[2]))
        for (ch, pitch), q in on.items():
            for t0, vel in q:
                groups.setdefault((ti, ch), []).append(
                    Note(pitch, to_sec(t0), max(to_sec(end_tick), to_sec(t0) + 0.01), vel, 0, None))

    keys = [k for k, v in groups.items() if v]
    if not keys:
        raise ValueError("file MIDI không có nốt nào")
    if len(keys) >= 2:
        mean = {k: sum(n.pitch for n in groups[k]) / len(groups[k]) for k in keys}
        mid = (max(mean.values()) + min(mean.values())) / 2
        for k in keys:
            nm = names.get(k[0], "").lower()
            hand = RIGHT if "right" in nm else LEFT if "left" in nm else (RIGHT if mean[k] >= mid else LEFT)
            for n in groups[k]:
                n.hand = hand
    else:
        only = groups[keys[0]]
        split_hands(only, threshold)
    notes = sorted((n for k in keys for n in groups[k]), key=lambda n: (n.start, n.pitch))
    for n in notes:
        n.track = 0 if n.hand == RIGHT else 1
    return MidiInput(notes, sorted(controls), key, "builtin")


def load_midi_input(path: str, reader: str = "auto", threshold: int = MIDDLE_C) -> MidiInput:
    """reader: "loader" = midi_loader.load_song của project (hand_split), "builtin" = reader ở trên."""
    if reader in ("auto", "loader"):
        try:
            from midi_loader import load_song
        except (ImportError, AttributeError):
            if reader == "loader":
                raise SystemExit("--reader loader: không import được midi_loader.load_song")
        else:
            try:
                song = load_song(path, normalize=False)     # (normalize được gọi một lần ở dưới, không hai lần)
            except TypeError:                               # midi_loader cũ chưa có tham số normalize
                song = load_song(path)
            kf = getattr(song, "key_fifths", None)
            return MidiInput(list(song.notes), list(getattr(song, "raw_controls", None) or []),
                             kf if kf else None, "midi_loader")
    return read_midi(path, threshold)


# --------------------------------------------------------------------------- #
# MusicXML -> MusicXML (đường cũ của normalize_piano_musicxml.py)
# --------------------------------------------------------------------------- #

@dataclass
class Event:
    onset: int
    duration: int
    items: list
    part_index: int
    staff: str = None
    voice: str = None
    grace: list = field(default_factory=list)


def strip_namespaces(root):
    for elem in root.iter():
        if isinstance(elem.tag, str) and "}" in elem.tag:
            elem.tag = elem.tag.split("}", 1)[1]

        for key in list(elem.attrib.keys()):
            if "}" in key:
                del elem.attrib[key]


def note_name_to_midi(name):
    m = re.match(r"^([A-Ga-g])([#b]?)(-?\d+)$", name.strip())
    if not m:
        raise ValueError(f"Không parse được nốt: {name}")

    step = m.group(1).upper()
    acc = m.group(2)
    octave = int(m.group(3))

    step_map = {
        "C": 0,
        "D": 2,
        "E": 4,
        "F": 5,
        "G": 7,
        "A": 9,
        "B": 11,
    }

    alter = 0
    if acc == "#":
        alter = 1
    elif acc == "b":
        alter = -1

    return (octave + 1) * 12 + step_map[step] + alter


def get_child_int(elem, tag, default=0):
    child = elem.find(tag)
    if child is None or child.text is None:
        return default

    try:
        return int(child.text)
    except Exception:
        return default


def midi_from_pitch_elem(pitch_elem):
    if pitch_elem is None:
        return None

    step = pitch_elem.findtext("step")
    octave = pitch_elem.findtext("octave")
    alter = pitch_elem.findtext("alter")

    if step is None or octave is None:
        return None

    step_map = {
        "C": 0,
        "D": 2,
        "E": 4,
        "F": 5,
        "G": 7,
        "A": 9,
        "B": 11,
    }

    step = step.upper()
    if step not in step_map:
        return None

    alter_value = 0
    if alter is not None:
        try:
            alter_value = int(float(alter))
        except Exception:
            alter_value = 0

    return (int(octave) + 1) * 12 + step_map[step] + alter_value


def update_state_from_attrs(attrs, state):
    if attrs is None:
        return

    div = attrs.find("divisions")
    if div is not None and div.text:
        try:
            state["divisions"] = int(div.text)
        except Exception:
            pass

    time_elem = attrs.find("time")
    if time_elem is not None:
        beats = time_elem.findtext("beats")
        beat_type = time_elem.findtext("beat-type")

        if beats:
            try:
                state["beats"] = int(beats)
            except Exception:
                pass

        if beat_type:
            try:
                state["beat_type"] = int(beat_type)
            except Exception:
                pass


def expected_duration(state):
    try:
        if state["beat_type"] == 0:
            return max(1, state["beats"] * state["divisions"])

        return max(
            1,
            int(
                round(
                    state["beats"]
                    * state["divisions"]
                    * 4.0
                    / state["beat_type"]
                )
            ),
        )
    except Exception:
        return max(1, state["beats"] * state["divisions"])




def duration_type_and_dots(divisions, duration):
    if divisions <= 0 or duration <= 0:
        return "quarter", 0

    q = duration / divisions

    candidates = [
        (16, "long"),
        (8, "breve"),
        (4, "whole"),
        (2, "half"),
        (1, "quarter"),
        (0.5, "eighth"),
        (0.25, "16th"),
        (0.125, "32nd"),
        (0.0625, "64th"),
    ]

    best_name = "quarter"
    best_score = abs(q - 1)
    dots = 0

    for value, name in candidates:
        # Không chấm
        score = abs(q - value)
        if score < best_score - 1e-6:
            best_score = score
            best_name = name
            dots = 0

        # Một chấm
        dotted_value = value * 1.5
        score_dotted = abs(q - dotted_value)
        if score_dotted < best_score - 1e-6:
            best_score = score_dotted
            best_name = name
            dots = 1

    return best_name, dots


def make_rest(duration, divisions, full=False):
    note = ET.Element("note")

    rest = ET.SubElement(note, "rest")
    if full:
        rest.set("measure", "yes")

    d = ET.SubElement(note, "duration")
    d.text = str(int(duration))

    v = ET.SubElement(note, "voice")
    v.text = "1"

    if not full:
        type_name, dots = duration_type_and_dots(divisions, duration)

        t = ET.SubElement(note, "type")
        t.text = type_name

        for _ in range(dots):
            ET.SubElement(note, "dot")

    return note




def add_measure_attributes(measure_elem, divisions, key_elem, time_elem, clef_sign, clef_line):
    attrs = ET.SubElement(measure_elem, "attributes")

    div = ET.SubElement(attrs, "divisions")
    div.text = str(divisions)

    if key_elem is not None:
        attrs.append(copy.deepcopy(key_elem))
    else:
        key = ET.SubElement(attrs, "key")
        fifths = ET.SubElement(key, "fifths")
        fifths.text = "0"

    if time_elem is not None:
        attrs.append(copy.deepcopy(time_elem))
    else:
        time = ET.SubElement(attrs, "time")
        beats = ET.SubElement(time, "beats")
        beat_type = ET.SubElement(time, "beat-type")
        beats.text = "4"
        beat_type.text = "4"

    clef = ET.SubElement(attrs, "clef")
    sign = ET.SubElement(clef, "sign")
    line = ET.SubElement(clef, "line")

    sign.text = clef_sign
    line.text = str(clef_line)


def find_tempo_direction(root):
    for direction in root.iter("direction"):
        sound = direction.find("sound")

        if sound is not None and sound.get("tempo"):
            return copy.deepcopy(direction)

        if direction.find(".//metronome") is not None:
            return copy.deepcopy(direction)

    return None


def clean_direction(direction_elem):
    d = copy.deepcopy(direction_elem)

    for staff in d.findall("staff"):
        d.remove(staff)

    for voice in d.findall("voice"):
        d.remove(voice)

    return d


def add_final_barline(measure_elem):
    barline = ET.SubElement(measure_elem, "barline")
    barline.set("location", "right")

    bar_style = ET.SubElement(barline, "bar-style")
    bar_style.text = "light-heavy"


def add_score_part(parent, pid, name, channel, program, volume):
    score_part = ET.SubElement(parent, "score-part")
    score_part.set("id", pid)

    part_name = ET.SubElement(score_part, "part-name")
    part_name.set("print-object", "no")
    part_name.text = name

    score_instrument = ET.SubElement(score_part, "score-instrument")
    score_instrument.set("id", f"{pid}-I1")

    instrument_name = ET.SubElement(score_instrument, "instrument-name")
    instrument_name.text = name

    instrument_sound = ET.SubElement(score_instrument, "instrument-sound")
    instrument_sound.text = "keyboard.piano.grand"

    midi_instrument = ET.SubElement(score_part, "midi-instrument")
    midi_instrument.set("id", f"{pid}-I1")

    midi_channel = ET.SubElement(midi_instrument, "midi-channel")
    midi_channel.text = str(channel)

    midi_program = ET.SubElement(midi_instrument, "midi-program")
    midi_program.text = str(program)

    vol = ET.SubElement(midi_instrument, "volume")
    vol.text = str(volume)

    pan = ET.SubElement(midi_instrument, "pan")
    pan.text = "0"




# --------------------------------------------------------------------------- #
# MusicXML: độ dài ghi được, nốt nối (tie), dấu hóa
# --------------------------------------------------------------------------- #

# (độ dài theo phách đen, "gốc" mà vị trí bắt đầu nên chia hết): từ dài đến ngắn
_STD_Q = [
    (Fraction(8), Fraction(8)), (Fraction(6), Fraction(4)), (Fraction(4), Fraction(4)),
    (Fraction(3), Fraction(2)), (Fraction(2), Fraction(2)), (Fraction(3, 2), Fraction(1)),
    (Fraction(1), Fraction(1)), (Fraction(3, 4), Fraction(1, 2)), (Fraction(1, 2), Fraction(1, 2)),
    (Fraction(3, 8), Fraction(1, 4)), (Fraction(1, 4), Fraction(1, 4)),
    (Fraction(3, 16), Fraction(1, 8)), (Fraction(1, 8), Fraction(1, 8)), (Fraction(1, 16), Fraction(1, 16)),
]


def split_duration(pos, dur, divisions):
    """
    Chia `dur` (đơn vị divisions) bắt đầu tại `pos` trong ô nhịp thành các nốt ghi được, nối bằng tie.
    - dur đã là một giá trị ghi được (có thể một chấm) -> [dur] (giữ nguyên như bản cũ).
    - nếu không chia đúng được (tuplet, divisions quá thô) -> None, người gọi dùng nốt gần nhất như cũ.
    """
    if divisions <= 0 or dur <= 0:
        return None
    q = Fraction(int(dur), int(divisions))
    if any(q == v for v, _ in _STD_Q):
        return [int(dur)]
    cands = []
    for v, base in _STD_Q:
        u = v * divisions
        if u.denominator == 1:
            cands.append((int(u), base * divisions))
    pieces, rem, p = [], int(dur), int(pos)
    while rem > 0:
        # đang lệch phách thì lấp tới phách kế trước, rồi mới lấy nốt lớn (8th + dotted 8th, không phải dotted 8th + 16th + 16th)
        to_beat = (-p) % int(divisions)
        limit = to_beat if 0 < to_beat < rem else rem
        for u, base in cands:
            if u <= limit and Fraction(p) % base == 0:
                pieces.append(u)
                rem -= u
                p += u
                break
        else:
            return None
    return pieces if len(pieces) <= 4 else None


_SHARP_ORDER, _FLAT_ORDER = "FCGDAEB", "BEADGCF"
_SHARP_NAMES = {0: ("C", 0), 1: ("C", 1), 2: ("D", 0), 3: ("D", 1), 4: ("E", 0), 5: ("F", 0),
                6: ("F", 1), 7: ("G", 0), 8: ("G", 1), 9: ("A", 0), 10: ("A", 1), 11: ("B", 0)}
_FLAT_NAMES = {0: ("C", 0), 1: ("D", -1), 2: ("D", 0), 3: ("E", -1), 4: ("E", 0), 5: ("F", 0),
               6: ("G", -1), 7: ("G", 0), 8: ("A", -1), 9: ("A", 0), 10: ("B", -1), 11: ("B", 0)}
_ACC_NAMES = {1: "sharp", -1: "flat", 0: "natural", 2: "double-sharp", -2: "flat-flat"}


def key_alters(fifths):
    d = {s: 0 for s in "CDEFGAB"}
    if fifths > 0:
        for s in _SHARP_ORDER[:fifths]:
            d[s] = 1
    elif fifths < 0:
        for s in _FLAT_ORDER[:-fifths]:
            d[s] = -1
    return d


def spell(midi, fifths):
    """MIDI -> (step, alter, octave); dấu thăng nếu khóa không giáng, ngược lại dấu giáng."""
    step, alter = (_FLAT_NAMES if fifths < 0 else _SHARP_NAMES)[midi % 12]
    return step, alter, midi // 12 - 1


def new_acc_state(fifths):
    """Trạng thái dấu hóa của một ô nhịp (reset mỗi ô nhịp): chỉ ghi dấu khi khác trạng thái hiện tại."""
    return {"fifths": fifths, "key": key_alters(fifths), "seen": {}}







# --------------------------------------------------------------------------- #
# MusicXML -> MusicXML: tay, grace, tie, ô nhịp lấy đà
# --------------------------------------------------------------------------- #

def parse_measure(part_index, measure_elem, state, args=None):
    """
    Một measure của một part -> [Event]. Chord (<chord/>) nằm cùng onset với note trước nó và KHÔNG làm
    thời gian chạy tiếp. Grace note được gắn vào note chính kế tiếp cùng voice (trừ khi --drop-grace).
    state["measure_end"] = điểm cuối thật sự của measure (kể cả rest/forward), dùng cho ô nhịp lấy đà.
    """
    events = []
    t = 0
    end = 0
    last_by_voice = {}
    pending_grace = {}
    drop_grace = bool(getattr(args, "drop_grace", False))

    for child in measure_elem:
        tag = child.tag

        if tag == "attributes":
            update_state_from_attrs(child, state)

        elif tag == "backup":
            t = max(0, t - get_child_int(child, "duration", 0))
            last_by_voice.clear()

        elif tag == "forward":
            t += get_child_int(child, "duration", 0)
            end = max(end, t)
            last_by_voice.clear()

        elif tag == "note":
            voice = child.findtext("voice", "1")

            if child.find("grace") is not None:
                if not drop_grace and child.find("pitch") is not None:
                    pending_grace.setdefault(voice, []).append(child)
                continue

            dur = get_child_int(child, "duration", 0)
            is_chord = child.find("chord") is not None

            if child.find("rest") is not None:
                if dur > 0:
                    last_by_voice.pop(voice, None)
                    t += dur
                    end = max(end, t)
                continue

            midi = midi_from_pitch_elem(child.find("pitch"))
            last_ev = last_by_voice.get(voice)

            if is_chord and last_ev is not None:
                if midi is not None:
                    last_ev.items.append((midi, child))
                    last_ev.duration = max(last_ev.duration, dur)
                continue

            if dur <= 0:
                continue

            if midi is None:
                last_by_voice.pop(voice, None)
                t += dur
                end = max(end, t)
                continue

            ev = Event(
                onset=t,
                duration=dur,
                items=[(midi, child)],
                part_index=part_index,
                staff=child.findtext("staff"),
                voice=voice,
                grace=pending_grace.pop(voice, []),
            )
            events.append(ev)
            last_by_voice[voice] = ev
            t += dur
            end = max(end, t)

    state["measure_end"] = end
    return events


def build_sequence(events, measure_duration):
    """
    Các event của một tay trong một measure -> chuỗi {"rest"|"notes"} phủ đủ measure.
    Event cùng onset (kể cả khác voice) gộp thành một hợp âm (độ dài lớn nhất, bị chặn bởi onset kế);
    nốt ngân bị cắt tại onset kế. Grace của event đầu tiên có grace được giữ ở entry["grace"].
    """
    seq = []

    if measure_duration <= 0:
        return seq

    by_onset = {}

    for ev in events:
        if ev.onset < 0 or ev.onset >= measure_duration:
            continue

        if not ev.items:
            continue

        by_onset.setdefault(ev.onset, []).append(ev)

    onsets = sorted(by_onset.keys())
    t = 0

    for i, onset in enumerate(onsets):
        if onset < t:
            continue

        if onset > t:
            seq.append({"type": "rest", "duration": onset - t})
            t = onset

        group = by_onset[onset]

        items = []
        seen = set()
        durations = []
        grace = []

        for ev in group:
            durations.append(ev.duration)

            if ev.grace and not grace:
                grace = ev.grace

            for midi, orig in ev.items:
                if midi is None or midi in seen:
                    continue

                seen.add(midi)
                items.append((midi, orig))

        if not items:
            continue

        next_onset = onsets[i + 1] if i + 1 < len(onsets) else measure_duration
        max_allowed = min(next_onset - onset, measure_duration - onset)

        if max_allowed <= 0:
            continue

        dur = min(max(durations) if durations else max_allowed, max_allowed)

        if dur <= 0:
            continue

        items.sort(key=lambda x: x[0])

        seq.append({"type": "notes", "duration": dur, "items": items, "grace": grace})
        t = onset + dur

    if t < measure_duration:
        seq.append({"type": "rest", "duration": measure_duration - t})

    return seq


def build_part_hand_map(avg_pitch, swap=False):
    """{chỉ số part: "R"/"L"} theo pitch trung bình: part cao là tay phải. Một part -> tay phải."""
    if not avg_pitch:
        return {}
    if len(avg_pitch) == 1:
        m = {k: RIGHT for k in avg_pitch}
    else:
        mid = (max(avg_pitch.values()) + min(avg_pitch.values())) / 2
        m = {k: (RIGHT if v >= mid else LEFT) for k, v in avg_pitch.items()}
    if swap:
        m = {k: (LEFT if v == RIGHT else RIGHT) for k, v in m.items()}
    return m


def assign_hand(ev, mode, split_midi, part_hand):
    """
    Tay của một event. mode:
      pitch: pitch thấp nhất < split -> trái, không thì phải
      staff: staff 2 -> trái, staff 1 -> phải, không có staff -> theo part
      part : theo part (part cao = phải)
      auto : pitch thấp nhất < split -> trái; không thì staff 2 -> trái, staff 1 -> phải; không có staff -> theo part
    """
    bottom = min(m for m, _ in ev.items)
    by_part = part_hand.get(ev.part_index, RIGHT)

    if mode == "pitch":
        return LEFT if bottom < split_midi else RIGHT
    if mode == "part":
        return by_part
    by_staff = LEFT if ev.staff == "2" else RIGHT if ev.staff == "1" else by_part
    if mode == "staff":
        return by_staff
    return LEFT if bottom < split_midi else by_staff


def _tie_types(note):
    return {t.get("type") for t in note.findall("tie")}


def _drop_tie(note, kind):
    for t in list(note.findall("tie")):
        if t.get("type") == kind:
            note.remove(t)
    notations = note.find("notations")
    if notations is not None:
        for t in list(notations.findall("tied")):
            if t.get("type") == kind:
                notations.remove(t)
        if len(notations) == 0:
            note.remove(notations)


def sanitize_ties(part):
    """
    Bỏ tie "treo": tie start phải có note cùng pitch bắt đầu đúng lúc nó kết thúc, và tie stop phải
    có note cùng pitch kết thúc đúng lúc nó bắt đầu. Dùng cho cả tie giữ từ input lẫn tie tự sinh.
    """
    rows = []
    base = 0
    for measure in part.findall("measure"):
        pos = 0
        last = 0
        for child in measure:
            if child.tag == "forward":
                pos += get_child_int(child, "duration", 0)
            elif child.tag == "note" and child.find("grace") is None:
                d = get_child_int(child, "duration", 0)
                chord = child.find("chord") is not None
                onset = base + (last if chord else pos)
                if not chord:
                    last, pos = pos, pos + d
                midi = midi_from_pitch_elem(child.find("pitch"))
                if midi is not None:
                    rows.append((onset, onset + d, midi, child))
        base += pos
    ends = {(e, m) for _, e, m, _ in rows}
    starts = {(s, m) for s, _, m, _ in rows}
    for s, e, m, note in rows:
        kinds = _tie_types(note)
        if "start" in kinds and (e, m) not in starts:
            _drop_tie(note, "start")
        if "stop" in kinds and (s, m) not in ends:
            _drop_tie(note, "stop")


def make_grace_note(g, chord=False):
    """Grace note từ note gốc `g`: grace, [chord], pitch, voice 1, type, accidental (không có duration)."""
    note = ET.Element("note")
    grace = ET.SubElement(note, "grace")
    src = g.find("grace")
    if src is not None:
        for k, v in src.attrib.items():
            grace.set(k, v)
    if chord:
        ET.SubElement(note, "chord")
    pitch = g.find("pitch")
    if pitch is not None:
        note.append(copy.deepcopy(pitch))
    ET.SubElement(note, "voice").text = "1"
    ET.SubElement(note, "type").text = g.findtext("type") or "eighth"
    for _ in g.findall("dot"):
        ET.SubElement(note, "dot")
    acc = g.find("accidental")
    if acc is not None:
        note.append(copy.deepcopy(acc))
    return note


def note_lyrics(note):
    """((number, syllabic, text), ...) của các <lyric> trong một <note> MusicXML; bỏ lyric không có chữ."""
    out = []
    for ly in note.findall("lyric"):
        text = "".join(t.text or "" for t in ly.findall("text")).strip()
        if text:
            out.append((ly.get("number") or ly.get("name") or "1", ly.findtext("syllabic"), text))
    return tuple(out)


def _entry_lyrics(entry, idx, orig_note, piece_i, tie_prev, seen):
    """
    Lyric cho nốt thứ `idx` của một hợp âm khi ghi: chỉ ở mảnh đầu và không phải nốt nối từ ô nhịp trước;
    mỗi số lời (number) chỉ một lần trong hợp âm. Đường MusicXML lấy từ nốt gốc, đường MIDI/regrid từ entry.
    """
    if piece_i != 0 or tie_prev:
        return None
    if orig_note is not None:
        lyr = note_lyrics(orig_note)
    else:
        lyr = entry.get("lyrics") if idx == 0 else ()
    out = []
    for ly in (lyr or ()):
        if ly[0] not in seen:
            seen.add(ly[0])
            out.append(ly)
    return out


def make_note_from_orig(orig_note, duration, divisions, chord=False, midi=None,
                        tie_stop=False, tie_start=False, show_accidental=True, acc_state=None, lyrics=None):
    """
    Một <note> voice 1. `orig_note` (đường MusicXML) cho pitch/accidental gốc; nếu None thì dựng pitch
    từ `midi` (đường MIDI). Thứ tự phần tử theo MusicXML: chord, pitch, duration, tie, voice, type,
    dot, accidental, notations.
    """
    note = ET.Element("note")

    if chord:
        ET.SubElement(note, "chord")

    pitch = None
    spelled = None
    if orig_note is not None:
        pitch = orig_note.find("pitch")
        if pitch is not None:
            note.append(copy.deepcopy(pitch))
    elif midi is not None:
        spelled = spell(midi, acc_state["fifths"] if acc_state else 0)
        pitch = ET.SubElement(note, "pitch")
        ET.SubElement(pitch, "step").text = spelled[0]
        if spelled[1]:
            ET.SubElement(pitch, "alter").text = str(spelled[1])
        ET.SubElement(pitch, "octave").text = str(spelled[2])

    d = ET.SubElement(note, "duration")
    d.text = str(int(duration))

    if tie_stop:
        ET.SubElement(note, "tie").set("type", "stop")
    if tie_start:
        ET.SubElement(note, "tie").set("type", "start")

    v = ET.SubElement(note, "voice")
    v.text = "1"

    type_name, dots = duration_type_and_dots(divisions, duration)

    t = ET.SubElement(note, "type")
    t.text = type_name

    for _ in range(dots):
        ET.SubElement(note, "dot")

    if orig_note is not None:
        if show_accidental:
            acc = orig_note.find("accidental")
            if acc is not None:
                note.append(copy.deepcopy(acc))
            elif pitch is not None:
                alter = pitch.findtext("alter")
                if alter is not None:
                    try:
                        acc_text = _ACC_NAMES.get(int(float(alter)))
                        if acc_text:
                            ET.SubElement(note, "accidental").text = acc_text
                    except Exception:
                        pass
    elif spelled is not None and acc_state is not None:
        step, alter, octave = spelled
        cur = acc_state["seen"].get((step, octave), acc_state["key"][step])
        if show_accidental and alter != cur:
            ET.SubElement(note, "accidental").text = _ACC_NAMES[alter]
        acc_state["seen"][(step, octave)] = alter

    if tie_stop or tie_start:
        notations = ET.SubElement(note, "notations")
        if tie_stop:
            ET.SubElement(notations, "tied").set("type", "stop")
        if tie_start:
            ET.SubElement(notations, "tied").set("type", "start")

    for number, syllabic, text in (lyrics or ()):        # <lyric> đứng sau <notations>
        ly = ET.SubElement(note, "lyric")
        ly.set("number", str(number))
        if syllabic:
            ET.SubElement(ly, "syllabic").text = syllabic
        ET.SubElement(ly, "text").text = text

    return note


def append_sequence_to_measure(measure_elem, seq, divisions, measure_duration, fifths=None,
                               keep_ties=False, no_fill_rests=False):
    """
    Ghi chuỗi (nghỉ / hợp âm) của một ô nhịp. Độ dài không ghi được thì chia thành nốt nối tie
    (nghỉ thì chia thành nhiều nghỉ). Entry có thể có "tie_prev"/"tie_next" (nốt nối qua vạch nhịp)
    và "grace" (grace note đặt trước hợp âm).
    `fifths` khác None = đường MIDI: pitch dựng từ MIDI, dấu hóa tính theo khóa.
    `keep_ties`: giữ tie của input khi độ dài nốt không đổi (tie treo được dọn sau bằng sanitize_ties).
    `no_fill_rests`: thay rest bằng <forward> (khoảng trống không hiện dấu nghỉ).
    """
    pos = 0
    acc_state = new_acc_state(fifths) if fifths is not None else None

    for entry in seq:
        dur = entry["duration"]
        pieces = split_duration(pos, dur, divisions) or [dur]

        if entry["type"] == "rest":
            if no_fill_rests:
                fwd = ET.SubElement(measure_elem, "forward")
                ET.SubElement(fwd, "duration").text = str(int(dur))
                ET.SubElement(fwd, "voice").text = "1"
            elif dur >= measure_duration:
                measure_elem.append(make_rest(dur, divisions, full=True))
            else:
                for piece in pieces:
                    measure_elem.append(make_rest(piece, divisions))

        elif entry["type"] == "notes":
            tie_prev = bool(entry.get("tie_prev"))
            tie_next = bool(entry.get("tie_next"))
            seen_ln = set()

            for gi, g in enumerate(entry.get("grace") or []):
                measure_elem.append(make_grace_note(g, chord=(g.find("chord") is not None and gi > 0)))

            for i, piece in enumerate(pieces):
                for idx, (midi, orig_note) in enumerate(entry["items"]):
                    kinds = set()
                    if keep_ties and orig_note is not None and len(pieces) == 1:
                        if get_child_int(orig_note, "duration", -1) == piece:
                            kinds = _tie_types(orig_note)
                    measure_elem.append(
                        make_note_from_orig(
                            orig_note,
                            piece,
                            divisions,
                            chord=(idx > 0),
                            midi=midi,
                            tie_stop=(i > 0 or tie_prev or "stop" in kinds),
                            tie_start=(i < len(pieces) - 1 or tie_next or "start" in kinds),
                            show_accidental=(i == 0 and not tie_prev),
                            acc_state=acc_state,
                            lyrics=_entry_lyrics(entry, idx, orig_note, i, tie_prev, seen_ln),
                        )
                    )

        pos += dur


def load_musicxml_root(path):
    """Đọc .xml/.musicxml hoặc .mxl (zip) thành root đã bỏ namespace."""
    if path.lower().endswith(".mxl"):
        import zipfile
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            rootfile = None
            if "META-INF/container.xml" in names:
                for rf in ET.fromstring(z.read("META-INF/container.xml")).iter():
                    if rf.tag.endswith("rootfile") and rf.get("full-path"):
                        rootfile = rf.get("full-path")
                        break
            if rootfile is None:
                rootfile = next((n for n in names if n.lower().endswith((".xml", ".musicxml"))
                                 and not n.startswith("META-INF")), None)
            if rootfile is None:
                raise ValueError("không tìm thấy file MusicXML trong .mxl")
            if z.getinfo(rootfile).file_size > 64 * 1024 * 1024:
                raise ValueError(".mxl giải nén quá lớn (> 64 MB)")
            root = ET.fromstring(z.read(rootfile))
    else:
        root = ET.parse(path).getroot()
    strip_namespaces(root)
    return root


def run_musicxml(args):
    """MusicXML (nhiều voice/staff, .xml/.musicxml/.mxl) -> 2 part P1/P2, mỗi part voice 1."""
    try:
        split_midi = note_name_to_midi(args.split)
    except ValueError as e:
        print(f"Lỗi --split: {e}", file=sys.stderr)
        return 1
    midi_only = [name for name, on in (
        ("--fine", args.fine), ("--min-dur", args.min_dur),
        ("--lh-max", args.lh_max is not None), ("--rh-max", args.rh_max is not None),
        ("--drop-ornaments", args.drop_ornaments), ("--simplify", args.simplify),
        ("--beats", args.beats != 4), ("--max-bpm", args.max_bpm != MAX_BPM),
        ("--voices", args.voices != "single"), ("--no-tidy", args.no_tidy),
        ("--chord-end", args.chord_end != "median"), ("--downbeat", args.downbeat != "auto"),
    ) if on]
    if midi_only:
        print("Lưu ý: %s chỉ áp dụng cho input MIDI; đường MusicXML -> MusicXML bỏ qua. "
              "Dùng xml_regrid_piano.py nếu muốn re-grid MusicXML bằng engine MIDI."
              % ", ".join(midi_only), file=sys.stderr)
    try:
        root = load_musicxml_root(args.input)
    except Exception as e:
        print(f"Không đọc được file input: {e}", file=sys.stderr)
        return 1
    if root.tag != "score-partwise":
        print(f"Chỉ hỗ trợ score-partwise, nhưng root là <{root.tag}>.", file=sys.stderr)
        return 1

    parts = root.findall("part")
    part_measures = [p.findall("measure") for p in parts]
    max_measures = max((len(x) for x in part_measures), default=0)
    if max_measures == 0:
        print("Không tìm thấy <part>/<measure> trong MusicXML.", file=sys.stderr)
        return 1

    measure_numbers = []
    for mi in range(max_measures):
        num = next((ms[mi].get("number") for ms in part_measures if mi < len(ms) and ms[mi].get("number")), None)
        measure_numbers.append(num if num else str(mi + 1))

    first_attrs = root.find(".//attributes")
    initial_divisions, initial_beats, initial_beat_type = 1, 4, 4
    initial_key = initial_time = None
    if first_attrs is not None:
        initial_divisions = get_child_int(first_attrs, "divisions", 1) or 1
        initial_key = first_attrs.find("key")
        initial_time = first_attrs.find("time")
        if initial_time is not None:
            initial_beats = get_child_int(initial_time, "beats", 4) or 4
            initial_beat_type = get_child_int(initial_time, "beat-type", 4) or 4

    states = [{"divisions": initial_divisions, "beats": initial_beats, "beat_type": initial_beat_type}
              for _ in parts]
    tempo_direction = find_tempo_direction(root)

    # 1. đọc hết (state của mỗi part chạy tuần tự qua các measure)
    measures = []
    pitch_sum, pitch_cnt = {}, {}
    for mi in range(max_measures):
        evs, ends, implicit, time_e, key_e = [], [], False, None, None
        for pi, ms in enumerate(part_measures):
            if mi >= len(ms):
                continue
            m = ms[mi]
            implicit = implicit or m.get("implicit") == "yes" or m.get("number") == "0"
            a = m.find("attributes")
            if a is not None and mi > 0:
                time_e = time_e if time_e is not None else a.find("time")
                key_e = key_e if key_e is not None else a.find("key")
            got = parse_measure(pi, m, states[pi], args)
            evs.extend(got)
            ends.append(states[pi].get("measure_end", 0))
            for ev in got:
                for midi, _ in ev.items:
                    pitch_sum[pi] = pitch_sum.get(pi, 0) + midi
                    pitch_cnt[pi] = pitch_cnt.get(pi, 0) + 1
        nominal = max(1, max(expected_duration(st) for st in states))
        div = max(1, max(st["divisions"] for st in states))
        dur = nominal
        if implicit and 0 < max(ends, default=0) < nominal:
            dur = max(ends)               # ô nhịp lấy đà / ô nhịp kết ngắn: không thêm nghỉ cho đủ
        measures.append({"events": evs, "duration": dur, "divisions": div, "short": dur < nominal,
                         "time": copy.deepcopy(time_e), "key": copy.deepcopy(key_e)})

    part_hand = build_part_hand_map({pi: pitch_sum[pi] / pitch_cnt[pi] for pi in pitch_sum}, args.swap_parts)

    # 2. dựng output
    new_root = ET.Element("score-partwise")
    new_root.set("version", root.get("version", "4.0"))
    for tag in ("identification", "defaults"):
        e = root.find(tag)
        if e is not None:
            new_root.append(copy.deepcopy(e))
    add_part_list(new_root, args)
    right_part = ET.SubElement(new_root, "part")
    right_part.set("id", "P1")
    left_part = ET.SubElement(new_root, "part")
    left_part.set("id", "P2")

    cur_div = None
    for mi, info in enumerate(measures):
        right_events, left_events = [], []
        for ev in info["events"]:
            if args.split_chords and args.hand in ("auto", "pitch"):
                lo = [(m, o) for m, o in ev.items if m < split_midi]
                hi = [(m, o) for m, o in ev.items if m >= split_midi]
                if lo:
                    left_events.append(replace(ev, items=lo, grace=ev.grace))
                if hi:
                    right_events.append(replace(ev, items=hi, grace=([] if lo else ev.grace)))
            elif assign_hand(ev, args.hand, split_midi, part_hand) == LEFT:
                left_events.append(ev)
            else:
                right_events.append(ev)

        duration, divisions = info["duration"], info["divisions"]
        right_seq = build_sequence(right_events, duration)
        left_seq = build_sequence(left_events, duration)

        rm = ET.SubElement(right_part, "measure")
        lm = ET.SubElement(left_part, "measure")
        for m in (rm, lm):
            m.set("number", measure_numbers[mi])
            if info["short"]:
                m.set("implicit", "yes")

        if mi == 0:
            add_measure_attributes(rm, divisions, initial_key, initial_time, "G", 2)
            add_measure_attributes(lm, divisions, initial_key, initial_time, "F", 4)
            if tempo_direction is not None:
                rm.append(clean_direction(tempo_direction))
        elif divisions != cur_div or info["time"] is not None or info["key"] is not None:
            for m in (rm, lm):
                attrs = ET.SubElement(m, "attributes")
                if divisions != cur_div:
                    ET.SubElement(attrs, "divisions").text = str(divisions)
                if info["key"] is not None:
                    attrs.append(copy.deepcopy(info["key"]))
                if info["time"] is not None:
                    attrs.append(copy.deepcopy(info["time"]))
        cur_div = divisions

        for m, seq in ((rm, right_seq), (lm, left_seq)):
            append_sequence_to_measure(m, seq, divisions, duration,
                                       keep_ties=args.keep_ties, no_fill_rests=args.no_fill_rests)

        if mi == len(measures) - 1 and not args.no_final_barline:
            add_final_barline(rm)
            add_final_barline(lm)

    sanitize_ties(right_part)
    sanitize_ties(left_part)
    write_xml_file(new_root, args.output)

    print(f"Đã xuất file: {args.output}")
    print(f"Phân tay: {args.hand}, ngưỡng {args.split} (MIDI {split_midi}), split chords: {args.split_chords}")
    return 0



# --------------------------------------------------------------------------- #
# Clean -> MusicXML (đường MIDI): 2 part, mỗi part đúng 1 voice
# --------------------------------------------------------------------------- #

def clean_to_sequences(clean: Clean, hand: str):
    """Một tay của `clean` thành [ô nhịp][entry]: nghỉ / hợp âm, cắt tại vạch nhịp (tie qua vạch)."""
    unit = clean.unit
    bar_u = clean.beats_per_bar * clean.units_per_beat
    nbars = max(1, len(clean.bar_times) - 1)

    by_u = {}
    lyr_at = {}                                  # unit -> lyric của các nốt bắt đầu ở đó
    for n in clean.notes:
        if n.hand == hand:
            u_n = int(round(n.start / unit))
            by_u.setdefault(u_n, []).append((n.pitch, int(round(n.end / unit))))
            for ly in (getattr(n, "lyric", None) or ()):
                lyr_at.setdefault(u_n, []).append(ly)

    segs, cur = [], 0
    for s0 in sorted(by_u):
        e = max(e for _, e in by_u[s0])
        if s0 < cur:
            raise ValueError("write_musicxml cần mỗi tay một chuỗi không chồng nhau "
                             "(normalize(voices=\"single\")); nốt ở unit %d chồng lên nốt trước" % s0)
        s = s0
        if e <= s:
            continue
        if s > cur:
            segs.append((cur, s, None))
        segs.append((s, e, sorted({p for p, _ in by_u[s0]})))
        cur = e
    total = max(nbars * bar_u, cur)
    nbars = -(-total // bar_u)
    if cur < nbars * bar_u:
        segs.append((cur, nbars * bar_u, None))

    measures = [[] for _ in range(nbars)]
    for a0, b, pitches in segs:
        a = a0
        while a < b:
            m = a // bar_u
            end = min(b, (m + 1) * bar_u)
            if pitches is None:
                measures[m].append({"type": "rest", "duration": end - a})
            else:
                measures[m].append({"type": "notes", "duration": end - a,
                                    "items": [(p, None) for p in pitches],
                                    "lyrics": lyr_at.get(a0) if a == a0 else None,
                                    "tie_prev": a > a0, "tie_next": end < b})
            a = end
    return measures


def make_tempo_direction(bpm):
    d = ET.Element("direction")
    d.set("placement", "above")
    dt = ET.SubElement(d, "direction-type")
    m = ET.SubElement(dt, "metronome")
    ET.SubElement(m, "beat-unit").text = "quarter"
    ET.SubElement(m, "per-minute").text = "%g" % round(bpm, 2)
    ET.SubElement(d, "sound").set("tempo", "%g" % round(bpm, 2))
    return d


def add_part_list(root, args):
    part_list = ET.SubElement(root, "part-list")
    if not args.no_brace:
        pg = ET.SubElement(part_list, "part-group")
        pg.set("type", "start")
        pg.set("number", "1")
        ET.SubElement(pg, "group-symbol").text = "brace"
        ET.SubElement(pg, "group-barline").text = "yes"
    add_score_part(part_list, "P1", "Piano Right", args.channel1, args.program, args.volume1)
    add_score_part(part_list, "P2", "Piano Left", args.channel2, args.program, args.volume2)
    if not args.no_brace:
        pg = ET.SubElement(part_list, "part-group")
        pg.set("type", "stop")
        pg.set("number", "1")


def write_xml_file(root, path):
    try:
        ET.indent(root, space="  ")
    except Exception:
        pass
    with open(path, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write('<!DOCTYPE score-partwise PUBLIC '
                '"-//Recordare//DTD MusicXML 4.0 Partwise//EN" '
                '"http://www.musicxml.org/dtds/partwise.dtd">\n')
        f.write(ET.tostring(root, encoding="unicode"))


class _XmlOpts:
    """Mặc định cho write_musicxml khi dùng như thư viện."""
    channel1, channel2, program = 1, 2, 1
    volume1 = volume2 = 78.7402
    no_brace = False
    no_final_barline = False


def write_musicxml(path: str, clean: Clean, key_fifths: int = 0, args=None) -> int:
    """
    Ghi `clean` thành MusicXML partwise: P1 tay phải (khóa Sol), P2 tay trái (khóa Fa), mỗi ô nhịp
    đúng độ dài, mỗi part chỉ voice 1; nốt qua vạch nhịp / độ dài lạ được nối bằng tie.
    Trả về số nốt. Chỉ nhận bản `voices="single"` (mỗi tay một chuỗi không chồng).
    """
    args = args or _XmlOpts()
    divisions = clean.units_per_beat
    bar_u = clean.beats_per_bar * divisions
    right = clean_to_sequences(clean, RIGHT)
    left = clean_to_sequences(clean, LEFT)
    nbars = max(len(right), len(left))
    right += [[{"type": "rest", "duration": bar_u}] for _ in range(nbars - len(right))]
    left += [[{"type": "rest", "duration": bar_u}] for _ in range(nbars - len(left))]

    root = ET.Element("score-partwise")
    root.set("version", "4.0")
    add_part_list(root, args)
    parts = [(ET.SubElement(root, "part"), right, "G", 2), (ET.SubElement(root, "part"), left, "F", 4)]
    parts[0][0].set("id", "P1")
    parts[1][0].set("id", "P2")

    key = ET.Element("key")
    ET.SubElement(key, "fifths").text = str(int(key_fifths))
    time = ET.Element("time")
    ET.SubElement(time, "beats").text = str(clean.beats_per_bar)
    ET.SubElement(time, "beat-type").text = "4"

    for pi, (part, seqs, sign, line) in enumerate(parts):
        for mi in range(nbars):
            m = ET.SubElement(part, "measure")
            m.set("number", str(mi + 1))
            if mi == 0:
                add_measure_attributes(m, divisions, key, time, sign, line)
                if pi == 0:
                    m.append(make_tempo_direction(clean.bpm))
            append_sequence_to_measure(m, seqs[mi], divisions, bar_u, fifths=int(key_fifths),
                                       no_fill_rests=getattr(args, "no_fill_rests", False))
            if mi == nbars - 1 and not args.no_final_barline:
                add_final_barline(m)

    write_xml_file(root, path)
    return len(clean.notes)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

_XML_EXT = (".xml", ".musicxml")
_XML_IN_EXT = _XML_EXT + (".mxl",)
_MIDI_EXT = (".mid", ".midi")


def run_midi(args):
    """MIDI -> MIDI sạch, hoặc MIDI -> MusicXML sạch (đúng 1 voice mỗi tay)."""
    src = args.input
    to_xml = args.output.lower().endswith(_XML_EXT)
    if to_xml and args.voices == "keep":
        print("--voices keep chỉ dùng được khi xuất .mid (MusicXML chỉ có 1 voice mỗi tay).", file=sys.stderr)
        return 1
    try:
        threshold = note_name_to_midi(args.split)
    except ValueError as e:
        print(f"Lỗi threshold: {e}", file=sys.stderr)
        return 1
    lh_max = args.lh_max if args.lh_max is not None else (3 if args.simplify else 0)
    rh_max = args.rh_max if args.rh_max is not None else (1 if args.simplify else 0)
    drop_orn = args.drop_ornaments or args.simplify
    try:
        inp = load_midi_input(src, args.reader, threshold)
        clean = normalize(inp.notes, controls=inp.controls or None, beats_per_bar=args.beats,
                          max_bpm=args.max_bpm, voices=args.voices, chord_end=args.chord_end,
                          downbeat=args.downbeat, tidy=not args.no_tidy,
                          fine=args.fine, min_dur=args.min_dur,
                          lh_max=lh_max, rh_max=rh_max, drop_ornaments=drop_orn)
    except (ValueError, RuntimeError) as e:
        print(f"Không chuẩn hóa được {src}: {e}", file=sys.stderr)
        return 1

    fifths = inp.key_fifths if inp.key_fifths is not None else estimate_key(clean.notes)
    if to_xml:
        n = write_musicxml(args.output, clean, key_fifths=fifths, args=args)
    else:
        n = write_midi(args.output, clean, key_fifths=fifths, program=max(0, args.program - 1))

    for line in clean.report:
        print(line)
    print(f"Đã xuất file: {args.output} ({n} nốt: tay phải {clean.stats['right']}, "
          f"tay trái {clean.stats['left']}; đọc bằng {inp.source}; khóa {fifths:+d})")
    return 0


def build_parser():
    p = argparse.ArgumentParser(
        description="Chuẩn hóa piano (MIDI hoặc MusicXML) về 2 tay, mỗi tay 1 voice, timing sạch."
    )
    p.add_argument("input", help="File đầu vào: .mid/.midi hoặc .xml/.musicxml/.mxl")
    p.add_argument("output", nargs="?", help="File đầu ra (.mid hoặc .xml). Mặc định <input>_clean.<đuôi>")

    p.add_argument("--hand", choices=("auto", "staff", "pitch", "part"), default="auto",
                   help="(MusicXML) cách phân tay. auto: pitch thấp hơn --split -> trái, không thì theo staff/part")
    p.add_argument("--split", "--threshold", dest="split", default="C4",
                   help="Ngưỡng tách tay, mặc định C4. MusicXML: pitch thấp nhất < ngưỡng là tay trái. "
                        "MIDI 1 track: điểm xuất phát của bộ theo dõi hai tay. (--threshold là tên cũ)")
    p.add_argument("--split-chords", action="store_true",
                   help="(MusicXML) tách từng note trong chord theo --split (chỉ khi --hand auto/pitch).")
    p.add_argument("--swap-parts", action="store_true", help="(MusicXML) đảo tay phải/trái theo part.")
    p.add_argument("--keep-ties", action="store_true",
                   help="(MusicXML) giữ tie của input khi độ dài nốt không đổi; tie treo bị dọn.")
    p.add_argument("--keep-beams", action="store_true", help="(không còn tác dụng: beam do phần mềm chấm nhạc tự vẽ)")
    p.add_argument("--drop-grace", action="store_true", help="(MusicXML) bỏ grace note (mặc định giữ).")
    p.add_argument("--no-fill-rests", action="store_true",
                   help="(MusicXML) không ghi dấu nghỉ, dùng <forward> cho khoảng trống.")
    p.add_argument("--channel1", type=int, default=1)
    p.add_argument("--channel2", type=int, default=2)
    p.add_argument("--program", type=int, default=1)
    p.add_argument("--volume1", type=float, default=78.7402)
    p.add_argument("--volume2", type=float, default=78.7402)
    p.add_argument("--no-brace", action="store_true", help="Không thêm brace nhóm piano.")
    p.add_argument("--no-final-barline", action="store_true", help="Không thêm barline light-heavy ở cuối.")

    g = p.add_argument_group("MIDI input")
    g.add_argument("--reader", choices=("auto", "loader", "builtin"), default="auto",
                   help="auto: dùng midi_loader của project nếu import được, không thì reader có sẵn")
    g.add_argument("--beats", type=int, default=4, help="số phách mỗi ô nhịp (mặc định 4)")
    g.add_argument("--max-bpm", type=float, default=MAX_BPM, help="giữ phách đen dưới tempo này (mặc định %(default)s)")
    g.add_argument("--voices", choices=("single", "keep"), default="single",
                   help="single: mỗi tay 1 voice (mặc định). keep: giữ nốt ngân (chỉ khi xuất .mid)")
    g.add_argument("--no-tidy", action="store_true",
                   help="giữ độ dài như đã chơi (phần mềm chấm nhạc có thể tách voice 2)")
    g.add_argument("--chord-end", choices=("median", "max", "min"), default="median")
    g.add_argument("--downbeat", choices=("auto", "first"), default="auto")
    g.add_argument("--fine", action="store_true",
                   help="cho phép đặt hợp âm/độ dài lên nửa ô lưới (ornament, 32nd). Mặc định tắt: mọi nốt "
                        "nằm trên ô lưới nguyên nên không còn nốt vụn")
    g.add_argument("--min-dur", type=float, default=MIN_DUR, metavar="SEC",
                   help="bỏ nốt ngắn hơn SEC giây trước khi chuẩn hóa (mặc định %(default)s = giữ hết)")
    g.add_argument("--lh-max", type=int, default=None, metavar="N",
                   help="tay trái tối đa N nốt mỗi hợp âm (giữ nốt bass trước). 1 = chỉ bass. "
                        "0 = không giới hạn (mặc định 0, hoặc 3 khi --simplify)")
    g.add_argument("--rh-max", type=int, default=None, metavar="N",
                   help="tay phải tối đa N nốt mỗi hợp âm (giữ nốt cao nhất = giai điệu trước). "
                        "1 = chỉ nốt chủ. 0 = không giới hạn (mặc định 0, hoặc 1 khi --simplify)")
    g.add_argument("--drop-ornaments", action="store_true",
                   help="bỏ nốt láy (nốt rất ngắn, cách nốt dài cùng beat / hợp âm dài kề bên 1-2 bán cung)")
    g.add_argument("--simplify", action="store_true",
                   help="đơn giản hóa: --lh-max 3 --rh-max 1 --drop-ornaments (ghi rõ --lh-max/--rh-max "
                        "thì giá trị đó thắng, kể cả 0)")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.keep_beams:
        print("Lưu ý: --keep-beams không còn tác dụng (beam luôn do phần mềm chấm nhạc tự vẽ).", file=sys.stderr)
    low = args.input.lower()
    if low.endswith(_MIDI_EXT):
        kind = "midi"
    elif low.endswith(_XML_IN_EXT):
        kind = "xml"
    else:
        print("Không nhận ra đuôi file đầu vào (cần .mid/.midi/.xml/.musicxml/.mxl).", file=sys.stderr)
        return 1

    if not args.output:
        stem = os.path.splitext(args.input)[0]
        args.output = stem + ("_clean.mid" if kind == "midi" else "_clean.xml")
    if args.output.lower().endswith(".mxl"):
        print("Chưa ghi được .mxl. Hãy xuất .xml/.musicxml.", file=sys.stderr)
        return 1
    if kind == "xml" and args.output.lower().endswith(_MIDI_EXT):
        print("MusicXML -> MIDI chưa hỗ trợ. Hãy xuất .xml, hoặc mở bằng MuseScore để export MIDI.", file=sys.stderr)
        return 1

    if kind == "midi":
        return run_midi(args)
    return run_musicxml(args) or 0


if __name__ == "__main__":
    sys.exit(main())
