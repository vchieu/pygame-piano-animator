"""
auto_pedal.py - Pure-function automatic sustain pedal generator by measure.

Follows docs/AUTO_PEDAL_SPEC.md with musical left-hand chord change rules:
  * Pedal starts on the first full measure that has a left-hand attack on beat 1
    (lead-in/pickup measures starting with rests are skipped).
  * At measure boundaries, pedal is re-pressed ("nhấp nhả") ONLY when the new measure
    has a new left-hand attack on beat 1 (indicating a chord change).
  * If a measure has no new left-hand attack on beat 1 (left-hand note held over or
    resting), pedal is NOT re-pressed and remains sustained continuously across the bar line.
  * Re-press gap_s = 0.03s (30 ms) ensures old notes damp, new notes catch and resonate,
    and the pedal indicator in the bottom-left corner visibly lifts up and depresses ("nhấc lên đạp xuống").
"""
from dataclasses import dataclass
from typing import List, Optional, Tuple

from midi_loader import LEFT, RIGHT, Note

SUSTAIN = 64
DEFAULT_PEDAL_GAP = 0.03      # 30 ms release gap: damps old notes, catches new notes, animates pedal lift


@dataclass(frozen=True)
class PedalMeasure:
    start: float
    end: float
    number: int
    pickup: bool = False


def measure_table(song) -> List[PedalMeasure]:
    """
    Build measure table for auto pedalling, matching staff system bar boundaries.
    """
    bars = [float(t) for t in getattr(song, "bar_times", []) if t >= 0.0]
    bars = sorted(set(bars))
    if not bars:
        return []

    if bars[0] > 0.0:
        bars.insert(0, 0.0)
    if len(bars) == 1 or bars[-1] < song.duration:
        bars.append(max(bars[-1] + 2.0, song.duration))

    pickup_end = getattr(song, "pickup_end", 0.0)
    measures: List[PedalMeasure] = []
    for i in range(len(bars) - 1):
        st, en = bars[i], bars[i + 1]
        is_pickup = (i == 0 and pickup_end > 0.0 and en <= pickup_end + 1e-9)
        measures.append(PedalMeasure(start=st, end=en, number=i, pickup=is_pickup))

    return measures


def split_lengths(dur: float, quarter: float) -> Tuple[float, float]:
    """
    d1 = quarter / 4 if dur >= quarter else dur / 2.
    d2 = dur - d1.
    """
    q = quarter if quarter > 0.05 else 0.5
    d1 = q / 4.0 if dur >= q - 1e-9 else dur / 2.0
    return d1, max(0.0, dur - d1)


def pick_anchor(notes: List[Note], measure: PedalMeasure) -> Optional[Note]:
    """
    First onset note in measure (excluding grace notes and 0-duration notes).
    Ties broken deterministically by (hand != RIGHT, -pitch).
    """
    cand = [n for n in notes
            if measure.start <= n.start < measure.end
            and not getattr(n, "grace", False)
            and n.duration > 0.0]
    if not cand:
        return None
    t0 = min(n.start for n in cand)
    cluster = [n for n in cand if abs(n.start - t0) <= 1e-6]
    return sorted(cluster, key=lambda n: (n.hand != RIGHT, -n.pitch))[0]


def has_lh_attack(notes: List[Note], measure: PedalMeasure) -> bool:
    """
    True if measure has a new left-hand attack on beat 1 (indicating a chord change).
    """
    beat_win = 0.15      # 150 ms window at start of measure
    cand = [n for n in notes
            if measure.start <= n.start < measure.start + beat_win
            and not getattr(n, "grace", False)
            and n.duration > 0.0]
    if not cand:
        return False
    lh = [n for n in cand if n.hand == LEFT or (n.hand != RIGHT and n.pitch < 60)]
    if lh:
        return True
    # If the song has no left hand notes at all in the whole score, fall back to any beat 1 attack
    if not any(n.hand == LEFT or n.pitch < 60 for n in notes):
        return True
    return False


def build(song, gap_s: float = DEFAULT_PEDAL_GAP) -> Tuple[List[Tuple[float, int, int]], List[Tuple[float, float]]]:
    """
    Build (controls, pedal_spans) for auto pedal mode.
    controls: [(time, 64, 0 | 127)]
    spans: [(t_down, t_rel)] for display brackets
    """
    measures = measure_table(song)
    ms = [m for m in measures if not m.pickup]
    if not ms:
        return [], []

    notes = getattr(song, "notes", [])
    spans: List[Tuple[float, float]] = []
    events: List[Tuple[float, int, int]] = []

    t_down = None
    for i, m in enumerate(ms):
        if t_down is None:
            if has_lh_attack(notes, m):
                t_down = m.start
            else:
                continue

        nxt = ms[i + 1] if i + 1 < len(ms) else None
        if nxt and has_lh_attack(notes, nxt):
            a = pick_anchor(notes, nxt)
            if a:
                q = song.quarter_length(a.start) if hasattr(song, "quarter_length") else 0.5
                d1, _ = split_lengths(a.duration, q)
                t_rel = min(a.start + d1, nxt.end)
                if t_rel <= t_down:
                    t_rel = nxt.start
            else:
                t_rel = nxt.start

            if t_rel <= t_down:
                t_rel = t_down + 1e-4

            spans.append((t_down, t_rel))
            events.append((t_down, SUSTAIN, 127))
            events.append((t_rel, SUSTAIN, 0))

            t_down = min(t_rel + max(0.0, gap_s), nxt.end - 0.01)
            if t_down <= t_rel:
                t_down = t_rel + 1e-4
        elif not nxt:
            t_rel = m.end
            if t_rel <= t_down:
                t_rel = t_down + 1e-4
            spans.append((t_down, t_rel))
            events.append((t_down, SUSTAIN, 127))
            events.append((t_rel, SUSTAIN, 0))

    # Sort events by timestamp; for identical timestamp, UP (0) must precede DOWN (127)
    events.sort(key=lambda e: (e[0], e[2] != 0))

    # Compress adjacent identical values
    out = []
    state = None
    for t, c, v in events:
        if state != v:
            out.append((t, c, v))
            state = v

    return out, spans
