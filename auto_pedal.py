"""
auto_pedal.py - Pure-function automatic sustain pedal generator by measure.

Follows docs/AUTO_PEDAL_SPEC.md:
  * Pedal goes down at the start of each full measure.
  * Lifts just after the first note (anchor) of the next measure (split length d1),
    and presses again after a short release gap gap_s (default 80 ms), so the pedal
    indicator visibly lifts up and depresses ("nhấc lên đạp xuống").
  * Pickup measures at the start receive no pedal.
  * The last measure releases at its end.
  * If a measure has no anchor notes (rests), pedal changes at the bar line.
"""
from dataclasses import dataclass
from typing import List, Optional, Tuple

from midi_loader import RIGHT, Note

SUSTAIN = 64
DEFAULT_PEDAL_GAP = 0.08      # 80 ms release gap so the pedal visually lifts (~5 frames at 60 fps)


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


def build(song, gap_s: float = DEFAULT_PEDAL_GAP) -> Tuple[List[Tuple[float, int, int]], List[Tuple[float, float]]]:
    """
    Build (controls, pedal_spans) for auto pedal mode.
    controls: [(time, 64, 0 | 127)]
    spans: [(t_down, t_rel)] for display brackets
    gap_s: duration in seconds that the pedal stays UP during a measure transition
           (default 0.08s = 80ms), so the pedal indicator in the bottom-left corner
           visibly lifts up and depresses ("nhấc lên đạp xuống").
    """
    measures = measure_table(song)
    ms = [m for m in measures if not m.pickup]
    if not ms:
        return [], []

    notes = getattr(song, "notes", [])
    spans: List[Tuple[float, float]] = []
    events: List[Tuple[float, int, int]] = []

    t_down = ms[0].start
    for i, m in enumerate(ms):
        if i + 1 < len(ms):
            nxt = ms[i + 1]
            a = pick_anchor(notes, nxt)
            if a:
                q = song.quarter_length(a.start) if hasattr(song, "quarter_length") else 0.5
                d1, _ = split_lengths(a.duration, q)
                t_rel = min(a.start + d1, nxt.end)
                if t_rel <= t_down:
                    t_rel = nxt.start
            else:
                t_rel = nxt.start
        else:
            t_rel = m.end

        if t_rel <= t_down:
            t_rel = t_down + 1e-4

        spans.append((t_down, t_rel))
        events.append((t_down, SUSTAIN, 127))
        events.append((t_rel, SUSTAIN, 0))

        if i + 1 < len(ms):
            nxt = ms[i + 1]
            t_down = min(t_rel + max(0.0, gap_s), nxt.end - 0.01)
            if t_down <= t_rel:
                t_down = t_rel + 1e-4

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
