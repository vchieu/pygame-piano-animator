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
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

from midi_loader import LEFT, RIGHT, Note

SUSTAIN = 64
DEFAULT_PEDAL_GAP = 0.03      # 30 ms release gap: damps old notes, catches new notes, animates pedal lift
MIN_BAR_S = 0.05              # bar marks closer than this are one mark (float noise, repeat joins)
RELEASE_DELAY_MIN = 0.04      # the pedal lifts this long (at least) after the new bass note's attack
RELEASE_DELAY_MAX = 0.08      # ... and at most this long, however slow the tempo
FINAL_TAIL_S = 0.3            # the last chord rings this long after the last note ends
FINAL_TAIL_LIMIT_S = 0.45     # ... but never later than this after song.duration (Transport stops at +0.5 s)

# Harmony detection constants (section 11.1 of pedal fix plan)
W_MIN = 0.07                  # weight threshold: pitch class must hold 7% of window to count
REGISTER_GUARD = 7            # max semitones above lowest LH since press to be a bass candidate
MID_MIN_BEATS_DEFAULT = 2.0   # window must be at least 2 beats long
MID_MIN_GAP_BEATS = 1.0       # at least 1 beat after previous bass attack
MID_BEAT_EPS = 0.02           # float tolerance for gap guard


# --------------------------------------------------------------------------- #
# Harmony detection functions (section 11.1 of pedal fix plan)
# --------------------------------------------------------------------------- #

def pcs_of(notes, t0: float, t1: float,
           struck_from: Optional[float] = None) -> Tuple[Set[int], Set[int]]:
    """
    Compute (core_pcs, all_pcs) for notes sounding in [t0, t1).
    core_pcs: pitch classes holding at least W_MIN (7%) of the total overlap time.
    all_pcs: all pitch classes present in the window.
    """
    w: Dict[int, float] = {}
    for n in notes:
        if getattr(n, "grace", False) or n.duration <= 0:
            continue
        if struck_from is not None and n.start < struck_from:
            continue
        a, b = max(n.start, t0), min(n.end, t1)
        if b > a:
            pc = n.pitch % 12
            w[pc] = w.get(pc, 0.0) + (b - a)

    tot = sum(w.values()) or 1.0
    core = {pc for pc, v in w.items() if v / tot >= W_MIN}
    return core, set(w.keys())

def triad_on_bass(bass_pc: int, pcs: Set[int], strict: bool = True) -> Optional[Tuple[int, int, int]]:
    """
    Test whether pcs contains a major or minor triad rooted on bass_pc.
    strict=True: all three tones must be in pcs.
    strict=False: bass plus at least one other tone of the triad in pcs.
    """
    for intervals in ((0, 4, 7), (0, 3, 7)):
        triad = {(bass_pc + i) % 12 for i in intervals}
        if strict:
            if triad <= pcs:
                return intervals
        else:
            if bass_pc in pcs and len((triad - {bass_pc}) & pcs) >= 1:
                return intervals
    return None


def new_chord_on_bass(new_bass_pc: int, new_core: Set[int], old_all: Set[int],
                      strict: bool = False) -> bool:
    """
    Test (b) from section 11.1: is there a new chord on the new bass?
    strict=True (mid-bar): complete major or minor triad rooted on new_bass_pc in new_core.
    strict=False (bar line): root + at least one other tone of a triad in new_core.
    In both cases, the triad must bring at least one tone absent from old_all.
    """
    for intervals in ((0, 4, 7), (0, 3, 7)):
        triad = {(new_bass_pc + i) % 12 for i in intervals}
        if strict:
            ok = triad <= new_core
        else:
            ok = new_bass_pc in new_core and len((triad - {new_bass_pc}) & new_core) >= 1
        if ok and bool((triad & new_core) - old_all):
            return True
    return False


def is_bass_candidate(candidate: Note, lowest_lh_pitch: int, lh_in_window: List[Note], measure_end: float) -> bool:
    """
    Guards 1 and 2 from section 11.1:
    1. Register: candidate.pitch <= lowest LH pitch since press + 7 (a fifth)
    2. It's the bass of its window: no lower LH note struck later in (candidate.start + 0.04, measure_end)
    """
    if candidate.pitch > lowest_lh_pitch + REGISTER_GUARD:
        return False
    for n in lh_in_window:
        if candidate.start + 0.04 < n.start < measure_end and n.pitch < candidate.pitch:
            return False
    return True


def harmony_conflict(old_bass_pc: int, union_pcs: Set[int]) -> bool:
    """Test (a) from section 11.1: does the union contain a conflict relative to old_bass_pc?"""
    rel = {(p - old_bass_pc) % 12 for p in union_pcs}
    return (1 in rel) or (6 in rel) or (3 in rel and 4 in rel)


def harmony_verdict(old_bass_pc: int, old_core: Set[int], old_all: Set[int],
                    new_bass_pc: int, new_core: Set[int],
                    strict: bool = False, bass_move_changes_pedal: bool = False,
                    lh_pcs: Optional[Set[int]] = None,
                    old_lh_pcs: Optional[Set[int]] = None) -> Tuple[bool, str]:
    """
    Decide whether the harmony changes between the old pedal window and the new window.
    Returns (changed: bool, reason: str).
    """
    # (c) bass-only texture: new has no pitch class besides the new bass
    bass_only = len(new_core - {new_bass_pc}) == 0
    if bass_only:
        return new_bass_pc != old_bass_pc, "c:bass-only"
    union = old_core | new_core
    if lh_pcs is not None:                      # RH-only tones (passing / neighbour notes) are not harmony
        union = union & (lh_pcs | {old_bass_pc, new_bass_pc})
    if harmony_conflict(old_bass_pc, union):
        return True, "a:conflict"
    if new_chord_on_bass(new_bass_pc, new_core, old_all, strict=strict):
        return True, "b:new-chord-on-bass"
    # (b2) the bass moves, the complete triad rooted on the new bass is sounding now, and the old left hand
    # did not hold that complete triad: a new chord, even if the melody had touched its tones before.
    # (Inversions are safe: the triad rooted on an inverted bass is incomplete, e.g. Em/G has no D.)
    if old_lh_pcs is not None and new_bass_pc != old_bass_pc:
        for intervals in ((0, 4, 7), (0, 3, 7)):
            triad = {(new_bass_pc + i) % 12 for i in intervals}
            if triad <= new_core and not triad <= old_lh_pcs:
                return True, "b2:new-root-in-bass"
    # If bass changed and new has a chord-like set different from old
    if not strict and bass_move_changes_pedal and new_bass_pc != old_bass_pc:
        for intervals in ((0, 4, 7), (0, 3, 7)):
            triad = {(new_bass_pc + i) % 12 for i in intervals}
            if new_bass_pc in new_core and len((triad - {new_bass_pc}) & new_core) >= 1:
                if bool((triad & new_core) - old_all):
                    return True, "b:new-chord-on-bass"
    return False, "same"


def same_harmony(old_bass_pc: int, old_core: Set[int], old_all: Set[int],
                 new_bass_pc: int, new_core: Set[int],
                 strict: bool = False) -> bool:
    """
    Returns True if the harmony is the same (no pedal change needed).
    """
    changed, _ = harmony_verdict(old_bass_pc, old_core, old_all, new_bass_pc, new_core, strict=strict)
    return not changed


@dataclass(frozen=True)
class PedalMeasure:
    start: float
    end: float
    number: int
    pickup: bool = False


class _Index:
    """Notes sorted by start, with bisect windows for fast range queries."""
    def __init__(self, notes: List[Note]):
        self.notes = sorted(notes, key=lambda n: n.start)
        self.starts = [n.start for n in self.notes]
        self.max_dur = max((n.duration for n in self.notes), default=0.0)

    def __iter__(self):
        return iter(self.notes)

    def __len__(self):
        return len(self.notes)

    def starting(self, lo: float, hi: float) -> List[Note]:
        """Notes with lo <= start < hi."""
        a = bisect.bisect_left(self.starts, lo)
        b = bisect.bisect_left(self.starts, hi)
        return self.notes[a:b]

    def sounding(self, t0: float, t1: float) -> List[Note]:
        """Notes overlapping [t0, t1)."""
        a = bisect.bisect_left(self.starts, t0 - self.max_dur)
        b = bisect.bisect_left(self.starts, t1)
        return [n for n in self.notes[a:b] if n.end > t0]


def spans_from_controls(controls: list, end_time: Optional[float] = None) -> List[Tuple[float, float]]:
    """Convert MIDI CC64 controls into [(t_down, t_up)] spans for bracket drawing."""
    out = []
    down = None
    for t, c, v in controls:
        if c != SUSTAIN:
            continue
        if v >= 64 and down is None:
            down = t
        elif v < 64 and down is not None:
            if t > down:
                out.append((down, t))
            down = None
    if down is not None:
        t_end = end_time if end_time is not None and end_time > down else down + 0.5
        out.append((down, t_end))
    return out


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


def attack_cluster(lh, measure: PedalMeasure, q: float = 0.5) -> List[Note]:
    """
    The left-hand notes that start together at the measure's first beat: the earliest
    onset in [start - early, start + late), and the notes within 40 ms of it.
    Empty if the left hand does not attack there.
    """
    early, late = attack_window(q)
    lo, hi = measure.start - early, measure.start + late
    if hasattr(lh, "starting"):
        cand = lh.starting(lo, hi)
    else:
        cand = [n for n in lh if lo <= n.start < hi]
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


def harmony_signature(notes, t0: float, win: float = 1.0, tol: float = 0.1,
                      bass: Optional[Note] = None, filtered: bool = False) -> Optional[Tuple[int, FrozenSet[int]]]:
    """
    Extract (bass_pitch_class, frozenset_pcs) for LH/bass notes active/starting in [t0 - tol, t0 + win).
    `tol` accommodates human micro-timing (e.g. playing 40ms early before bar line).
    The bass is `bass` if provided; otherwise the lowest note STRUCK in the window (start >= t0 - tol);
    only if none starts there, the lowest note still ringing from before.
    """
    if filtered or hasattr(notes, "sounding"):
        valid = notes
    else:
        valid = lh_notes(notes)
    if not valid:
        return None

    if hasattr(valid, "sounding"):
        cand = valid.sounding(t0 - tol, t0 + win)
    else:
        cand = valid

    weights: dict = {}
    low_new: Optional[Note] = None      # lowest note struck in the window
    low_old: Optional[Note] = None      # lowest note ringing in from before it

    for n in cand:
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

    low_note = bass if bass is not None else (low_new if low_new is not None else low_old)
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


def mid_anchors(lh, measure: PedalMeasure, q: float = 0.5) -> List[Note]:
    """
    Lowest left-hand note of every onset cluster strictly inside the measure (after the
    bar-line attack window and before the next bar line's early window), in time order.
    """
    early, late = attack_window(q)
    lo, hi = measure.start + late, measure.end - early
    if hasattr(lh, "starting"):
        cand = lh.starting(lo, hi)
    else:
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


def lh_sounding(lh, t0: float, t1: float) -> bool:
    """True if any left-hand note sounds at some point in [t0, t1)."""
    if hasattr(lh, "sounding"):
        return bool(lh.sounding(t0, t1))
    return any(n.start < t1 and n.end > t0 for n in lh)


def build(song, gap_s: float = DEFAULT_PEDAL_GAP, max_hold_bars: int = 4,
          mid_bar: bool = True, mid_min_beats: float = 2.0,
          mid_min_gap_beats: float = MID_MIN_GAP_BEATS,
          bass_move_changes_pedal: bool = False) -> Tuple[List[Tuple[float, int, int]], List[Tuple[float, float]]]:
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
      * the window from the candidate to the bar end is at least mid_min_beats beats,
      * comes at least mid_min_gap_beats (1.0) beats after the previous bass attack,
      * passes the register guard (no more than 7 semitones above lowest LH since press),
      * and the harmony changes (both hands, per section 11.1 of the fix plan).

    bass_move_changes_pedal: if True, re-press whenever bass pitch class changes even
    within the same chord (for backward compatibility). Default False keeps pedal down
    on inversions (D1 = hold).
    """
    measures = measure_table(song)
    ms = [m for m in measures if not m.pickup]
    if not ms:
        return [], []

    notes = getattr(song, "notes", [])
    lh = lh_notes(notes)
    # Index for all notes (both hands) for harmony detection
    all_notes = [n for n in notes if not getattr(n, "grace", False) and n.duration > 0.0]
    all_idx = _Index(all_notes)
    lh_idx = _Index(lh)
    qlen = song.quarter_length if hasattr(song, "quarter_length") else (lambda t: 0.5)
    beats = sorted(getattr(song, "beat_times", []) or [])
    spans: List[Tuple[float, float]] = []
    events: List[Tuple[float, int, int]] = []

    def on_beat(t: float, q: float) -> bool:
        if not beats:
            return True
        i = bisect.bisect_left(beats, t)
        near = [beats[j] for j in (i - 1, i) if 0 <= j < len(beats)]
        gap = (beats[i] - beats[i - 1]) if 0 < i < len(beats) else q
        if gap < 0.01:
            gap = q
        tol = 0.25 * min(q, gap)
        return any(abs(b - t) <= tol + 1e-6 for b in near)

    def sig_at(a: Note, m: PedalMeasure, q: float):
        # harmony of the measure that starts with attack `a`; a long bass note (mid_bar on)
        # limits the window to that note so the next chord of the bar is not mixed in
        win = max(m.end - a.start, 0.25)
        if mid_bar and a.duration >= mid_min_beats * q:
            win = max(min(win, a.duration), q)
        tol = max(0.02, min(0.1, 0.15 * q))
        return harmony_signature(lh_idx, a.start, win=win, tol=tol, bass=a, filtered=True)

    # State: t_down (pedal press time), bass (current bass pitch), 
    # low (lowest LH pitch since press), t0 (bass attack time), held (bars count)
    st = {"t_down": None, "bass": None, "low": None, "t0": 0.0, "held": 0}

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
        # Update state with new bass
        st["t_down"] = t_rel + max(0.0, gap_s)
        st["bass"] = a.pitch
        st["low"] = a.pitch  # lowest since new press
        st["t0"] = a.start

    def let_go(t: float):
        """Lift the pedal at time t and leave it up until the left hand attacks again."""
        td = st["t_down"]
        t = max(t, td + 1e-4)
        spans.append((td, t))
        events.append((td, SUSTAIN, 127))
        events.append((t, SUSTAIN, 0))
        st.update(t_down=None, bass=None, low=None, held=0)

    for i, m in enumerate(ms):
        q = qlen(m.start)
        if st["t_down"] is None:
            cl = attack_cluster(lh_idx, m, q)
            if cl:
                a0 = _lowest(cl)
                st.update(t_down=m.start, bass=a0.pitch, low=a0.pitch, t0=a0.start, held=1)
            else:
                anchors = mid_anchors(lh_idx, m, q)
                if not anchors:
                    continue                      # lead-in measure / no left hand yet
                a0 = anchors[0]
                st.update(t_down=a0.start, bass=a0.pitch, low=a0.pitch, t0=a0.start, held=1)
                # don't continue - process mid_bar/end of song/bar line from here

        # ---- inside the measure: a new chord on a later beat (section 11.1) ----------
        if mid_bar:
            for a in mid_anchors(lh_idx, m, q):
                aq = qlen(a.start)
                if not on_beat(a.start, aq):
                    continue
                # If this note carries over the bar line and next has no LH attack, prefer holding
                if a.end >= m.end - 0.01:
                    nxt_m_cand = ms[i + 1] if i + 1 < len(ms) else None
                    if nxt_m_cand is not None:
                        nq2 = qlen(nxt_m_cand.start)
                        cln2 = attack_cluster(lh_idx, nxt_m_cand, nq2)
                        if not cln2:
                            continue
                # Guard 1: Register guard
                if st.get("low") is not None and a.pitch > st["low"] + REGISTER_GUARD:
                    continue
                # Guard 3: Window guard (from candidate to bar end is at least mid_min_beats beats)
                if m.end - a.start < mid_min_beats * aq - 0.02:
                    continue
                # Guard 4: Gap guard (at least mid_min_gap_beats beats after press / previous bass attack)
                if st.get("t0") is not None and a.start - st["t0"] < mid_min_gap_beats * aq - MID_BEAT_EPS:
                    continue
                # Guard 2: It is the bass of its own window (no lower LH note struck later in window)
                if any(n.pitch < a.pitch for n in lh_idx.sounding(a.start + 0.04, m.end)
                       if a.start + 0.04 < n.start < m.end):
                    continue
                # Compute old harmony from st["t0"] to a.start
                t0_start = st.get("t0", m.start)
                old_bass_pc = st["bass"] % 12
                new_bass_pc = a.pitch % 12
                lo_old = max(t0_start, m.start)
                old_core, old_all = pcs_of(all_idx.sounding(lo_old, a.start), lo_old, a.start,
                                           struck_from=lo_old - 0.05)
                _, old_lh = pcs_of(lh_idx.sounding(lo_old, a.start), lo_old, a.start,
                                   struck_from=lo_old - 0.05)
                old_lh = old_lh | {old_bass_pc}
                # Compute new harmony from a.start to m.end
                new_core, _ = pcs_of(all_idx.sounding(a.start - 0.04, m.end), a.start - 0.04, m.end,
                                     struck_from=a.start - 0.05)
                new_core = new_core | {new_bass_pc} | {n.pitch % 12 for n in lh_idx.starting(a.start - 0.05, a.start + 0.5 * (m.end - a.start))}
                lh_pcs = {n.pitch % 12 for n in lh_idx.sounding(lo_old, m.end)}
                changed, why = harmony_verdict(old_bass_pc, old_core, old_all, new_bass_pc, new_core, strict=True, bass_move_changes_pedal=False, lh_pcs=lh_pcs, old_lh_pcs=old_lh)
                if not changed:
                    continue
                press_change(a, m.end)
                st["held"] = 1
                st["low"] = min(st.get("low") if st.get("low") is not None else a.pitch, a.pitch)

        if st["t_down"] is not None and st.get("low") is not None:
            lh_in_bar = [n.pitch for n in lh_idx.sounding(st["t0"], m.end)
                         if st["t0"] - 0.05 <= n.start < m.end]
            if lh_in_bar:
                st["low"] = min(st["low"], min(lh_in_bar))

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
        cl = attack_cluster(lh_idx, nxt, nq)
        if not cl:                            # no new left-hand attack: keep holding ...
            if st["held"] >= max_hold_bars:   # ... but not forever
                anchors = mid_anchors(lh_idx, nxt, nq)
                if anchors:                   # refresh at the measure's first left-hand onset
                    a = anchors[0]
                    press_change(a, nxt.end)
                    st.update(t0=a.start, held=1)
                    continue
                if not lh_sounding(lh_idx, nxt.start, nxt.end):
                    let_go(nxt.start)         # left hand silent all measure: lift at the bar line
                    continue
            st["held"] += 1
            continue

        a = _lowest(cl)
        new_bass_pc = a.pitch % 12
        old_bass_pc = st["bass"] % 12
        t0_start = st.get("t0", m.start)
        # "old" harmony = the bar just finished (not everything since the press: that saturates)
        lo_old = max(t0_start, m.start)
        old_core, old_all = pcs_of(all_idx.sounding(lo_old, nxt.start), lo_old, nxt.start,
                                   struck_from=lo_old - 0.05)
        _, old_lh = pcs_of(lh_idx.sounding(lo_old, nxt.start), lo_old, nxt.start,
                           struck_from=lo_old - 0.05)
        old_lh = old_lh | {old_bass_pc}
        new_core, _ = pcs_of(all_idx.sounding(nxt.start - 0.04, nxt.end), nxt.start - 0.04, nxt.end,
                             struck_from=nxt.start - 0.05)
        # the LH tones of the bar's first half are always chord tones (short bass / arpeggio notes are
        # otherwise diluted below W_MIN by a busy RH, and the bass note itself can drop out)
        new_core = new_core | {n.pitch % 12 for n in lh_idx.starting(nxt.start - 0.05, nxt.start + 0.5 * (nxt.end - nxt.start))} | {new_bass_pc}
        lh_pcs = {n.pitch % 12 for n in lh_idx.sounding(lo_old, nxt.end)}
        changed, why = harmony_verdict(old_bass_pc, old_core, old_all, new_bass_pc, new_core, strict=False, bass_move_changes_pedal=bass_move_changes_pedal, lh_pcs=lh_pcs, old_lh_pcs=old_lh)

        if not changed and st["held"] >= max_hold_bars:
            changed = True
        if not changed and bass_move_changes_pedal and new_bass_pc != old_bass_pc:
            changed = True

        if not changed:
            st["held"] += 1
            st["low"] = min(st.get("low") if st.get("low") is not None else a.pitch, a.pitch)
            continue

        press_change(a, nxt.end)
        st.update(t0=a.start, held=1)
        st["low"] = min(st.get("low") if st.get("low") is not None else a.pitch, a.pitch)

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
