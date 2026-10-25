# Auto pedal review and fix plan

> **Revision 3 (read section 11 last; it is the final rule).** Revision 2 (section 10) first: The screenshot came from `anh-dau-tu-luc-em-di.musicxml`, not from
> `khong-mon-dang-ho-doi.musicxml`. Section 10 matches your report exactly (`D5` in the left hand, second 9, measure 3) and
> **changes the design in 5.3**. Sections 1 to 9 stay valid for the first score (use it as a second test case), except where
> section 10 says otherwise.

Scope: `auto_pedal.py` (+ `musicxml_loader.py` tempo map, `midi_loader.set_pedal_mode`, `staff.draw_pedal_brackets`),
checked against `khong-mon-dang-ho-doi.musicxml`.

Status: analysis done and partly verified by running the real loader + `auto_pedal.build` on the score
(`pretty_midi` and `progress` were stubbed, since they are not installed here; neither is used by the MusicXML path).
Not yet verified: the exact measure and note you see on screen (see "Open questions").

---

## 1. TL;DR

1. **Your expectation for the 2nd measure is reasonable.** `A2` is a one-beat bass note, and `E3` is a new bass note that
   is held for 7 beats (tied into the next measure) and carries a different harmony (Am to Em colour). A pedal change on
   `E3` is what a pianist would normally do.
2. **Why it does not happen today** (two independent causes, both verified):
   - **Bug in the tempo map** (`musicxml_loader`): `song.quarter_times` is `[(0.0, 0.5)]` (120 bpm) although the score is
     126 bpm (a quarter is 0.476 s). Every beat-based threshold in `auto_pedal` is 5 % off.
   - **Rule too strict**: `mid_min_gap_beats = 1.5` forbids a change less than 1.5 beats after the previous press.
     `A2` to `E3` is exactly 1 beat, so the change is rejected, whatever the harmony.
3. **Do not just lower the threshold.** With the tempo fixed and only the gap lowered, the number of pedal spans in this
   piece goes from 61 to about 80 and spurious mid-bar changes appear (e.g. `G2 E3 B3` in measure 4 is one Em arpeggio but
   gets split at `B3`). The harmony test is the real weakness: it looks at the **left hand only** and compares tiny
   pitch-class sets, so two different single bass notes always count as "changed". The fix needs a small chord analyser
   that reads both hands (section 5).
4. **Measure 3 having "2 pedals"**: in the file as I load it, measure 3 is covered by one pedal; the visible re-press is at
   the downbeat of measure 4 (`G2`, 63 ms after the attack). I could not find a `D5` there (see Open questions). With the
   chord-level rule this re-press disappears because measure 4 is the same Em chord with a new bass note (an inversion).

---

## 2. Reproduction

Score facts (MusicXML numbering, 4/4, 126 bpm, 12 divisions per quarter, LH = part 2):

| XML measure | LH | RH | Harmony (by ear / chord labeller) |
|---|---|---|---|
| 1 | rest | rest x3 beats, `E4` on beat 4 | lead-in (not flagged as pickup: `pickup_end == 0`) |
| 2 | `A2` (1 beat), `E3` (3 beats, **tied**) | `C5 B4 A4 G4 E4~` | Am on beat 1, then E-bass (Esus4/Em) |
| 3 | `E3` (tied, no new attack) | `E4~ B3 D4` | Em7 / E7 colour |
| 4 | `G2 E3 B3` (arpeggio) | `B4 A4 G4 E4~` | Em (Em/G) |
| 5 | `D4` (long) | `E4~ ...` | D / Em7 area |

Current output (`build(song)` with defaults), first events:

```
t=1.905  DOWN   (m2 beat 1.00)
t=5.777  UP     (m4 beat 1.13)   <- first release, 63 ms after G2 / B4 attack
t=5.807  DOWN   (m4 beat 1.19)
t=7.682  UP     (m5 beat 1.13)
...
```

So the first pedal covers **measures 2 and 3 completely**, and the next change is on the downbeat of measure 4.

Diagnostics for the mid-bar candidate `E3` (m2 beat 2):

```
quarter_times            = [(0.0, 0.5)]        # wrong, real value 0.476
a.duration >= 2 beats    = True   (3.333 s)
on_beat                  = True
a.start - t_down         = 0.476 s  <  mid_min_gap_beats * q = 1.5 * 0.5 = 0.75   -> REJECTED
cur sig (A2)             = (A, {A});  new sig (E3) = (E, {E});  pcs_changed = True  (would have passed)
```

Experiments (all on this score):

| Variant | spans | Result |
|---|---|---|
| defaults | 61 | no change at m2 beat 2 |
| `mid_min_gap_beats = 0.9` | 62 | adds m2 beat 2 only, nothing else changes |
| `gap = 1.0 - 0.02` but q still 0.5 | 61 | still rejected: `0.5 - 0.02 = 0.48 > 0.476` (blocked by 4 ms) |
| tempo fixed (q = 0.476), other defaults | 80 | **many new mid-bar changes** (e.g. m4 beat 3) |
| tempo fixed + `gap = 1.0` | 81 | m2 beat 2 added, but m4 beat 3 etc. still spurious |

The last two rows are why the harmony test must change together with the tempo fix: the current thresholds only look
sane because the tempo is 5 % wrong.

---

## 3. Is the expectation right? (musical reasoning)

Sustain pedalling normally follows harmony, changed just after the new bass note is struck ("legato pedalling"), which is
what the module already does. For this passage:

- **m2:** `A2` (Am, with `C5`) then `E3`. The `E3` is not a passing note: it lasts 7 beats and becomes the bass of the
  following Em chord, while the `A2` and the `C5 / B4 / A4` above it belong to the Am sound. Keeping the pedal from `A2`
  through `G4 E4` over an `E3` bass blurs two harmonies for almost 3 beats. **Changing on `E3` is right.**
  (Strictly, "root to fifth of the same chord" is one harmony. Here the melody and the 7-beat length make it a real
  change, which is why a chord analyser that reads both hands is better than a bass-only rule.)
- **m3:** the bass `E3` is only held, the right hand is `E4 B3 D4` (Em7 colour). One pedal for the whole bar, **correct**.
- **m4:** `G2 E3 B3` is an Em arpeggio over a new bass note `G`. The chord does not change (Em, first inversion). A pianist
  could either hold the pedal or refresh at the bar line. Recommendation: hold it, and rely on `max_hold_bars` for a
  refresh (decision D1 below).

---

## 4. Findings in the code

Severity: **H** = wrong result on this score, **M** = fragile / likely wrong elsewhere, **L** = cleanup.

### H1. Tempo map keeps the wrong point (`musicxml_loader._timing` / `_Tempo`)

`_Tempo.points` for this file is `[(0, 0.0, 120), (0, 0.0, 120), (0, 0.0, 126)]`: `set_tempo` and `set_divisions` each call
`restart_here`, which appends a point at the same time. When `quarter_times` is built from these points it skips every
point whose `sec` was already seen:

```python
for _, sec, bpm in tempo.points:
    if bpm > 0 and sec not in seen:      # first point at this time wins = the default 120
        quarter_times.append((sec, 60.0 / bpm))
        seen.add(sec)
```

The first point at a given time is the stale one; the **last** one is the tempo in force. Effect: any score whose first
tempo is written inside measure 1 (nearly all of them) gets 0.5 s per quarter in `quarter_length`, which feeds
`release_delay`, `attack_window`, `mid_anchors`, `on_beat`, `sig_at`, `mid_min_*` and `FINAL_TAIL`. Tempo changes that land
on the same time as another point (tempo + divisions change in one measure) are wrong the same way.

### H2. Mid-bar minimum gap rejects beat 1 to beat 2 (`build`, mid-bar loop)

`a.start - st["t_down"] < mid_min_gap_beats * aq` with `mid_min_gap_beats = 1.5`. The gap is measured from the last
**press**, not from the previous bass **attack**, and 1.5 beats cannot be satisfied by a one-beat bass note followed by a
long one. There is also no float tolerance, so an exact 1-beat gap is rejected by rounding.

### M1. Harmony test reads only the left hand and compares near-empty sets (`harmony_signature`, `pcs_changed`)

- Documented intent (`pcs_changed`): "the bass alone moving root to fifth inside one chord is not a change". Actual
  behaviour: with one bass note per window the sets are `{A}` and `{E}`, Jaccard 0, so it is **always** a change. The
  docstring and the code disagree.
- Conversely a rising arpeggio of one chord (`G2 E3 B3`) is split once the thresholds allow it.
- The right hand is ignored, so the harmony of a long held bass (m2 to m3 here) cannot be seen at all.
- Limit already listed in `ARCHITECTURE.md` ("only the LH is read"); this score shows it matters.

### M2. Bar-line rule counts any bass pitch-class change as a harmony change (`harmony_changed`)

`new_sig[0] != cur_sig[0]` returns True on every bass note change, so inversions and walking bass re-press at every bar
line (m4 to m10 and most of the piece re-press every bar). That may be intended, but it makes "one pedal per chord"
impossible and is the likely source of the second pedal you see.

### M3. `st["sig"]` is overwritten by a partial window after a mid-bar press

After `press_change` in the mid-bar loop, `sig=new` where `new` covers only `min(a.duration, m.end - a.start)`. At the next
bar line `harmony_changed(st["sig"], new_sig)` then compares a half-bar signature with a whole-bar one. Harmless today
(bass pc decides), but it breaks as soon as chord-level comparison is used. The signature should always be computed over the
same kind of window.

### M4. `lh_notes` fallback returns **all** notes when the score has no LH notes

A score with no left-hand part (or one the loader labelled all RH) gets a pedal that follows the melody. This is the
documented behaviour (`AUTO_PEDAL_SPEC.md`, section 11), so it is a **decision**, not a bug: either keep it, or return no
pedal and log it in `song.cleanup`. Low priority.

### L1. `draw_pedal_brackets` is called with all `song.pedal_spans` (it clips internally), while `sys_spans` is computed
just above and only used for the `<= 12` check. Works, but is O(n) per system per frame; pass `sys_spans` and carry the
previous span's end for `is_new_press`.

### L2. `release_delay(q)` clamps to 40 to 80 ms. At 126 bpm that gives 63 ms, so the visible lift is just after the new
note, correct for legato pedalling. No change needed, but add a test so the tempo fix does not move it unnoticed.

### L3. Tests: see section 9 (now reviewed). The tempo fix does not break them, because they set `quarter_times` by hand.

---

## 5. Fix plan

Order matters: do 5.1 first (it changes numbers everywhere), then 5.2 to 5.4 together.

### 5.1 Fix the tempo map (`musicxml_loader.py`)

- Build `quarter_times` so the **last** point at a given time wins:

  ```python
  by_time = {}
  for _, sec, bpm in tempo.points:
      if bpm > 0:
          by_time[sec] = 60.0 / bpm          # later point overwrites
  quarter_times = sorted(by_time.items())
  ```
- Also guard `restart_here`: skip appending when the previous point has the same `div` (replace it instead), so `points` has
  no zero-length segments.
- Check `midi_loader.quarter_length` for `t` before the first point (currently returns 0.5): return the first point's value.
- Test: load this file, assert `quarter_length(2.4)` is about 0.476; a score with a tempo change in measure 5 gets two entries.

### 5.2 Add a small chord analyser (`auto_pedal.py`)

New pure function, no UI dependencies:

```python
def chord_at(index_all, t0, t1, bass_pc) -> Chord   # Chord(root_pc, quality, confidence)
```

- Input: **all** non-grace notes sounding in `[t0, t1)` (both hands), weight = overlap duration, LH x1.5, plus a small bonus
  for `bass_pc`.
- Templates: major, minor, dim, sus4, 7, maj7, m7, m7b5 (7ths slightly penalised, passing tones outside the template
  penalised, missing root/third/fifth penalised, bonus when root == bass).
- Prototype result on this score (checked): m2 beat 1 = Am, m2 beats 2 to 4 = E (sus4 colour), m3 = E7/Em7 colour,
  m4 = Em for the whole bar, so m4 beat 3 is *not* a new chord.
- `same_harmony(a, b)`: same **root** (quality only breaks ties when confidence is high). Qualities such as Em vs Em7 vs Esus4
  with the same root must **not** trigger a change.
- Cache the weights per window; the piece is short, but keep it `O(notes in window)`.

### 5.3 Use it in the mid-bar test (`build`)

A mid-bar anchor `a` changes the pedal when all of these hold:

1. LH note struck on a beat (existing `on_beat`), not a grace note.
2. `a.duration >= mid_min_beats * q` (keep 2.0; ties are already merged into one note, so the 7-beat `E3` qualifies).
3. **Gap rule, reworked:** at least `mid_min_gap_beats = 1.0` beat after the previous **bass attack that started the current
   pedal** (not the press time), with `eps = 0.02 s`. This allows beat 1 to beat 2 and still forbids two changes inside one beat.
4. `not same_harmony(chord_at(prev_press_window), chord_at([a.start, a.start + min(a.duration, to_next_anchor_or_bar_end))))`.

The "previous window" is `[last change, a.start)` (at most one bar), the "new window" ends at the next anchor or the bar
end, never beyond `a.duration`. Both windows are computed with the same function so M3 disappears: `st["chord"]` replaces
`st["sig"]`.

### 5.4 Use it at the bar line (`build`)

- LH attack on the downbeat **and** `not same_harmony(st["chord"], chord_at(new bar window))`, or `held >= max_hold_bars`.
- Same bass pitch class but a different chord still counts as a change (keeps the existing `harmony_changed` intent).
- **Decision D1:** bass note changes inside the same chord (inversions, `E3` to `G2` in m4):
  - (a) hold the pedal (recommended, matches your "one pedal" expectation); refresh after `max_hold_bars`.
  - (b) keep today's behaviour (re-press whenever the bass pitch class moves).
  Implement as a parameter `bass_move_changes_pedal: bool = False` so (b) stays available and tests can cover both.
- Consider lowering `max_hold_bars` from 4 to 3 if (a) feels too long on slow pieces; check on 3 more scores before changing.

### 5.5 Smaller items

- M4: if there are no LH notes, return no pedal and append a message to `song.cleanup` instead of following the melody.
- L1: pass `sys_spans` to `draw_pedal_brackets` (keep the "new press" test working with the previous span's end).
- Update the docstrings: `pcs_changed` claim, the "root to fifth" sentence, and the module header (harmony now reads both hands).
- `ARCHITECTURE.md`: update the auto-pedal section, the "Limits" bullet (RH is now read for harmony), and add the tempo-map
  bug to the review notes. Update `docs/AUTO_PEDAL_SPEC.md` accordingly. Per `CLAUDE.md`: **do not touch `version.py` or
  `CHANGELOG.md`** unless you tell me the version; ask before committing.

### 5.6 Tests (`tests/test_auto_pedal.py`, plus one loader test)

Using small synthetic songs (no local data), 120 bpm and 126 bpm variants:

| Test | Expectation |
|---|---|
| `test_tempo_first_in_measure` (loader) | `quarter_length` equals the written tempo at t = 0 and later |
| `test_short_bass_then_long_bass_changes` | `A2` (1 beat) then `E3` (3 beats), RH Am to Em: one change on beat 2 |
| `test_long_held_bass_next_bar_keeps_pedal` | tied bass into the next bar, no LH attack: one pedal across both bars |
| `test_arpeggio_same_chord_no_split` | `G2 E3 B3` + Em melody: no mid-bar change |
| `test_inversion_holds_pedal` | Em over `E3` then Em over `G2` at the bar line: no change (default); change with `bass_move_changes_pedal=True` |
| `test_alberti_and_repeated_chords` | unchanged from today |
| `test_max_hold_bars` | refresh still happens |
| `test_release_delay_scaling` | 126 bpm gives 63 ms, still 40 to 80 ms at 40 and 200 bpm |
| `test_no_lh_notes_no_pedal` | empty result + cleanup message |
| `test_regression_khong_mon` (optional, if you accept a small fixture) | see section 6 |

### 5.7 Regression check

- Script: load the score, run `build`, print spans as `m<n> beat <b>` (as in section 2), diff against the previous run.
- Expected: span count goes from 61 to a similar number (not 80), plus the new change at m2 beat 2 and fewer bar-line
  re-presses where only the bass inverts.
- Listen / look: staff view with "Auto by measure" on m2 to m5, then two other pieces with Alberti bass and with a
  walking bass.

---

## 6. Expected pedal for the first measures after the fix

| Time (m, beat) | Event | Reason |
|---|---|---|
| m2 b1 | DOWN | first LH attack (`A2`) |
| m2 b2 + about 63 ms | UP, DOWN +30 ms | `E3` long new bass, Am to E |
| m3 | none | bass held (tie), same harmony |
| m4 b1 | none (D1a) or UP/DOWN (D1b) | `G2` is an inversion of the same Em |
| m5 b1 + about 63 ms | UP, DOWN +30 ms | `D4`, a new chord |

With D1a the second pedal spans m2 beat 2 to m5 beat 1 (about 10 beats). If that feels long, set `max_hold_bars = 3`, which
refreshes at the m5 bar line anyway.

---

## 7. Open questions / files that would help

> Items 1 to 4 were answered by your screenshot and message; see section 9 for what I could and could not match.

1. **Which measures are you counting?** In the score file, measure 1 is the lead-in (3 beats rest + `E4`). If you count
   from the first bar with music, your "2nd / 3rd" are my m3 / m4. Please confirm, or send a screenshot of the staff with
   the Ped. brackets and the measure numbers.
2. **The `D5` note.** In the file there is no `D5` anywhere near measures 1 to 6 (the first `D5` is at measure 38). The
   pedal lifts at m4 beat 1.13 (`G2` / `B4`), m5 beat 1.13 (`D4`) ... Is the note you saw `D4` (m3 beat 4, or m5 beat 1), or is the
   hand assignment / octave display different on your side?
3. **Settings used when you run it:** `musicxml_hands` (file vs auto) and `musicxml_fingering`. I assumed the default
   `file` (staff 1 = RH, staff 2 = LH); with `auto`, `hand_split.py` may put some notes in the other hand and change the
   LH-only harmony.
4. **Files to confirm the plan:** `tests/test_auto_pedal.py`, `docs/AUTO_PEDAL_SPEC.md`, `hand_split.py`, and
   `pianists`/settings if you changed the pedal mode there.
5. **Decision D1** (inversions: hold or re-press) and whether you want `max_hold_bars` at 3 or 4.

---

## 8. Suggested order of work

1. Tempo fix + test (small, safe).
2. `chord_at` / `same_harmony` + unit tests on short chord examples.
3. Mid-bar rule (5.3), run the regression script, compare with section 6.
4. Bar-line rule (5.4) with D1, update tests.
5. Cleanup items (5.5), docs, run `pytest`, `tools/regress.py`.
6. Ask for the version and changelog decision before committing.


---

## 9. Update after the screenshot, `hand_split.py`, the spec and the tests

### 9.1 What the screenshot shows, and what I could not match

- The staff draws the measure number as `m.index + 1` above each bar line, and the first measure of a system has no
  number. So "2" and "3" in the picture are the same global numbers as the XML measures.
- The picture does **not** match the uploaded score at those numbers: its first measure has three green (right hand) notes
  under an `8va`, measure 2 has blue (left hand) notes written in the treble staff, and measure 3 has six or so notes with
  two red-circled ones. In the file, measure 1 is only a lone `E4`, and measure 3 has three right-hand notes. So the image
  comes from another system of this piece, from another score, or from a different hand assignment (blue notes in the
  treble staff suggest the hands were re-split, or edited in the editor).
- The `24` under the bar lines is not a measure number (those are drawn at the top); I could not tell from `staff.py` what
  it is, so I ignored it.

### 9.2 Best match for "second 9, the middle of a measure, note D in the left hand"

With the default `file` hands, the release nearest to 9 s is **t = 9.586 s, 63 ms after the left-hand `D2` at the downbeat
of XML measure 6** (the `A2` and `F3` follow; same shape as measure 2: a short bass, then a long note). With the `auto`
hand split (`hand_split.py`, `musicxml_hands = auto`) the pedal spans are `1.90 to 5.78`, `5.81 to 9.59`, `9.62 to 11.49`, so
again there is a lift at 9.59 s. So my guess is that the note is **`D2`, not `D5`**, and that your "3rd measure" is a
differently counted measure 6 (or the 3rd measure of a phrase). Please confirm; if it is the m6 case, the expectation from
the m2 analysis carries over:

| m6 notes (LH) | Today | After the fix |
|---|---|---|
| `D2` (1 beat), `A2` (1 beat), `F3` (2 beats) | one pedal per bar, re-pressed 63 ms after `D2` | same re-press at the downbeat if Dm differs from the previous chord (m5 is D / Em7 area, so probably yes), no change at `A2` (root to fifth of the same Dm), no split at `F3` |

The question for you stays: do you want the pedal to **lift at the new bass note's attack + 63 ms** (current), or a bit
later / earlier? 63 ms after the attack is the usual "legato pedal"; if the lift looks like it sits "on the note" on the staff,
that is just how it is drawn (the notch is at the release time, 63 ms to the right of the note head).

### 9.3 Effect of `hand_split.py` (auto hands)

With `musicxml_hands = auto`, the left hand loses some notes it has in the file (for example `D4` in measure 5 is given to the
right hand, and `B3` of measure 3 is given to the left). Because the pedal reads only the left hand, the pedal then also changes:
in measure 5 there is no left-hand attack, so the pedal is held through it. This is another reason to let the chord analyser
(5.2) read **both hands**: the pedal would then not depend on how the notes were divided between the hands.

### 9.4 `tests/test_auto_pedal.py` reviewed (not run: no `pygame` or `pytest` here)

- Tests that set `quarter_times = [(0.0, 0.5)]` by hand, or call `release_delay(0.5)`, are not affected by the tempo fix (5.1).
  There is no test that builds a MusicXML song and checks `quarter_length`; add the loader test from 5.6.
- Behaviours these tests pin and the new rules **must keep**:
  - `test_mid_bar_oompah_same_chord_no_change`: bass `C` then `G` with `E G` above in the left hand is **one** chord.
    The chord analyser must read the `E G` and see C major under both bass notes.
  - `test_mid_bar_oompah_c_to_g7_changes`: `G` bass with `B D` is a new chord (G7 colour) and must change.
  - `test_mid_bar_bass_change_on_beat_3` and `test_mid_bar_lh_rests_on_beat_1_then_new_bass_on_beat_2`: bass only, 2 or 3
    beats long, `C` to `G`: **must change**. These two tests show the intended rule: **a long bass note that moves to a
    different pitch class with no common chord tones is a change.** Your `A2` to `E3` (3 to 7 beats) is the same situation,
    and is only blocked by the gap rule (the test with the bass change at beat 3 is 2 beats after the press, so it passes).
  - `test_mid_bar_walking_bass_quarters_does_not_chop` and `test_mid_bar_ignores_off_beat_bass`: still need the duration and
    on-beat conditions.
  - `test_new_bass_over_a_lower_ringing_note_changes_pedal`, `test_same_bass_new_chord_colour_changes_pedal`,
    `test_early_lh_attack_new_chord_anchors_on_that_note`: unchanged; the bar-line rule still decides on bass or chord
    change (see D1 for the inversion case).
- Important consequence for the design: a *bass-only* `C` then `G` is a change in the current tests, but "`G2 E3 B3` forming an
  Em arpeggio" must **not** be one. The difference is that in the second case the new notes are tones of the chord already
  sounding (the union of both hands' notes since the last press forms one triad), while `C` to `G` with nothing else sounding
  has no such evidence. So the rule in 5.3 should be: **change unless the new bass pitch class is a chord tone of the chord
  identified over the pedal so far (both hands).** For `A2` then `E3` over `C5 B4 A4`, `E` is a chord tone of Am; the
  decider is then the *length and role* of `E3` (7 beats, bass of the next chord) plus the right hand moving to `G4 E4`
  over it. Implement it as two signals: (a) chord root differs between the windows, or (b) the new bass lasts >= 3 beats
  **and** the right-hand notes during it no longer fit the previous chord's root. Add the A2/E3 case as a test with a
  right-hand melody so the rule is pinned.
- Tests to add or change: the list in 5.6, plus one for each of the oompah cases above run through the new analyser, and a
  hands-auto vs hands-file test showing that moving a single note between hands does not change the pedal.

### 9.5 `AUTO_PEDAL_SPEC.md`

It is in Vietnamese and matches the current code well. Sections to change when the plan is applied: 3 (note that
`quarter_times` must hold the tempo in force), 4 (`harmony_signature`, `harmony_changed`, `pcs_changed` replaced by the chord
analyser; the oompah sentence stays true), 6 (mid-bar conditions, new gap rule and the chord test), 9 (new parameters such as
`bass_move_changes_pedal`), and the edge-case table (add: arpeggio of one chord, inversion at the bar line, long tied bass
over two bars). Everything else (release delay, gap, final tail, `max_hold_bars`, display) stays as written.

### 9.6 What I still need from you

1. Confirm the note: **`D2` at about 9.5 s (XML measure 6)**, or something else?
2. Which score produced the screenshot (is it `khong-mon-dang-ho-doi.musicxml` with edited hands)? If you edited hands or
   fingering in the editor, send the project state or tell me which hand each of those notes has.
3. Decision D1 and the preferred `max_hold_bars` (section 5.4).


---

## 10. Revision 2: the correct score (`anh-dau-tu-luc-em-di.musicxml`, hands = `file`)

### 10.1 It reproduces your report exactly

Score: 4/4, **61 bpm** (a quarter is 0.984 s, a bar 3.93 s), measure 1 is a lead-in with three right-hand notes.
`song.quarter_times` is `[(0.0, 0.5)]` again, so **the tempo bug (H1) is confirmed and here it is a factor of two**.

Left hand, then what the pedal does today (`build` defaults):

| Measure | LH | Chord (by ear) | Pedal today | You expect |
|---|---|---|---|---|
| 2 | `G3 D4 G4` (beats 1 to 2), `A3 E4 C#5` (beats 3 to 4) | G, then A | **one pedal** (3.93 to 7.93) | **a change at beat 3** |
| 3 | `B3 F#4 B4 C#5`, then `D5` (beat 3, 2 beats long) | Bm all bar | **two pedals**: lifts at 9.90 s, 63 ms after LH `D5` (m3 beat 3) | **one pedal** |
| 4 | same pattern as measure 2 | G, then A | one pedal | change at beat 3 |
| 5 | same pattern as measure 3 (`D5` is 1 beat) | Bm | one pedal | one pedal |

Why:

- **Measure 3 split:** `D5` is picked as a "new bass note": it is the lowest note *struck* in its cluster, it lasts 2 beats
  (1.97 s, over the threshold `2 x 0.5 s = 1.0 s` that the wrong tempo gives; with the tempo fixed it still lasts exactly
  the threshold, so it passes or fails by float rounding), it is on a beat, and the pitch-class sets differ (`{D}` against
  `{B F# C#}`). But `D5` is not a bass: it is the **top of an ascending arpeggio** (`B3 F#4 B4 C#5 D5`) and a chord tone of Bm.
- **Measure 2 miss:** the chord changes at beat 3, but the new bass `A3` lasts half a beat, and `mid_bar` requires the bass
  note itself to last >= 2 beats (`mid_min_beats`). The change is carried by a *short* bass note and its figure
  (`A3 E4 C#5`), not by a long bass.

Experiment (new score):

| Variant | spans | m2 beat 3 | m3 beat 3 (`D5`) |
|---|---|---|---|
| defaults (wrong tempo) | 50 | no | **split** |
| tempo fixed | 42 | no | **split** |
| tempo fixed + `mid_min_gap_beats = 1.0` | 42 | no | **split** |

So, on this score, the tempo fix and the gap tweak change nothing visible. The design has to change.

### 10.2 What this means for the plan

Two rules in the current mid-bar test are wrong in principle, not just in their constants:

1. **"Long bass note" is the wrong trigger.** A chord can change on a short bass note (`A3`), and a long note can be a
   non-bass (`D5`). The trigger should be the **chord window**: a change point is a beat where the harmony that follows,
   for at least `mid_min_beats` (2 beats) or up to the bar end, differs from the harmony of the current pedal.
2. **The anchor must be in the bass register.** A note more than an octave above the lowest left-hand note struck since the
   last press (`D5` = 74 against `B3` = 59: 15 semitones) is figuration, never a pedal anchor. Existing tests still hold:
   `C3` to `G3` is 7 semitones, `C` to `G2` goes down.

Replace 5.3 with:

**5.3 (revised) Mid-bar change.** A candidate is a left-hand note struck on a beat, not a grace note, and:

1. **Register guard:** `anchor.pitch <= lowest_lh_pitch_since_last_press + 12`.
2. **Window guard:** the window `[anchor.start, min(next_candidate_beat_after_2_beats, bar_end))` is at least
   `mid_min_beats` beats long (the window, not the note; this lets `A3 E4 C#5` in measure 2 qualify: 2 beats to the bar end).
3. **Gap guard:** at least 1 beat after the previous bass attack of this pedal (`eps = 0.02 s`).
4. **Harmony test** (both hands; `old` = notes under the pedal so far, `new` = notes in the window), a change when
   **either**:
   - **(a) conflict:** the union of `old` and `new` pitch classes, relative to the old bass pc, contains a minor second or
     a tritone (`G` chord with `C#` under it), or both a minor and a major third; **or**
   - **(b) new chord on a new bass:** `new` contains a complete triad (or 7th) rooted on the new bass pc, and at least
     one of its tones is **absent** from `old`.
   - **(c) bass-only texture** (no notes other than the bass in `new`): fall back to today's test (`pcs_changed` on the two bass
     notes). This keeps `test_mid_bar_bass_change_on_beat_3` and `..._beat_2` passing.

Hand-check of the rule on the cases we have (no code run yet):

| Case | Guard / test | Result |
|---|---|---|
| Score 2, m2 beat 3: `A3 E4 C#5` + RH `D6 C#6 A5 G5` over `G3 D4 G4 + B5` | window 2 beats; `C#` is a tritone over `G` (a) | **change** |
| Score 2, m3 beat 3: `D5` over `B3 F#4 B4 C#5` | register guard (15 st) | **no change** |
| Score 2, m5 beat 3 (`D5`, 1 beat) | register guard | no change |
| Score 1, m2 beat 2: `E3` + RH `B4 G4 E4` over `A2` + `C5 B4` | `E G B` is complete Em on the new bass and `G` is not in `old` (b) | **change** |
| Score 1, m4: `G2 E3 B3` + RH `B4 A4 G4 E4` | `E G B` rooted on `E` is a triad, but every tone is already under the pedal (`G`, `B4` in RH); no conflict | **no change** |
| Test: oompah `C | E G` then `G | E G` | new window has no complete triad on `G` (needs `B` or `D`); no conflict | no change |
| Test: oompah `C | E G` then `G | B D` | complete G triad, `B D` absent from `old` (b) | change |
| Test: walking bass `C D E F` | union relative to `C` is `{0,2,4,5}`: no conflict, no triad | no change |
| Test: bass only `C` then `G` | (c) | change |

Rule (b) can still be fooled by a pickup chord or a passing 7th, so the plan includes a short **prototype step before
touching `build`**: implement `harmony_conflict(old, new)` and `new_chord_on_bass(old, new)` as pure functions, print the verdict for
every beat of both scores and the test songs, and tune (not the thresholds in `build`) until the table above is reproduced.

### 10.3 Bar-line rule (5.4), with your decision

You said inversions should **keep** the pedal, so D1 is settled as **(a): hold**.

- At a bar line with a left-hand attack, change when `old` and `new` conflict (a) or `new` has a complete chord on the new bass with
  tones absent from `old` (b), or `held >= max_hold_bars`. A bass note that only moves **inside the chord already under the pedal**
  (`E3` to `G2` in Em) does not change it.
- Score 2 measure 2 to 3 (`A3 E4 C#5` to `B3 F#4 B4 C#5`): `B` and `F#` are absent from `old`, and the old chord's bass `A` against
  new `B` is a new chord, so it **changes at the bar line** as you expect (bar 3 starts a fresh pedal).
- Keep `bass_move_changes_pedal: bool = False` as the switch for the old behaviour (re-press whenever the bass pc moves).
- `max_hold_bars`: your answer for 3 or 4 is still open; with this rule 4 is fine for these two pieces.

### 10.4 Expected pedal, new score (measures 2 to 5)

| Time | Event |
|---|---|
| m2 beat 1 | DOWN |
| m2 beat 3 + about 80 ms | UP, DOWN +30 ms (G to A) |
| m3 beat 1 + about 80 ms | UP, DOWN (A to Bm) |
| m3 beat 3 (`D5`) | **nothing** |
| m4 beat 1 + about 80 ms | UP, DOWN (Bm to G) |
| m4 beat 3 | UP, DOWN (G to A) |
| m5 beat 1 | UP, DOWN, and nothing at m5 beat 3 |

With the tempo fixed the lift delay is `0.08 s` here (a quarter is 0.98 s; today it is 62.5 ms because of the wrong tempo).

### 10.5 Changes to the earlier plan

| Item | Change |
|---|---|
| 5.1 tempo fix | **Still first**, and now clearly needed: 61 bpm is read as 120 bpm |
| 5.2 chord analyser (`chord_at` with templates) | **Downsized**: the template labeller gave `F#sus4` for the first half of measure 3 and `D` for the second, a false change. Use the relative-pc conflict and "new chord on new bass" tests (10.2) instead; keep the labeller only as an optional debugging aid |
| 5.3 mid-bar | **Replaced** by the revised 5.3 above (chord window + register guard, not note length) |
| 5.4 bar line | D1 = hold on inversion (decided) |
| 5.6 tests | Add: `test_arpeggio_top_note_is_not_an_anchor` (`B3 F#4 B4 C#5 D5`, 61 bpm), `test_short_bass_two_chords_per_bar` (`G3 D4 G4 | A3 E4 C#5` with RH), `test_slow_tempo_threshold_uses_real_quarter` (61 bpm), keep the 9.4 list |
| Score 1 | still a test case (`A2` then long `E3` with RH melody must change; `G2 E3 B3` must not) |
| 9.2 (`D2` guess at second 9) | **Withdrawn**: the screenshot and your `D5` are explained by score 2 |

### 10.6 Still open

1. `max_hold_bars` 3 or 4 (4 is fine for both scores).
2. OK to implement the prototype step first (10.2) and show you the beat-by-beat verdict table before changing `build`?


---

## 11. Revision 3: rule validated with a scratch prototype (final, for the implementer)

Status: the plan is meant to be handed to another model to implement. I did **not** change any repository file. I wrote a
throw-away script, `pedal_rule_prototype.py` (attached; NOT repository code), that applies the rule below to both scores and to
the four unit-test scenarios and prints where the pedal changes and why. The implementer can use it as an executable reference
and as a source of expected values, then port the rule into `auto_pedal.py`.

**Decided:** `max_hold_bars = 4` (unchanged default). Inversion over the same chord keeps the pedal (D1 = hold).

### 11.1 Final rule (replaces the constants and conditions in 5.3, 5.4 and 10.2)

Definitions, per candidate (a left-hand note struck on a beat, or at a bar line the lowest note of the left-hand attack cluster):

- `old`: pitch classes of **both hands** struck since the current pedal went down (and still counted while sounding), up to the candidate.
- `new`: pitch classes of both hands struck from the candidate to the **end of the bar**.
- A pitch class "is in" a set when it holds at least **7 %** of that window's sounding time (`W_MIN = 0.07`; 12 % was too high:
  the passing `B4` in score 1 measure 2 was lost).
- `old_bass` = pitch class of the bass note the current pedal was pressed on; `nb` = pitch class of the candidate.

Mid-bar candidates (beats 2 to 4) must pass **all** of the guards:

1. **Register:** `candidate.pitch <= lowest_LH_pitch_since_the_press + 7` (a fifth). `D5` over `B3` fails (15); `A3` over `G3`
   passes (2); `G4` over `G3` fails (12), which is what keeps the `G4` at beat 2 of score 2 measure 2 from being an anchor.
2. **It is the bass of its own window:** no lower left-hand note is struck later in the window up to the bar end (`E G`
   chord notes before a lower `G` bass are not anchors).
3. **Window:** from the candidate to the bar end is at least `mid_min_beats` (2) beats.
4. **Gap:** at least 1 beat after the press (`eps = 0.02 s`).

Then it is a change when **any** of:

- **(a) conflict:** `old | new`, measured from `old_bass`, contains a minor second (1), a tritone (6), or both a minor
  and a major third (3 and 4).
- **(b) new chord on the new bass, mid-bar = strict:** a major or minor triad rooted on `nb` has **all three** tones in `new`,
  and at least one of its tones is **not** in any pitch class struck since the press.
- **(c) bass-only texture** (`new` holds no pitch class besides `nb`): `nb != old_bass` (the old test; keeps the existing bass-only tests).

Bar line (left-hand attack cluster in `[start - 0.2q, start + 0.3q)`): same (a) and (c); (b) is **relaxed**: `nb` plus **at
least one** other tone of a major or minor triad on `nb` is in `new`, and the triad has a tone absent from `old`. Also change when
`held >= max_hold_bars`. No left-hand attack at the bar line: hold (unchanged behaviour). A bass that moves inside a chord whose tones
have all been played under this pedal (Em with `G2`) therefore holds.

`q` here must come from the **fixed** tempo map (5.1); the prototype takes the beat grid from `bar_times`.

### 11.2 Results (hands = `file`)

Score 2, `anh-dau-tu-luc-em-di.musicxml`, measures 2 to 9 (`b` = rule, `a` = conflict):

| Where | Result | Matches your expectation |
|---|---|---|
| m2 beat 1 | DOWN | yes |
| m2 beat 3 | change (a): `C#` over the old `G` | **yes: two chords in m2** |
| m3 beat 1 | change (b): Bm over the A chord | yes |
| m3 beat 3 (`D5`) | skipped by the register guard | **yes: one pedal in m3** |
| m4 beat 1 / beat 3 | change / change (same pattern as m2) | yes |
| m5 beat 1 | change; no change at beat 3 | yes |
| m6 to m9 | changes at m6 beat 1, m6 beat 3, m7 beat 1, m9 beat 1; nothing else | plausible, please check by ear |

Pedal spans in this score: **40** (today 50). All of the surplus today is the split at the arpeggio tops and similar notes.

Score 1, `khong-mon-dang-ho-doi.musicxml`:

| Where | Result |
|---|---|
| m2 beat 1 | DOWN |
| m2 beat 2 | change (b): `E3` with `G` and `B` is Em on the new bass, `G` and `B` are new to the pedal |
| m3 | none (tied bass held) |
| m4 beat 1 | **hold** (Em over `G2`, all tones already played) |
| m4 beat 3 | none (`G2 E3 B3` is one arpeggio) |
| m6 beat 1 | change (a) (Dm area) ; m7 beat 1, m8 beat 1 change; m9 beat 1 change (c) |

Spans: **62** (today 61), but at different places: the new change at m2 beat 2, and the bar-line re-presses that only move the bass are gone.

Existing unit-test scenarios (synthetic, bar = 2 s, 0.5 s per beat):

| Scenario | Expected | Prototype |
|---|---|---|
| bass `C` then `G` (beat 3) | change | change (c) |
| oompah `C` + `E G`, then `G` + `E G` | no change | no change |
| oompah `C` + `E G`, then `G` + `B D` | change (one only) | one change at beat 3 |
| walking bass `C D E F` | no change | no change |

### 11.3 Limits of this validation (tell the implementer)

- Only two scores and four synthetic scenarios; **no corpus run**. The constants (`0.07`, `+7`, 2 beats, 1 beat) were tuned by
  hand on these cases. Run the regression script from 5.7 on at least three more scores (waltz or oompah, Alberti, a slow
  ballad) before trusting them; any extra change or missing change should be judged by ear, then the table above extended.
- The prototype assumes 4/4, hands from the file, and reads beats from the bar length; the implementation must read
  `beat_times`, `time signature`, early attacks (`attack_window`) and use `_Index` for speed.
- It does not model the release delay, the 30 ms gap, the final tail or `let_go`; those parts of `build` stay as they are.
- The prototype is not optimised (it scans the note list per candidate).

### 11.4 Hand-off checklist for the implementer

1. Tempo fix (5.1) first, with its loader test. Re-run the existing tests: they should pass unchanged.
2. Add pure functions `harmony_conflict`, `triad_on_bass`, `is_bass_candidate` (guards 1 and 2) and the window helpers; unit test each on
   the rows of 11.2.
3. Replace the mid-bar loop and the bar-line decision in `build` (5.3, 5.4) by the rule in 11.1; keep `release_delay`, `press_change`,
   `let_go`, the final tail and `max_hold_bars` as they are. Keep `bass_move_changes_pedal=False` as the switch for the old behaviour.
4. Replace `st["sig"]` by the "pitch classes struck since the press" bookkeeping; remove the half-window signature (M3).
5. Tests from 5.6 and 10.5; add the two scores' expected change lists above (as small synthetic fixtures, not the copyrighted files).
6. Update `ARCHITECTURE.md`, `AUTO_PEDAL_SPEC.md` (sections listed in 9.5) and the docstrings. Do not touch `version.py` or
   `CHANGELOG.md` unless the version is given.
7. Report back with the span counts and the change lists for both scores so the rule can be reviewed.
