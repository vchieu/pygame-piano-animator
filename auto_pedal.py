"""
auto_pedal.py - Pure-function automatic sustain pedal generator, following the left hand's harmony.

Follows docs/AUTO_PEDAL_SPEC.md:
  * Pedal starts on the first full measure that has a left-hand attack on beat 1
    (lead-in/pickup measures starting with rests are skipped).
  * At measure boundaries the pedal is re-pressed ONLY when the new measure has a new
    left-hand attack on beat 1 AND the left-hand harmony really changed (bass pitch class,
    or a different pitch-class set over the same bass), or the same chord was held for
    `max_hold_bars` measures. Repeated chords and Alberti patterns keep the pedal down.
  * A measure with no new left-hand attack on beat 1 (note held over, or resting) keeps the
    pedal down - but never longer than `max_hold_bars` measures: at the limit the pedal is
    refreshed at the measure's first left-hand onset, or let go at the bar line if the left
    hand is completely silent there (it goes down again at the next left-hand attack).
  * Change point: the pedal lifts `release_delay(q)` (40-80 ms) after the new bass note's
    attack and goes down again `gap_s` (30 ms) later, so the old chord is damped, the new bass
    note is caught by the new pedal, and the pedal indicator visibly lifts and depresses.
    The gap is exact: the lift moves earlier instead of the gap shrinking.
  * mid_bar (default on): the pedal can also change INSIDE a measure, on a beat where the
    left hand plays a new long bass note (>= mid_min_beats) and the harmony really changes
    (pitch-class sets of the chord before and after differ). Short bass notes (Alberti,
    walking bass) never trigger it.
  * The last measure lets go `FINAL_TAIL_S` after the music ends, so the last chord rings out.
  * Provides helper functions for harmony detection and pedal analysis.
"""
import bisect
from dataclasses import dataclass
from typing import FrozenSet, List, Optional, Tuple

from midi_loader import LEFT, RIGHT, Note

SUSTAIN = 64
DEFAULT_PEDAL_GAP = 0.03      # 30 ms release gap: damps old notes, catches new notes, animates pedal lift
MIN_BAR_S = 0.05              # bar marks closer than this are one mark (float noise, repeat joins)
RELEASE_DELAY_MIN = 0.04      # the pedal lifts this long (at least) after the new bass note's attack
RELEASE_DELAY_MAX = 0.08      # ... and at most this long, however slow the tempo
FINAL_TAIL_S = 0.3            # the last chord rings this long after the last note ends
FINAL_TAIL_LIMIT_S = 0.45     # ... but never later than this after song.duration (Transport stops at +0.5 s)


@dataclass(frozen=True)
class PedalMeasure:
    start: float
    end: float
    number: int
    pickup: bool = False


def measure_table(song) -> List[PedalMeasure]:
    """
    Build measure table for auto pedalling, matching staff system bar boundaries.
    Bar marks closer than MIN_BAR_S are merged so no near-zero-length measure appears.
    """
    raw = sorted({float(t) for t in getattr(song, "bar_times", []) if t >= 0.0})
    if not raw:
        return []

    bars = [raw[0]]
    for t in raw[1:]:
        if t - bars[-1] >= MIN_BAR_S:
            bars.append(t)

    if bars[0] >= MIN_BAR_S:
        bars.insert(0, 0.0)
    else:
        bars[0] = 0.0

    dur = float(getattr(song, "duration", bars[-1]))
    if dur - bars[-1] >= MIN_BAR_S:
        bars.append(dur)
    elif bars[-1] < dur and len(bars) > 1:
        bars[-1] = dur                         # a last mark just before the end is the end

    pickup_end = getattr(song, "pickup_end", 0.0)
    measures: List[PedalMeasure] = []
    for i in range(len(bars) - 1):
        st, en = bars[i], bars[i + 1]
        is_pickup = (i == 0 and pickup_end > 0.0 and en <= pickup_end + 1e-9)
        measures.append(PedalMeasure(start=st, end=en, number=i, pickup=is_pickup))

    return measures


def release_delay(quarter: float) -> float:
    """
    How long after a new bass note's attack the pedal lifts: an eighth of a beat, kept
    between 40 and 80 ms. Long enough for the new note to be struck before the old chord is
    damped, short enough that the old chord does not smear into the new one at slow tempi.
    """
    q = quarter if quarter > 0.05 else 0.5
    return min(max(q / 8.0, RELEASE_DELAY_MIN), RELEASE_DELAY_MAX)


def attack_window(q: float) -> Tuple[float, float]:
    """(early, late) seconds around a bar line in which a note counts as 'on beat 1'.
    Scales with the beat (0.1 s early / 0.15 s late at 120 bpm)."""
    q = q if q > 0.05 else 0.5
    return 0.2 * q, 0.3 * q


def _lowest(cluster: List[Note]) -> Note:
    """The bass of a cluster: the lowest note, a LEFT-hand note first."""
    return min(cluster, key=lambda n: (n.hand != LEFT, n.pitch))


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
        return _lowest(cl)
    cand = [n for n in notes
            if measure.start <= n.start < measure.end
            and not getattr(n, "grace", False)
            and n.duration > 0.0]
    if not cand:
        return None
    t0 = min(n.start for n in cand)
    cluster = [n for n in cand if abs(n.start - t0) <= 1e-6]
    return _lowest(cluster)


def is_lh_note(n: Note) -> bool:
    """A LEFT-hand note, or a note of no assigned hand in the bass area (pitch < 60).
    A note the score gives to the RIGHT hand is never bass, however low it goes."""
    return n.hand == LEFT or (n.hand != RIGHT and n.pitch < 60)


def lh_notes(notes: List[Note]) -> List[Note]:
    """
    Filter notes for left-hand / bass notes (see is_lh_note), grace and 0-length notes out.
    """
    lh = [n for n in notes
          if not getattr(n, "grace", False)
          and n.duration > 0.0
          and is_lh_note(n)]
    if lh:
        return lh
    # If no left-hand / low-pitch notes exist in score, return all notes
    return [n for n in notes if not getattr(n, "grace", False) and n.duration > 0.0]


def harmony_signature(notes: List[Note], t0: float, win: float = 1.0, tol: float = 0.1) -> Optional[Tuple[int, FrozenSet[int]]]:
    """
    Extract (bass_pitch_class, frozenset_pcs) for LH/bass notes active/starting in [t0 - tol, t0 + win).
    `tol` accommodates human micro-timing (e.g. playing 40ms early before bar line).
    The bass is the lowest note STRUCK in the window (start >= t0 - tol); only if none starts
    there, the lowest note still ringing from before. So an older, lower bass note that is
    still sounding does not hide the new bass.
    """
    valid = lh_notes(notes)
    if not valid:
        return None

    weights: dict = {}
    low_new: Optional[Note] = None      # lowest note struck in the window
    low_old: Optional[Note] = None      # lowest note ringing in from before it

    for n in valid:
        a = max(n.start, t0 - tol)
        b = min(n.start + n.duration, t0 + win)
        if b <= a or n.start >= t0 + win:
            continue
        pc = n.pitch % 12
        dur = b - a
        weights[pc] = weights.get(pc, 0.0) + dur
        if n.start >= t0 - tol:
            if low_new is None or n.pitch < low_new.pitch:
                low_new = n
        elif low_old is None or n.pitch < low_old.pitch:
            low_old = n

    low_note = low_new if low_new is not None else low_old
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
        out.append(_lowest(cl))
    return out


def has_lh_attack(notes: List[Note], measure: PedalMeasure, q: float = 0.5) -> bool:
    """True if the left hand (or bass area) attacks near the measure's first beat."""
    return bool(attack_cluster(lh_notes(notes), measure, q))


def lh_sounding(lh: List[Note], t0: float, t1: float) -> bool:
    """True if any left-hand note sounds at some point in [t0, t1)."""
    return any(n.start < t1 and n.end > t0 for n in lh)


def build(song, gap_s: float = DEFAULT_PEDAL_GAP, max_hold_bars: int = 4,
          mid_bar: bool = True, mid_min_beats: float = 2.0,
          mid_min_gap_beats: float = 1.5) -> Tuple[List[Tuple[float, int, int]], List[Tuple[float, float]]]:
    """
    Build (controls, pedal_spans) for auto pedal mode.
    controls: [(time, 64, 0 | 127)]
    spans: [(t_down, t_rel)] for display brackets

    The pedal goes down at the first measure the left hand attacks on beat 1. At each
    later bar line it is re-pressed only if the left hand attacks there AND the harmony
    changed (or the pedal has been held `max_hold_bars` bars): released release_delay()
    after the bass note's attack, pressed again exactly `gap_s` after that.

    A bar line without a left-hand attack keeps the pedal, up to `max_hold_bars` bars; then
    it is refreshed at the measure's first left-hand onset, or - if the left hand is silent
    for the whole measure - let go at the bar line and pressed again at the next attack.

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
        """Release the pedal just after bass note `a` sounds, press it again exactly gap_s later."""
        td = st["t_down"]
        d1 = release_delay(qlen(a.start))
        d1 = min(d1, max(0.005, a.duration - gap_s - 0.005))   # re-press before the note ends
        # the lift moves earlier (never the gap shorter) when the bar's end is close
        t_rel = min(a.start + d1, bound_end - max(0.0, gap_s) - 0.01)
        if t_rel <= td:
            t_rel = max(a.start, td + 1e-4)
        spans.append((td, t_rel))
        events.append((td, SUSTAIN, 127))
        events.append((t_rel, SUSTAIN, 0))
        st["t_down"] = t_rel + max(0.0, gap_s)

    def let_go(t: float):
        """Lift the pedal at time t and leave it up until the left hand attacks again."""
        td = st["t_down"]
        t = max(t, td + 1e-4)
        spans.append((td, t))
        events.append((td, SUSTAIN, 127))
        events.append((t, SUSTAIN, 0))
        st.update(t_down=None, sig=None, held=0)

    for i, m in enumerate(ms):
        q = qlen(m.start)
        if st["t_down"] is None:
            cl = attack_cluster(lh, m, q)
            if not cl:
                anchors = mid_anchors(lh, m, q)
                if anchors:
                    a0 = anchors[0]
                    st.update(t_down=m.start, sig=sig_at(a0, m, q), t0=a0.start, held=1)
                    continue
                continue                      # lead-in measure / no left hand yet
            a0 = _lowest(cl)
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
            song_dur = float(getattr(song, "duration", m.end))
            last_end = max((n.end for n in notes), default=song_dur)
            # let the last chord ring: FINAL_TAIL_S after the music ends (not past the playback end)
            t_rel = max(min(m.end, song_dur), last_end) + FINAL_TAIL_S
            t_rel = min(t_rel, song_dur + FINAL_TAIL_LIMIT_S)
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
        if not cl:                            # no new left-hand attack: keep holding ...
            if st["held"] >= max_hold_bars:   # ... but not forever
                anchors = mid_anchors(lh, nxt, nq)
                if anchors:                   # refresh at the measure's first left-hand onset
                    a = anchors[0]
                    press_change(a, nxt.end)
                    sig = sig_at(a, nxt, nq)
                    if sig is not None:
                        st["sig"] = sig
                    st.update(t0=a.start, held=1)
                    continue
                if not lh_sounding(lh, nxt.start, nxt.end):
                    let_go(nxt.start)         # left hand silent all measure: lift at the bar line
                    continue
            st["held"] += 1
            continue

        a = _lowest(cl)
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
