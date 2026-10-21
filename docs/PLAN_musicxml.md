# Plan: import MusicXML (.xml / .musicxml / .mxl)

Vietnamese summary for the user first, then the full implementation brief.

## TL;DR (tiếng Việt)

File MusicXML **không chứa sẵn hình vẽ / cách vẽ note** — nó chỉ chứa *dữ liệu ký âm*
(pitch chính xác có dấu hóa, hình nốt `<type>`, dấu chấm `<dot/>`, khuông `<staff>` 1/2,
số ngón `<fingering>`, nhịp, tempo, dấu nối `<tie>`). App vẫn tự vẽ bằng engine của mình
(`staff.py` + font Leland trong `notation.py`, và thanh nốt rơi trong `main.py`).

Cái XML mang lại là **chính xác hơn MIDI**:
- tay trái/phải biết chắc từ `<staff>1</staff>` / `<staff>2</staff>`, không phải đoán;
- dấu hóa đúng (C# hay Db), hình nốt đúng (nốt đen / móc đơn / chấm dôi) thay vì suy từ giây;
- số ngón tay có sẵn nếu file có ghi;
- vạch nhịp, dấu nối đúng như bản gốc.

Vì vậy kế hoạch gồm 3 phần:
1. **Đọc MusicXML** thành `MidiSong` giống hệt MIDI (dùng lại toàn bộ hands / fingering /
   staff / editor sẵn có).
2. **Điền ngón tay còn thiếu**: file XML có thể ghi ngón chỗ này chỗ kia (như
   `output.xml`: 424/455 nốt có ngón, 31 nốt không). Những nốt đã có ngón thì giữ nguyên,
   những nốt thiếu thì `fingering.py` giải tiếp — nhưng *có biết* các ngón đã cho để nối
   tiếp cho liền mạch (xem §3).
3. **Một mục trong Settings** để chọn nguồn dữ liệu: `MusicXML: File / Auto`
   — lấy tay + ngón + dấu hóa từ file, **hay** để project tự chia tay + tự giải ngón
   như với MIDI.

Ta giữ thêm thông tin ký âm (spelling, hình nốt) trong `Note` để khuông nhạc vẽ đúng
giọng/dấu hóa — đây là phần "xài cách vẽ theo XML" mà bạn hỏi.

**Chốt với bạn:** (1) OK — ngón thiếu thì planner điền thêm; (2) Export ra `.musicxml`
(hoặc `.mxl`) giữ đúng ngón tay đang chỉnh, xem §5; (3) dấu lặp sẽ **được chơi đúng theo
kí âm** (repeat/volta D.C./D.S. đều mở rộng), xem §2.9.

---

## 1. What MusicXML gives us (verified against the sample scores)

I parsed every file in `D:\Project\pianoplayer-2.2.0\scores\` with a scratch parser. All 12
`.xml` + 2 `.mxl` parse into a correct measure timeline (`50-nam-ve-sau.mxl` → 123.4 s, matching
the MIDI's 123.3 s). Findings:

| Tag | Use |
|---|---|
| `<score-partwise>` / `<part-list>/<score-part>` | parts (usually one piano part) |
| `<attributes><divisions>` | ticks per quarter; **can change per measure** |
| `<attributes><key><fifths>` | key signature → correct accidentals |
| `<attributes><time>` | beats/beat-type, for bar & beat times |
| `<attributes><clef number>` | clef per staff |
| `<note><pitch><step>/<alter>/<octave>` | exact pitch *and spelling* (C# vs Db) |
| `<note><duration>` | length in divisions |
| `<note><type>`, `<dot>` | printed note value (quarter, eighth, dotted…) |
| `<note><staff>` (1/2) | **which hand** — the whole point |
| `<note><chord>` | this note starts with the previous one |
| `<note><rest>` | skip |
| `<note><grace>` | zero-duration ornament |
| `<note><tie type="start|stop">` | merge into one longer note |
| `<note><notations><fingering>` | printed fingering, when present |
| `<backup>` / `<forward>` | move the cursor within a measure (voices) |
| `<direction><sound tempo>` / `<metronome>` | tempo map |

Not present in the samples: no `<fingering>` at all. But the *other* project's own output
(`D:\Project\pianoplayer-2.2.0\output.xml`) does carry them, and it is the reference for what
we must be able to round-trip:

- 2 `<part>`s (`P1` treble, `P2` bass — no `<staff>` element at all), so the part-order rule
  below is the one that matters here;
- `<divisions>10080</divisions>` (very fine, from music21), `<sound tempo="80">`;
- `<fingering alternate="no" substitution="no">1</fingering>` inside
  `<notations><technical>` on **424 of 455 notes** — P2 is 226/226 fingerings, P1 is 198 of 229
  (the missing 31 are measures 21-24 and 28-29, a passage the annotator skipped);
- exactly the "chỗ ghi chỗ không" case you described, so partial fingering is the normal case,
  not an edge case;
- neither `<tie>` nor `<repeat>` in this file, but `couperin_baricades.xml` has 70 tempo
  changes, so the tempo map must be real.

## 2. New module `musicxml_loader.py`

No new dependency: `zipfile` + `xml.etree.ElementTree` are stdlib. (Rejected `music21`:
tens of MB, slow import, and it would have to be added to the Nuitka build.)

```python
MUSICXML_EXTENSIONS = (".xml", ".musicxml", ".mxl")

def is_musicxml(path) -> bool
def read_musicxml(path) -> MidiSong        # main entry point, mirrors load_midi()
```

`read_musicxml` steps (same three steps `load_midi` uses, so `song.cleanup` works the same):

1. **Open** — plain text XML, or an `.mxl` zip: read `META-INF/container.xml` for the rootfile
   (fall back to the first non-`META-INF` `.xml` entry, e.g. `score.xml`).
   `ET.fromstring` the bytes; namespaces are stripped from tags (MuseScore writes none, but
   other exporters do).
2. **Walk measures once**, across all parts in measure order, keeping a running
   *(division → seconds)* map:
   - current `divisions`, `bpm` (default 120), `beats`/`beat-type`;
   - `<sound tempo>` and `<metronome><per-minute>` update bpm;
   - measure length = `beats * (4/beat_type) * divisions` *at that measure's bpm* → `bar_times`;
   - `beat_times` from the time signature;
   - per part/measure: cursor in divisions, `<chord>` notes reuse the previous start,
     `<backup>`/`<forward>` move the cursor, `<grace>` notes get a small fixed duration and
     are flagged;
   - pitch = `(octave+1)*12 + STEP[step] + alter`, then `_fit_to_piano`;
   - hand from `<staff>` (1 = RIGHT, 2 = LEFT); a part with no `<staff>` and more than one
     part → lower part LEFT, upper part RIGHT;
   - finger from `<notations><fingering>` when it is 1..5;
   - velocity: `<dynamics>` (p/mf/f…) mapped to 40..110, default 64;
   - tie merge: a note whose `<tie type="start">` is followed by the same (part, staff, voice,
     pitch) with `type="stop"` is merged into one note (start of the first, end of the last).
   - **2.9 Repeats** — walk the measure list *as the signs say it is played*, then join the
     unrolled sequence into one timeline. This is what "chơi theo kí âm chính xác" means:
     - `<barline><repeat direction="forward|backward" times="n">` — play the section between
       them `times` (default 2) times;
     - voltas `<barline><ending number="1|2">` — first pass takes ending 1, later passes
       skip it and take the endings that follow;
     - `<direction><sound dacapo="yes">` / `dalsegno` / `<segno>` / `<coda>` /
       `<sound tocoda="yes">` / `<sound fine="yes">` — the written-out jump order;
     - each unrolled pass gets its own measure times (`bar_times` grows with it), so the
       staff's paging and the falling notes line up with what is heard.
     A score with no such signs (all our samples except any volta) behaves exactly as before.
     Where a score's signs can't be resolved safely (a `tocoda` with no matching `<coda>`),
     the sign is ignored and `song.cleanup` says so — never a crash or a silent truncation.
3. **Hands** — when `use_file=True` (see §3) the `<staff>` hand is kept as-is and
   `hand_split` is *not* run; when `use_file=False` the staff hand is dropped
   (`hand=None`) and the normal `hand_split.split_hands` path runs exactly as in MIDI.
   Either way this happens in `load_musicxml`'s caller, the same way `load_midi` does it.
4. **Tracks** — one `TrackInfo` per `<score-part>`: index, `part-name`, MIDI program from
   `<midi-program>` minus 1 (`TrackInfo.program` is 0-based, like pretty_midi), note count,
   and the hand when every note of the part is one hand.

`MidiSong.controls` is empty (nothing to convert: MusicXML has no pedal CCs here), and
`raw_controls` too, so the pedals indicator simply stays off.

## 3. Fingering: three sources, not two

Your refinement: the choice is not "file or project" but **three** settings, and they are the
same thing the app already does for MIDI, PIG and the editor — the existing `plan_fingering`
flags (`fixed`, `repair`, `repaired`) and the editor's per-note `user_set` already are the
three modes. So this is mostly plumbing, plus one new soft bias for `Fill in`.

`musicxml_fingering` (settings.json, default `"fill"`):

| Mode | What it does | How |
|---|---|---|
| **Auto** (`"auto"`) | Project finds every finger, the file's fingers ignored | pass no `fixed`, and drop `Note.finger` before planning — exactly today's MIDI path |
| **File** (`"file"`) | The file's fingers used exactly as written, even where unplayable | `fixed = {id(n): n.finger for n in notes if n.finger}` — every written finger **hard**, nothing else planned. `fingering.py:792-794` returns only the states that agree, so a written 3 comes back 3 even if it is a stretch a hand can't hold. `hands.HandAnimator.repair` is off (the editor already calls `build_hands(..., repair=False)`), so the drawing shows what the file says |
| **Fill in** (`"fill"`) | The file's fingers kept, the gaps planned in context — and a bad written finger may be corrected | §3 below |

**Fill in** (the earlier §3, now the middle of three):

1. **`context_fixed(groups, vpitch, hand, ...)`** — a relaxed one-pass solve over the same
   groups with **no given fingers at all** (figures on, as today). Cheap: one beam pass. It
   gives every note a plausible finger and, more usefully, the *hand position* the passage
   sits in.
2. **Plan with the context** — the real call passes
   `fixed={id(n): n.finger for n in notes if n.finger}` and lets the beam choose the rest, with
   each un-given note's **finger-1 cost biased toward its context finger**:
   `unary_cost(p, f) += FILL_CONTEXT_W * |f - context_fixed[id(n)]|`. `FILL_CONTEXT_W` is a new
   *soft* entry in `fingering.W`, scaled by the pianist's `figure` behaviour like the other
   suggestions. Soft, not a pin, so the planner still has the last word.
3. **Correcting a written finger.** A written finger stays hard **except** where keeping it
   makes the chord unplayable — the same test `repair=True` already uses
   (`min(local) >= IMPOSSIBLE`, `fingering.py:875`). Then the written fingers that can't stand
   are relaxed one at a time so a playable chord wins, and the notes that changed go into
   `repaired` — exactly the existing repair flow, so `hands.HandAnimator.repaired` and the
   editor's difficulty view keep working. On the real `output.xml` check this fires: a written
   octave 1-5 is fine, a written 4-5 octave would be corrected.
4. **Nothing is lost** — `plan_fingering` returns only `{id(note): finger}`, but every note
   keeps its original `Note.finger` (that is where the file's mark lives), so the editor can
   still show "this is what the file said" against "this is what is played": today's
   `editor.load_song` uses `planned[n.hand].finger_for(n)` for the shown finger and
   `HandAnimator.repaired` for the ones changed to be playable. The same two notions cover the
   Fill in mode unchanged.
5. **Report it** — `song.cleanup` gets
   `"kept 424 fingering marks from the file, planned 31, corrected 2"`, the same style as the
   MIDI sanitizing report.

Tests for this (§6) check each of the three modes on the same fixture: Auto ignores the
written 3, File keeps an unplayable written 4-5, Fill in keeps a playable written 3 and
corrects the unplayable one.


## 4. Keeping the printed notation (`Note` extra fields)

`staff.py` currently derives everything from the MIDI number: `diatonic_step(pitch)` maps
1→C#, 3→D#, 6→F#, 8→G#, 10→A# (`PITCH_MAP`, always sharps), and the shape comes from
`note_value(duration, beat_len)`. That is why a Db in the XML would print as C#.

Minimal, backwards-compatible change — three `Note` fields, all defaulted so every existing
constructor and test keeps working:

```python
@dataclass(frozen=True)
class Note:
    ...
    step: Optional[int] = None      # written diatonic step (octave*7 + C..B); None = from pitch
    alter: int = 0                  # -2..+2, only meaningful with `step`
    printed: Optional[str] = None   # "whole"|"half"|"quarter"|"eighth"|... (from <type>)
```

`staff.py` then uses them only where they exist:
- `diatonic_step(n)` → written `step`/`alter` when set, else today's `PITCH_MAP`;
- the accidental glyph: sharp / flat / natural from `alter` (Leland has all three in
  `notation.py`) instead of always "sharp";
- `note_value` → `printed` when set (already normalised to the module's names), else today's
  duration maths.

MIDI/PIG loading leaves all three `None`/0, so nothing about the existing look changes.
`split_at_bar_lines` (ties across barlines) must carry `step`/`alter`/`printed` onto the
pieces like it carries `finger`; the first piece keeps them.

The **falling notes** over the keyboard and the **editor roll** deliberately keep their plain
blocks (that is this app's own look — see `notation.py`'s docstring), so nothing changes there.

## 5. Settings entries

`app_settings.py` currently lays out 6 rows (`ROWS`); this adds a 7th and an 8th.

**Hand layout** — `musicxml_hands` (settings.json, default `"file"`), two `Button`s:

- **`File`** — the score's own layout: `<staff>1</staff>` → RIGHT, `<staff>2</staff>` → LEFT
  (a part with no `<staff>`, when there are two parts, is upper part RIGHT / lower part LEFT).
  This is what `output.xml` needs.
- **`Auto`** — ignore the file's layout, plan the hands with `hand_split.py` exactly as for a
  MIDI file.

**Fingering** — `musicxml_fingering` (settings.json, default `"fill"`), three `Button`s
(§3):

- **`Auto`** — the project finds every finger, the file's marks ignored.
- **`File`** — the file's fingers exactly as written, playable or not.
- **`Fill in`** — keep the file's fingers, plan the gaps in that hand position, and correct a
  written finger only where keeping it is unplayable.

Both rows sit together under the soundfont/font rows, with the description column explaining
the choice in the file's existing text style; both only affect MusicXML sources, and switching
either reloads a currently-open MusicXML song so the change is visible at once.
`pianist.app_setting("musicxml_hands", "file")` and
`pianist.app_setting("musicxml_fingering", "fill")` are read by `musicxml_loader.load_musicxml`.


## 6. Wiring the format in, and exporting back

| Where | Change |
|---|---|
| `midi_loader.load_song` | `.xml`/`.musicxml`/`.mxl` → `musicxml_loader.read_musicxml`, next to the `.txt` → `read_pig` branch |
| `common.pick_file` / `_MIDI_TYPES` | add `("MusicXML files", "*.xml *.musicxml *.mxl")` and put it in the combined first entry, and in the title strings |
| `main.py` menu + `choose_playback` | dialogs say "Open score file"; `--edit` still opens the editor on it |
| `hands.load_with_hands` | unchanged — it goes through `load_song` |
| `CLI` | `python main.py song.mxl` just works |

**Export (your point 2).** `output.xml` proves the format carries fingering, so the editor
exports a real `.musicxml` rather than a side file:

- `musicxml_loader.save_musicxml(src_path, dst_path, notes, fingers)` — read the source score,
  then for each note-on put the *current* hand and finger into `<notations><technical>`
  (`<fingering>`), add/replace `<staff>` from the note's hand, and leave everything else
  (structure, layout, tempo, dynamics, ties, layout hints) untouched. Notes the editor moved or
  added get a `<note>` of their own; notes deleted lose theirs.
- Round-trip back from `output.xml`: our loader reads its 424 fingerings and its 2-part hand
  layout, we re-finger the blank 31, and export writes all 455 — a file the other project can
  read straight back.
- `editor.save()` picks the writer by extension, like it already picks PIG vs MIDI:
  `.xml` → `save_musicxml`, `.mxl` → `save_musicxml` + zip, `.mid` → `save_fingered_midi`,
  `.txt` → `save_pig`. `common.save_file_dialog` gets the MusicXML entries and
  `default_export_name` keeps the source's extension.
- A Vietnamese/English message on save: "Exported 455 fingering marks to X" / and, when some
  notes couldn't be matched, the count, as `save_fingered_midi` already reports.


## 6. Tests (`tests/test_musicxml.py`)

Fast, no local data — the fixtures are written into `tmp_path` by the tests themselves:

1. `test_score_partwise_with_two_staves_splits_hands` — a tiny hand-written score: a right-hand
   note on `<staff>1</staff>` and a left-hand note on `<staff>2</staff>` → hands RIGHT/LEFT,
   correct pitches.
2. `test_divisions_and_tempo_make_seconds` — `<divisions>4</divisions>`, `sound tempo="60"`:
   a quarter note is 1.0 s, a bar of 4/4 is 4.0 s, `bar_times` aligns with the measures.
3. `test_chord_and_backup_voices` — a `<chord>` shares its start, a `<backup>` restarts the
   cursor so two voices overlap in time in one measure.
4. `test_ties_are_merged` — two tied quarters → one 2.0 s note.
5. `test_grace_note_is_short` — a grace note gets a small duration and does not shift the
   quarter after it.
6. `test_mxl_zip_is_read` — the same score zipped as `score.xml` + `META-INF/container.xml`
   loads and matches the plain `.xml` result.
7. `test_alter_keeps_the_written_spelling` — `Bb` stays `alter=-1` / its `step`, while the MIDI
   number is still 70/58 (so playback is unchanged but the staff can print a flat).
8. `test_fingering_from_file_is_kept` — a `<fingering>3</fingering>` ends up on `Note.finger`,
   and `song.cleanup` reports "kept 1, planned N".
9. `test_fingering_modes_on_one_score` — one fixture with a written 3 (playable):
   - `File` → the 3, and a following un-playable written 4-5 octave stays 4-5;
   - `Auto` → no written finger used (`Note.finger` dropped from planning);
   - `Fill in` → the 3 kept **and** the unplayable 4-5 corrected, with the changed notes in
     `HandAnimator.repaired`;
   `song.cleanup` reports the counts of each (kept / planned / corrected).
10. `test_fill_in_continues_the_written_run` — a run of four eighths where the file fingers
    only the first and last (3 and 1): the two inner notes get distinct fingers sitting between
    3 and 1 rather than both 3 or both 1, and the outer two are exactly 3 and 1.
11. `test_hands_modes` — `musicxml_hands="file"` keeps the `<staff>` layout; `"auto"` runs
    `hand_split` (a note on staff 2 high in the treble register comes back RIGHT).
12. `test_volta_repeats_are_played` — a 4-bar score with `repeat forward` on bar 1,
    `repeat backward times="2"` on bar 2 and a 2nd ending: the unrolled measures come out as
    1, 2, 1, 3 (4 measures) with the times to match, and `len(song.bar_times)` has one more
    entry than that.
13. `test_musicxml_round_trip` — load a small score with a couple of written fingers, re-finger
    one note by hand, `save_musicxml` to a new file, read it back: the note's `<fingering>` is
    the new one, the other written finger is untouched, and the note count matches.
14. `test_mxl_export_is_a_zip` — the same, saved as `.mxl`, opens with `zipfile` and its
    `score.xml` entry parses.
15. `test_load_song_dispatches_by_extension` — `.xml` and `.mxl` go to the MusicXML loader,
    `.mid` to MIDI, `.txt` to PIG.

Plus one `test_staff.py` test that a `Note` with `step`/`alter`/`printed` set draws a flat and
the printed value instead of a sharp and the duration-derived value (numeric check on
`note_value` / `diatonic_step(note)`), and one `test_core.py` test that a whole MusicXML song
loads through `hands.load_with_hands` and gets finite poses.

## 7. Docs

- `CLAUDE.md` — a `musicxml_loader.py` row in the Modules table, and the load-path note.
- `docs/ARCHITECTURE.md` — a new "## MusicXML import (musicxml_loader.py)" section: the tag
  table, the division/tempo maths, the repeat unrolling, the tie merge, the three fingering
  modes (§3), the `Note` spelling fields, and the two settings.
- `CHANGELOG.md` — a bullet under the current version (I will not bump `version.py`; the
  author sets versions).
- `README.md` — mention `.xml` / `.musicxml` / `.mxl` next to MIDI in the "open a file" text.

## 8. Decisions taken and what is left

Decided with the user:

1. **Three fingering settings** — `Auto` (project plans everything), `File` (the file's fingers
   exactly as written, playable or not), `Fill in` (keep the file's fingers, plan the gaps in
   that hand position, correct a written finger only where it is unplayable). Default `Fill in`
   (§3).
2. **Hand layout setting** — `File` (the score's `<staff>` / part layout) or `Auto` (the
   project's `hand_split`), default `File` (§5).
3. **Export** — the editor writes `.musicxml` (and `.mxl` when the name says so), carrying the
   current hand and finger of every note, like `output.xml` does. No PIG side file.
4. **Repeats** — played as the score's signs say: repeat/volta/D.C./D.S./coda are unrolled
   (§2.9). Not "play everything once".

Still open, low risk:

5. **Two parts instead of two staves.** Some scores are two `<part>`s (e.g. `bach_joy.xml`,

   `output.xml`). Rule: `<staff>1</staff>` → RIGHT and `<staff>2</staff>` → LEFT; a part with a
   single staff gets its hand from its register relative to the other part. This is what
   `output.xml` needs, since it has no `<staff>` at all.
5. **Out-of-range pitches** are octave-shifted by the existing `_fit_to_piano`, same as MIDI.
6. **Volta/D.C. display** — the unrolled passes mean a repeated bar appears more than once in
   `bar_times`; the staff paging shows the music as played. That is intended (it matches the
   falling notes), and it is stated in the docs.
7. **Mode `File` and the hands** — in `File` mode nothing repairs an impossible written
   fingering, so a hand asked to hold, say, 4-5 on an octave is drawn reaching it. The app
   already has that path today (the editor builds hands with `repair=False`), so it is a known
   look, not a new one; the fingers may cross or a chord may be rolled. Anyone who does not
   want that picks `Fill in`.
8. **Chord with more written fingers than a hand has** — `_states` falls back to
   `[tuple(g or 1 for g in given)]` (`fingering.py:805`), so the extra notes are kept but the
   hand can only animate the first five; that is today's behaviour for a sixth note in one
   hand's chord (`hands.HandAnimator.__init__` drops the notes without a finger).

