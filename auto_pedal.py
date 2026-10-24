"""
auto_pedal.py - Pure-function automatic sustain pedal generator by measure.

Follows docs/AUTO_PEDAL_SPEC.md with musical chord change rules:
  * Pedal starts on the first full measure that has a left-hand attack on beat 1
    (lead-in/pickup measures starting with rests are skipped).
  * At measure boundaries, pedal is re-pressed ("nhấp nhả") ONLY when the new measure
    has a new left-hand attack on beat 1 (indicating a chord change).
  * If a measure has no new left-hand attack on beat 1 (left-hand note held over or
    resting), pedal is NOT re-pressed and remains sustained continuously across the bar line.
  * Re-press gap_s = 0.03s (30 ms) ensures old notes damp, new notes catch and resonate,
    and the pedal indicator in the bottom-left corner visibly lifts up and depresses
    ("nhấc lên đạp xuống").
  * Anchor selection prioritizes left-hand bass notes and clamps d1 for short/staccato
    notes so re-press happens before the note ends.
  * "Chord change" means the left-hand harmony really changed (bass pitch class, or a
    different pitch-class set over the same bass), judged by harmony_signature/
    harmony_changed over the whole measure - repeated chords and Alberti patterns
    keep the pedal down; a new bass note or chord colour re-presses it.
  * mid_bar (default on): the pedal can also change INSIDE a measure, on a beat where the
    left hand plays a new long bass note (>= mid_min_beats) and the harmony really changes
    (pitch-class sets of the chord before and after differ). Short bass notes (Alberti,
    walking bass) never trigger it.
  * Provides helper functions for harmony detection and pedal analysis.
"""
import bisect
from dataclasses import dataclass
from typing import FrozenSet, List, Optional, Tuple

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
        bars.append(max(bars[-1], getattr(song, "duration", bars[-1])))

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


def attack_window(q: float) -> Tuple[float, float]:
    """(early, late) seconds around a bar line in which a note counts as 'on beat 1'.
    Scales with the beat (0.1 s early / 0.15 s late at 120 bpm)."""
    q = q if q > 0.05 else 0.5
    return 0.2 * q, 0.3 * q


def attack_cluster(lh: List[Note], measure: PedalMeasure, q: float = 0.5) -> List[Note]:
    """
    The left-hand notes that start together at the measure's first beat: the earliest
    onset in [start - early, start + late), and the notes within 40 ms of it.
    Empty if the left hand does not attack there.
    """
    early, late = attack_window(q)
    cand = [n for n in lh if measure.start - early <= n.start < measure.start + late]
    if not cand:
        return []
    t0 = min(n.start for n in cand)
    return [n for n in cand if n.start - t0 <= 0.04]


def pick_anchor(notes: List[Note], measure: PedalMeasure, q: float = 0.5) -> Optional[Note]:
    """
    The note whose attack the pedal change is hung on: the lowest left-hand note of the
    cluster at the measure's first beat (same window has_lh_attack uses, so an attack
    played a little early still counts). If the left hand does not attack there, the
    first onset of the measure (grace and 0-length notes excluded).
    """
    cl = attack_cluster(lh_notes(notes), measure, q)
    if cl:
        return min(cl, key=lambda n: (n.hand != LEFT, n.pitch))
    cand = [n for n in notes
            if measure.start <= n.start < measure.end
            and not getattr(n, "grace", False)
            and n.duration > 0.0]
    if not cand:
        return None
    t0 = min(n.start for n in cand)
    cluster = [n for n in cand if abs(n.start - t0) <= 1e-6]
    return sorted(cluster, key=lambda n: (n.hand != LEFT, n.pitch))[0]


def get_bass_pitch_class(notes: List[Note], t0: float, tol: float = 0.15) -> Optional[int]:
    """
    Get the pitch class of the lowest left-hand / bass note that STARTS near t0.
    Returns pitch class (0-11) of the lowest attack near t0, or None if no LH attack found.
    """
    # Find notes that start near t0 and are LH or bass (pitch < 60)
    attack_notes = []
    for n in notes:
        if getattr(n, "grace", False) or n.duration <= 0.0:
            continue
        # Check if this note starts near t0 (indicating a new attack)
        if t0 - tol <= n.start <= t0 + tol:
            # Check if note is LH or bass area
            is_lh = n.hand == LEFT or (n.hand != RIGHT and n.pitch < 60)
            if is_lh:
                attack_notes.append(n)
    
    if not attack_notes:
        return None
    
    # Return pitch class of the lowest bass attack
    lowest = min(attack_notes, key=lambda n: n.pitch)
    return lowest.pitch % 12


def lh_notes(notes: List[Note]) -> List[Note]:
    """
    Filter notes for left-hand / bass notes.
    Includes LEFT hand notes and any note with pitch < 60 (bass area).
    """
    lh = [n for n in notes
          if not getattr(n, "grace", False)
          and n.duration > 0.0
          and (n.hand == LEFT or n.pitch < 60)]
    if lh:
        return lh
    # If no left-hand / low-pitch notes exist in score, return all notes
    return [n for n in notes if not getattr(n, "grace", False) and n.duration > 0.0]


def harmony_signature(notes: List[Note], t0: float, win: float = 1.0, tol: float = 0.1) -> Optional[Tuple[int, FrozenSet[int]]]:
    """
    Extract (bass_pitch_class, frozenset_pcs) for LH/bass notes active/starting in [t0 - tol, t0 + win).
    `tol` accommodates human micro-timing (e.g. playing 40ms early before bar line).
    """
    valid = lh_notes(notes)
    if not valid:
        return None

    weights: dict = {}
    low_note: Optional[Note] = None

    for n in valid:
        a = max(n.start, t0 - tol)
        b = min(n.start + n.duration, t0 + win)
        if b <= a or n.start >= t0 + win:
            continue
        pc = n.pitch % 12
        dur = b - a
        weights[pc] = weights.get(pc, 0.0) + dur
        if low_note is None or n.pitch < low_note.pitch:
            low_note = n

    if not weights or low_note is None:
        return None

    total_weight = sum(weights.values())
    pcs = {pc for pc, w in weights.items() if (w / total_weight) >= 0.12}
    pcs.add(low_note.pitch % 12)
    return (low_note.pitch % 12, frozenset(pcs))


def harmony_changed(cur_sig: Optional[Tuple[int, FrozenSet[int]]], new_sig: Optional[Tuple[int, FrozenSet[int]]]) -> bool:
    """
    Returns True if harmony changed between cur_sig and new_sig.
    """
    if cur_sig is None:
        return True
    if new_sig is None:
        return False
    # Bass pitch class changed?
    if new_sig[0] != cur_sig[0]:
        return True
    # Pitch class set Jaccard similarity <= 0.5 (same bass but different chord)
    inter = len(cur_sig[1] & new_sig[1])
    union = len(cur_sig[1] | new_sig[1])
    if union == 0:
        return True
    return (inter / union) <= 0.5


def pcs_changed(cur_sig, new_sig, threshold: float = 0.5) -> bool:
    """
    Mid-bar test: did the chord colour change? Compares only the pitch-class SETS
    (the bass alone moving root -> fifth inside one chord is not a change).
    Jaccard similarity <= threshold means changed.
    """
    if cur_sig is None or new_sig is None:
        return False
    union = len(cur_sig[1] | new_sig[1])
    if union == 0:
        return False
    return len(cur_sig[1] & new_sig[1]) / union <= threshold


def mid_anchors(lh: List[Note], measure: PedalMeasure, q: float = 0.5) -> List[Note]:
    """
    Lowest left-hand note of every onset cluster strictly inside the measure (after the
    bar-line attack window and before the next bar line's early window), in time order.
    """
    early, late = attack_window(q)
    lo, hi = measure.start + late, measure.end - early
    cand = sorted((n for n in lh if lo <= n.start < hi), key=lambda n: n.start)
    out: List[Note] = []
    i = 0
    while i < len(cand):
        t0 = cand[i].start
        cl = [n for n in cand[i:] if n.start - t0 <= 0.04]
        i += len(cl)
        out.append(min(cl, key=lambda n: (n.hand != LEFT, n.pitch)))
    return out


def has_lh_attack(notes: List[Note], measure: PedalMeasure, q: float = 0.5) -> bool:
    """True if the left hand (or bass area) attacks near the measure's first beat."""
    return bool(attack_cluster(lh_notes(notes), measure, q))


def build(song, gap_s: float = DEFAULT_PEDAL_GAP, max_hold_bars: int = 4,
          mid_bar: bool = True, mid_min_beats: float = 2.0,
          mid_min_gap_beats: float = 1.5) -> Tuple[List[Tuple[float, int, int]], List[Tuple[float, float]]]:
    """
    Build (controls, pedal_spans) for auto pedal mode.
    controls: [(time, 64, 0 | 127)]
    spans: [(t_down, t_rel)] for display brackets

    The pedal goes down at the first measure the left hand attacks on beat 1. At each
    later bar line it is re-pressed only if the left hand attacks there AND the harmony
    changed (or the pedal has been held `max_hold_bars` bars): released just after the
    bass note's attack (d1 later), pressed again `gap_s` after that.

    mid_bar: also re-press inside a measure, at a left-hand bass note that
      * lies on a beat (when the song has beat_times),
      * lasts at least `mid_min_beats` beats (so passing notes / Alberti never count),
      * comes at least `mid_min_gap_beats` beats after the last press, and
      * changes the chord colour (pcs_changed) against the chord pedalled so far.
    """
    measures = measure_table(song)
    ms = [m for m in measures if not m.pickup]
    if not ms:
        return [], []

    notes = getattr(song, "notes", [])
    lh = lh_notes(notes)
    qlen = song.quarter_length if hasattr(song, "quarter_length") else (lambda t: 0.5)
    beats = sorted(getattr(song, "beat_times", []) or [])
    spans: List[Tuple[float, float]] = []
    events: List[Tuple[float, int, int]] = []

    def on_beat(t: float, q: float) -> bool:
        if not beats:
            return True
        i = bisect.bisect_left(beats, t)
        near = [beats[j] for j in (i - 1, i) if 0 <= j < len(beats)]
        return any(abs(b - t) <= 0.25 * q for b in near)

    def sig_at(a: Note, m: PedalMeasure, q: float):
        # harmony of the measure that starts with attack `a`; a long bass note (mid_bar on)
        # limits the window to that note so the next chord of the bar is not mixed in
        win = max(m.end - a.start, 0.25)
        if mid_bar and a.duration >= mid_min_beats * q:
            win = max(min(win, a.duration), q)
        return harmony_signature(lh, a.start, win=win, tol=0.1)

    st = {"t_down": None, "sig": None, "t0": 0.0, "held": 0}

    def press_change(a: Note, bound_end: float):
        """Release the pedal just after bass note `a` sounds, press it again gap_s later."""
        td = st["t_down"]
        d1, _ = split_lengths(a.duration, qlen(a.start))
        d1 = min(d1, max(0.005, a.duration - gap_s - 0.005))   # re-press before the note ends
        t_rel = min(a.start + d1, bound_end)
        if t_rel <= td:
            t_rel = max(a.start, td + 1e-4)
        spans.append((td, t_rel))
        events.append((td, SUSTAIN, 127))
        events.append((t_rel, SUSTAIN, 0))
        nd = min(t_rel + max(0.0, gap_s), bound_end - 0.01)
        if nd <= t_rel:
            nd = t_rel + 1e-4
        st["t_down"] = nd

    for i, m in enumerate(ms):
        q = qlen(m.start)
        if st["t_down"] is None:
            cl = attack_cluster(lh, m, q)
            if not cl:
                continue                      # lead-in measure / no left hand yet
            a0 = min(cl, key=lambda n: (n.hand != LEFT, n.pitch))
            st.update(t_down=m.start, sig=sig_at(a0, m, q), t0=a0.start, held=1)

        # ---- inside the measure: a new chord on a later beat -------------------------
        if mid_bar:
            for a in mid_anchors(lh, m, q):
                aq = qlen(a.start)
                if a.duration < mid_min_beats * aq or not on_beat(a.start, aq):
                    continue
                if a.start - st["t_down"] < mid_min_gap_beats * aq:
                    continue
                c0 = max(st["t0"], a.start - (m.end - m.start))
                cur = harmony_signature(lh, c0, win=max(a.start - c0 - 0.01, 0.05), tol=0.05)
                new = harmony_signature(lh, a.start, win=max(min(a.duration, m.end - a.start), aq), tol=0.05)
                if not pcs_changed(cur, new):
                    continue
                press_change(a, m.end)
                st.update(sig=new, t0=a.start, held=1)

        nxt = ms[i + 1] if i + 1 < len(ms) else None
        if nxt is None:
            song_dur = getattr(song, "duration", m.end)
            t_rel = min(m.end, song_dur + 0.3)
            td = st["t_down"]
            if t_rel <= td:
                t_rel = td + 1e-4
            spans.append((td, t_rel))
            events.append((td, SUSTAIN, 127))
            events.append((t_rel, SUSTAIN, 0))
            break

        # ---- bar line: only a new left-hand attack with a changed harmony ------------
        nq = qlen(nxt.start)
        cl = attack_cluster(lh, nxt, nq)
        if not cl:                            # no new left-hand attack: keep holding
            st["held"] += 1
            continue

        a = min(cl, key=lambda n: (n.hand != LEFT, n.pitch))
        new_sig = sig_at(a, nxt, nq)
        if not (harmony_changed(st["sig"], new_sig) or st["held"] >= max_hold_bars):
            st["held"] += 1                   # same chord again: keep holding
            continue

        press_change(a, nxt.end)
        if new_sig is not None:
            st["sig"] = new_sig
        st.update(t0=a.start, held=1)

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
