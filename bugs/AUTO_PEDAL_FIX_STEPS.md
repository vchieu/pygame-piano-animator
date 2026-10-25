# Auto-pedal fix — step-by-step task list for a smaller model

**Read this whole file first, then do the steps strictly in order. After each step: run the tests, run the check script, compare with the expected numbers, and only then continue. Do not change anything that is not in a step.**

Target file: `auto_pedal.py` (function `build()` and helpers). The diffs below were produced against the exact `auto_pedal.py`
that was reviewed; they were applied and verified (tests + check script). If a diff does not apply cleanly (your file moved on),
apply the *intent* described under "What it does" by hand, keeping the same variable names.

## Rules (from CLAUDE.md)
- Do NOT edit `version.py` or `CHANGELOG.md`.
- Behaviour changes -> update `docs/ARCHITECTURE.md` (auto-pedal section) and `AUTO_PEDAL_SPEC.md` (see "Docs" at the end).
- Tests go into `tests/test_auto_pedal.py`; they must be fast, headless, deterministic.
- Do not change the public signature of `build(...)`; `harmony_verdict` gets two **optional** keyword parameters (Step 4), old callers keep working.
- Existing tests must keep passing. Especially `test_inversion_holds_pedal`, `test_arpeggio_top_note_is_not_an_anchor`,
  `test_short_bass_two_chords_per_bar`, the Alberti tests.

## Background (why)
Auto-pedal follows the left-hand (LH) bass harmony. On real scores it **smears harmony**: the pedal is held over several bars although
the LH chord changes every bar (and sometimes twice per bar), and it also **cuts a sustained chord for no reason**. Causes, all in
`auto_pedal.py`:
1. `new_core` is computed over the whole next bar and keeps only pitch classes with >= 7 % of the time (`W_MIN`). Short LH bass /
   arpeggio notes are diluted by a busy right hand (RH), so the new bass note itself may not even be in `new_core`.
2. `old_all` is accumulated since the press (`st["t0"]`), so after 1-2 bars of a diatonic piece it contains every pitch class and the rule
   "a new chord must bring a tone absent from `old_all`" can never fire again.
3. A bass root that moves to a pitch class the melody had already touched (A -> F#m with F#4 in the melody) is judged "no new tone".
4. `harmony_conflict` counts RH passing / neighbour tones (e.g. G#4 over a D chord = tritone) as a harmonic change, so a sustained chord is re-pressed.
The forced re-press at `max_hold_bars` (4) is the only thing that ended the long smears.

## Test material
- Piece A: `anh-dau-tu-luc-em-di.musicxml` (D major, 4/4, 41 bars).
- Piece B: `Khi-nguoi-lon-co-don.musicxml` (A major, 3/4, 101 bars, one chord per bar).

## The check script (create it as `tools/pedal_check.py`, no other changes to the repo)
```python
"""tools/pedal_check.py - print how auto-pedal behaves on a score.  Usage (from the repo root):
    python tools/pedal_check.py score1.musicxml [score2.musicxml ...]
Prints: number of pedal spans, spans that cover >= 3 bars, mid-bar presses, and bar lines where the
first left-hand bass note changes pitch class but the pedal was NOT re-pressed (candidates for 'smeared harmony')."""
import bisect, contextlib, io, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import auto_pedal
import midi_loader

NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']

for path in sys.argv[1:]:
    with contextlib.redirect_stdout(io.StringIO()):
        song = midi_loader.load_song(path)
    bars, beats = song.bar_times, song.beat_times
    _, spans = auto_pedal.build(song)

    def pos(t):                                  # (bar, beat) of time t, both 1-based
        b = bisect.bisect_right(bars, t + 1e-6)
        k = bisect.bisect_right(beats, t + 1e-6) - 1
        k0 = bisect.bisect_right(beats, bars[b - 1] + 1e-6) - 1
        return b, k - k0 + 1
    long_ = ["%d.%d->%d.%d" % (*pos(a), *pos(b)) for a, b in spans if pos(b)[0] - pos(a)[0] >= 3]
    mid = ["%d.%d" % pos(a) for a, _ in spans if pos(a)[1] != 1]
    downs = [a for a, _ in spans]
    missed, prev = [], None
    for i in range(1, len(bars)):
        lo = bars[i - 1]
        lh = [n for n in song.notes if lo - 0.01 <= n.start < lo + 0.15 and str(n.hand).upper().startswith("L")]
        if not lh:
            prev = None
            continue
        bass = min(lh, key=lambda n: n.pitch).pitch % 12
        if prev is not None and bass != prev and not any(lo - 0.02 <= t <= lo + 0.2 for t in downs):
            missed.append("%d:%s->%s" % (i, NAMES[prev], NAMES[bass]))
        prev = bass
    print(os.path.basename(path))
    print("  spans:", len(spans))
    print("  spans covering >= 3 bars:", long_)
    print("  mid-bar presses (%d):" % len(mid), mid)
    print("  bass change at bar line without a re-press:", missed)
```
Run before starting (baseline) and after each step: `python tools/pedal_check.py <pieceA.musicxml> <pieceB.musicxml>`.
Baseline you should see: A: 36 spans, B: 74 spans; A has 10 bar lines with a bass change and no re-press; B has 5.

---
## Step 1 — delete the orphaned dead block (no behaviour change)
After `pcs_of` there is a stray docstring + `rel = ...` + `return ...` with no `def` (a leftover duplicate of `harmony_conflict`).
Apply:
```diff
--- a/auto_pedal.py
+++ b/auto_pedal.py
@@ -73,14 +73,6 @@
 
 
 
-    """
-    Test (a) from section 11.1: does the union contain a conflict relative to old_bass_pc?
-    A conflict is: minor second (1), tritone (6), or both minor and major third (3 and 4).
-    """
-    rel = {(pc - old_bass_pc) % 12 for pc in union_pcs}
-    return (1 in rel) or (6 in rel) or (3 in rel and 4 in rel)
-
-
 def triad_on_bass(bass_pc: int, pcs: Set[int], strict: bool = True) -> Optional[Tuple[int, int, int]]:
     """
     Test whether pcs contains a major or minor triad rooted on bass_pc.
```
Expected: tests unchanged (all pass), check-script numbers identical to the baseline.
Do NOT delete other "unused" helpers in this task (tests may import them).

## Step 2 — judge the bar line on the bar that just finished, and keep the new LH tones as chord tones
What it does (bar-line branch of `build()`):
- `old_core` / `old_all` are computed over the **last bar only** (`lo_old = max(t0_start, m.start)`), not since the press -> fixes cause 2.
- `new_core` always contains the new bass pitch class and the pitch classes of the LH notes struck in the **first half of the next bar**
  -> fixes cause 1 (and a bar with two chords still works: the first chord is judged on the first half).
- `old_lh` (LH pitch classes of the old bar + old bass) is computed here and used in Step 4.
```diff
--- a/auto_pedal.py
+++ b/auto_pedal.py
@@ -643,10 +643,19 @@
         new_bass_pc = a.pitch % 12
         old_bass_pc = st["bass"] % 12
         t0_start = st.get("t0", m.start)
-        old_core, old_all = pcs_of(all_idx.sounding(t0_start, nxt.start), t0_start, nxt.start,
-                                   struck_from=t0_start - 0.05)
+        # "old" harmony = the bar just finished (not everything since the press: that saturates)
+        lo_old = max(t0_start, m.start)
+        old_core, old_all = pcs_of(all_idx.sounding(lo_old, nxt.start), lo_old, nxt.start,
+                                   struck_from=lo_old - 0.05)
+        _, old_lh = pcs_of(lh_idx.sounding(lo_old, nxt.start), lo_old, nxt.start,
+                           struck_from=lo_old - 0.05)
+        old_lh = old_lh | {old_bass_pc}
         new_core, _ = pcs_of(all_idx.sounding(nxt.start - 0.04, nxt.end), nxt.start - 0.04, nxt.end,
                              struck_from=nxt.start - 0.05)
+        # the LH tones of the bar's first half are always chord tones (short bass / arpeggio notes are
+        # otherwise diluted below W_MIN by a busy RH, and the bass note itself can drop out)
+        half = nxt.start + 0.5 * (nxt.end - nxt.start)
+        new_core = new_core | {n.pitch % 12 for n in lh_idx.starting(nxt.start - 0.05, half)} | {new_bass_pc}
         changed, why = harmony_verdict(old_bass_pc, old_core, old_all, new_bass_pc, new_core, strict=False, bass_move_changes_pedal=bass_move_changes_pedal)
 
         if not changed and st["held"] >= max_hold_bars:
```
Expected: all existing tests pass. Check script: A: 44 spans, no span covering >= 3 bars; B: 75 spans.
(The 3 remaining "no re-press" bar lines in A, `13:E->D 17:E->B 21:E->D`, are fixed in Step 4.)

## Step 3 — same idea for the mid-bar branch
What it does: for a mid-bar bass candidate `a`, `old_*` is computed over the last bar-window only (`lo_old`), and `new_core` additionally gets
the new bass pitch class and the LH pitch classes struck from just before `a` to half-way to the bar end.
```diff
--- a/auto_pedal.py
+++ b/auto_pedal.py
@@ -587,13 +587,18 @@
                     continue
                 # Compute old harmony from st["t0"] to a.start
                 t0_start = st.get("t0", m.start)
-                old_core, old_all = pcs_of(all_idx.sounding(t0_start, a.start), t0_start, a.start,
-                                           struck_from=t0_start - 0.05)
+                old_bass_pc = st["bass"] % 12
+                new_bass_pc = a.pitch % 12
+                lo_old = max(t0_start, m.start)
+                old_core, old_all = pcs_of(all_idx.sounding(lo_old, a.start), lo_old, a.start,
+                                           struck_from=lo_old - 0.05)
+                _, old_lh = pcs_of(lh_idx.sounding(lo_old, a.start), lo_old, a.start,
+                                   struck_from=lo_old - 0.05)
+                old_lh = old_lh | {old_bass_pc}
                 # Compute new harmony from a.start to m.end
                 new_core, _ = pcs_of(all_idx.sounding(a.start - 0.04, m.end), a.start - 0.04, m.end,
                                      struck_from=a.start - 0.05)
-                old_bass_pc = st["bass"] % 12
-                new_bass_pc = a.pitch % 12
+                new_core = new_core | {new_bass_pc} | {n.pitch % 12 for n in lh_idx.starting(a.start - 0.05, a.start + 0.5 * (m.end - a.start))}
                 changed, why = harmony_verdict(old_bass_pc, old_core, old_all, new_bass_pc, new_core, strict=True, bass_move_changes_pedal=False)
                 if not changed:
                     continue
```
Expected: all existing tests pass. Check script: A and B nearly unchanged (A 44, B 75).

## Step 4 — new-root rule (b2) and RH-only tones are not harmony
What it does:
- `harmony_verdict(..., lh_pcs=None, old_lh_pcs=None)`: two new **optional** keyword parameters.
- Conflict test (a): when `lh_pcs` is given, only pitch classes that the LH plays (or the old/new bass) count. RH passing and neighbour tones can
  no longer cut a sustained chord. (Fixes cause 4.)
- New rule (b2): the bass moves, the **complete** triad rooted on the new bass sounds now, and the old LH did not hold that complete triad -> change.
  This catches A -> F#m even if the melody had F#. Inversions stay safe, because the triad rooted on an inverted bass is incomplete
  (Em/G has no D), so `test_inversion_holds_pedal` still passes. (Fixes cause 3.)
```diff
--- a/auto_pedal.py
+++ b/auto_pedal.py
@@ -131,7 +131,9 @@
 
 def harmony_verdict(old_bass_pc: int, old_core: Set[int], old_all: Set[int],
                     new_bass_pc: int, new_core: Set[int],
-                    strict: bool = False, bass_move_changes_pedal: bool = False) -> Tuple[bool, str]:
+                    strict: bool = False, bass_move_changes_pedal: bool = False,
+                    lh_pcs: Optional[Set[int]] = None,
+                    old_lh_pcs: Optional[Set[int]] = None) -> Tuple[bool, str]:
     """
     Decide whether the harmony changes between the old pedal window and the new window.
     Returns (changed: bool, reason: str).
@@ -140,10 +142,21 @@
     bass_only = len(new_core - {new_bass_pc}) == 0
     if bass_only:
         return new_bass_pc != old_bass_pc, "c:bass-only"
-    if harmony_conflict(old_bass_pc, old_core | new_core):
+    union = old_core | new_core
+    if lh_pcs is not None:                      # RH-only tones (passing / neighbour notes) are not harmony
+        union = union & (lh_pcs | {old_bass_pc, new_bass_pc})
+    if harmony_conflict(old_bass_pc, union):
         return True, "a:conflict"
     if new_chord_on_bass(new_bass_pc, new_core, old_all, strict=strict):
         return True, "b:new-chord-on-bass"
+    # (b2) the bass moves, the complete triad rooted on the new bass is sounding now, and the old left hand
+    # did not hold that complete triad: a new chord, even if the melody had touched its tones before.
+    # (Inversions are safe: the triad rooted on an inverted bass is incomplete, e.g. Em/G has no D.)
+    if old_lh_pcs is not None and new_bass_pc != old_bass_pc:
+        for intervals in ((0, 4, 7), (0, 3, 7)):
+            triad = {(new_bass_pc + i) % 12 for i in intervals}
+            if triad <= new_core and not triad <= old_lh_pcs:
+                return True, "b2:new-root-in-bass"
     # If bass changed and new has a chord-like set different from old
     if not strict and bass_move_changes_pedal and new_bass_pc != old_bass_pc:
         for intervals in ((0, 4, 7), (0, 3, 7)):
@@ -599,7 +612,8 @@
                 new_core, _ = pcs_of(all_idx.sounding(a.start - 0.04, m.end), a.start - 0.04, m.end,
                                      struck_from=a.start - 0.05)
                 new_core = new_core | {new_bass_pc} | {n.pitch % 12 for n in lh_idx.starting(a.start - 0.05, a.start + 0.5 * (m.end - a.start))}
-                changed, why = harmony_verdict(old_bass_pc, old_core, old_all, new_bass_pc, new_core, strict=True, bass_move_changes_pedal=False)
+                lh_pcs = {n.pitch % 12 for n in lh_idx.sounding(lo_old, m.end)}
+                changed, why = harmony_verdict(old_bass_pc, old_core, old_all, new_bass_pc, new_core, strict=True, bass_move_changes_pedal=False, lh_pcs=lh_pcs, old_lh_pcs=old_lh)
                 if not changed:
                     continue
                 press_change(a, m.end)
@@ -660,8 +674,9 @@
         # the LH tones of the bar's first half are always chord tones (short bass / arpeggio notes are
         # otherwise diluted below W_MIN by a busy RH, and the bass note itself can drop out)
         half = nxt.start + 0.5 * (nxt.end - nxt.start)
-        new_core = new_core | {n.pitch % 12 for n in lh_idx.starting(nxt.start - 0.05, half)} | {new_bass_pc}
-        changed, why = harmony_verdict(old_bass_pc, old_core, old_all, new_bass_pc, new_core, strict=False, bass_move_changes_pedal=bass_move_changes_pedal)
+        new_core = new_core | {n.pitch % 12 for n in lh_idx.starting(nxt.start - 0.05, nxt.start + 0.5 * (nxt.end - nxt.start))} | {new_bass_pc}
+        lh_pcs = {n.pitch % 12 for n in lh_idx.sounding(lo_old, nxt.end)}
+        changed, why = harmony_verdict(old_bass_pc, old_core, old_all, new_bass_pc, new_core, strict=False, bass_move_changes_pedal=bass_move_changes_pedal, lh_pcs=lh_pcs, old_lh_pcs=old_lh)
 
         if not changed and st["held"] >= max_hold_bars:
             changed = True
```
Expected: all existing tests pass. Check script:
- A: **47 spans**, no span >= 3 bars, **no** bar line with a bass change and no re-press.
- B: **71 spans**, **0 mid-bar presses** (the two spurious presses at 24.2 / 25.2 are gone), spans >= 3 bars are only genuine long holds
  (`7.1->10.1` Bm, `16.1->20.1` F#m, `23.1->26.1` D, `54.1->57.1`, `98.1->102.1`).
- Remaining known gaps in B (open fifths / melody-only bars, **leave them**, see "Out of scope"): `3:F#->C#`, `9:B->E`, `56:B->F#`.

## Step 5 — add tests
Append to `tests/test_auto_pedal.py` (it already has `Note`, `LEFT`, `RIGHT`, `auto_pedal`, `_beat_song`). Do not remove the existing tests.
Before Step 2 the first three fail and the fourth passes (it is a regression guard); after Step 4 all four must pass.
```python
# --- new tests (append to tests/test_auto_pedal.py; uses the file's _beat_song, Note, LEFT, RIGHT, auto_pedal) ---
BEAT = 0.5   # seconds per beat in these tests


def _legato(pitches_and_beats, bar_start, bar_beats, hand):
    """[(pitch, beat_in_bar), ...] -> Notes, each lasting until the next onset of the list (the last to the bar end)."""
    out = []
    for i, (p, b) in enumerate(pitches_and_beats):
        end_b = pitches_and_beats[i + 1][1] if i + 1 < len(pitches_and_beats) else bar_beats
        out.append(Note(p, bar_start + b * BEAT, bar_start + end_b * BEAT, 80, 0, hand))
    return out


def _bar(i, bar_beats, lh, rh):
    t0 = i * bar_beats * BEAT
    return _legato(lh, t0, bar_beats, LEFT) + _legato(rh, t0, bar_beats, RIGHT)


def _starts(spans):
    return [round(a, 2) for a, _ in spans]


def test_busy_right_hand_does_not_hide_a_bass_chord_change():
    """4/4: D | Em F#m | Bm | G, LH = short bass + arpeggio notes, RH = busy melody (anh-dau-tu-luc-em-di bars 7-10)."""
    bars = [
        _bar(0, 4, [(50, 0), (57, 0.5), (62, 1), (64, 1.5), (66, 2), (57, 3)], [(79, 0), (74, 2.5), (76, 2.8), (78, 3)]),
        _bar(1, 4, [(40, 0), (47, 0.5), (55, 1), (42, 2), (49, 2.5), (57, 3)], [(71, 0), (69, 2), (73, 3)]),
        _bar(2, 4, [(47, 0), (54, 0.5), (59, 1), (61, 1.5), (62, 2)], [(71, 0), (66, 2.5), (64, 3), (66, 3.5)]),
        _bar(3, 4, [(43, 0), (50, 0.5), (55, 1), (59, 1.5)], [(66, 0), (66, 2), (66, 2.5), (64, 2.8), (61, 3.3)]),
        _bar(4, 4, [(50, 0), (57, 1)], [(74, 0)]),
    ]
    notes = [n for b in bars for n in b]
    s = _beat_song(notes, [0.0, 2.0, 4.0, 6.0, 8.0, 10.0], 10.0)
    _, spans = auto_pedal.build(s)
    starts = _starts(spans)
    for bar_line in (2.0, 4.0, 6.0, 8.0):                      # Em, Bm, G, D: each bar line re-pressed
        assert any(abs(t - bar_line) < 0.2 for t in starts), (bar_line, spans)
    assert any(abs(t - 3.0) < 0.2 for t in starts), spans       # Em | F#m inside bar 2


def test_rh_passing_tone_does_not_cut_a_sustained_chord_3_4():
    """3/4: the same D chord for 3 bars, RH melody F#4 G#4 A4 (G#4 = tritone to D): one pedal span (Khi-nguoi-lon-co-don bars 23-25)."""
    bar = lambda i: _bar(i, 3, [(38, 0), (45, 1), (50, 2)], [(66, 0), (68, 1.5), (69, 2)])
    notes = [n for i in range(3) for n in bar(i)]
    s = _beat_song(notes, [0.0, 1.5, 3.0, 4.5], 4.5)
    _, spans = auto_pedal.build(s)
    assert len(spans) == 1, spans


def test_melody_tone_does_not_hide_new_chord_root_3_4():
    """3/4: A | A | F#m | F#m with the melody sitting on F#4 all along: the pedal changes at bar 3 (Khi-nguoi-lon-co-don bars 68-71)."""
    A = dict(lh=[(45, 0), (52, 1), (57, 2), (61, 2.5)], rh=[(66, 0)])
    Fm = dict(lh=[(42, 0), (49, 1), (54, 2), (57, 2.5)], rh=[(66, 0)])
    notes = []
    for i, c in enumerate((A, A, Fm, Fm)):
        notes += _bar(i, 3, c["lh"], c["rh"])
    s = _beat_song(notes, [0.0, 1.5, 3.0, 4.5, 6.0], 6.0)
    _, spans = auto_pedal.build(s)
    assert any(abs(t - 3.0) < 0.2 for t in _starts(spans)), spans


def test_four_bar_progression_four_presses():
    """4/4: I | ii | V | I, one clear LH chord per bar under a melody: four presses, not one long smear."""
    chords = [((48, 55, 60, 64), 72), ((50, 57, 62, 65), 74), ((43, 55, 59, 62), 71), ((48, 55, 60, 64), 72)]
    notes = []
    for i, (lh, mel) in enumerate(chords):
        notes += _bar(i, 4, [(lh[0], 0), (lh[1], 1), (lh[2], 2), (lh[3], 3)], [(mel, 0), (mel + 2, 2)])
    s = _beat_song(notes, [0.0, 2.0, 4.0, 6.0, 8.0], 8.0)
    _, spans = auto_pedal.build(s)
    assert len(spans) == 4, spans
```
Run the whole file: `pytest -q tests/test_auto_pedal.py`.

## Step 6 — docs
- `AUTO_PEDAL_SPEC.md`: describe (1) old window = last bar, (2) new tones = LH of the first half of the bar + new bass, (3) rule (b2),
  (4) conflict test uses LH + bass pitch classes only. Also fix the drifted details: `mid_min_gap_beats` default is **1.0** (not 1.5),
  document the `bass_move_changes_pedal` parameter, repair the garbled text of step 4b.
- `docs/ARCHITECTURE.md`: only the auto-pedal section; same four points, 5-8 lines.

## Out of scope — do not change (report only)
- Open-fifth voicings (B bars 2->3 `F#3 C#4 F#4...` -> `C#3 G#3 C#4`, bar 56 `F# C#` only): ambiguous without a third; leave as is.
- Bar 1 of piece A has no pedal (RH pickup inside a full bar, LH rest): acceptable.
- Removing other unused helpers (`harmony_signature`, `harmony_changed`, `pcs_changed`, `triad_on_bass`, `is_bass_candidate`, `same_harmony`, nested `sig_at`): separate clean-up.
- `max_hold_bars` stays as the safety net.

## Final report to give back
1. `pytest -q tests/test_auto_pedal.py` result (counts).
2. The check-script output for both pieces after Step 4.
3. List of files changed. Nothing else should be touched.
