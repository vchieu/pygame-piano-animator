#!/usr/bin/env python3
"""Apply the review fixes to the repo.   usage: python apply_review_fixes.py [repo_root]
Every edit is an exact-text replacement that must match exactly once; if any edit of a file
fails, that file is left untouched and reported. Run `git diff` afterwards."""
import os
import sys

ROOT = sys.argv[1] if len(sys.argv) > 1 else "."

REVOICE = '''

def revoice(clean: Clean, voices: str = "single", chord_end: str = "median", tidy: bool = True):
    """
    Voice `clean.notes` again, per hand as they now carry it. Needed after the notes were edited
    on the clean clock (a note moved to the other hand may overlap that hand's held notes, which
    a notation program prints as a second voice). Returns (Clean, [(old note, new note)]); a note
    that lands on a key its hand already plays at that unit is merged away (no pair).
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
        for u, ends, ns in _voice(per[hand], voices, chord_end, rest_min, counters, tidy):
            for n, e in zip(ns, ends):
                pairs.append((n, replace(n, start=u * unit, end=e * unit, hand=hand,
                                         track=0 if hand == RIGHT else 1)))
    pairs.sort(key=lambda p: (p[1].start, p[1].pitch))
    notes = [new for _, new in pairs]
    rh = sum(1 for n in notes if n.hand == RIGHT)
    stats = dict(clean.stats, notes_out=len(notes), right=rh, left=len(notes) - rh)
    return replace(clean, notes=notes, stats=stats), pairs
'''

EDITS = {
    "normalize.py": [
        # the CLI must not clean a song that load_song already cleaned (auto_normalize on)
        ("    song = load_song(a.src)\n    clean = normalize(song.notes,",
         "    song = load_song(a.src, normalize=False)      # (normalized once, below - not twice)\n"
         "    clean = normalize(song.notes,"),
        # tiny tidy-ups
        ("    import os\n    os.replace(tmp, path)", "    os.replace(tmp, path)"),
        ("import math\nimport struct\n", "import math\nimport os\nimport struct\n"),
        ("        counters[\"merged\"] += before - sum(1 for _ in out_notes if _.hand == hand)",
         "        counters[\"merged\"] += before - sum(1 for o in out_notes if o.hand == hand)"),
        ("# --------------------------------------------------------------------------- #\n"
         "# Output: a MIDI file with two staves",
         REVOICE.lstrip("\n") + "\n\n"
         "# --------------------------------------------------------------------------- #\n"
         "# Output: a MIDI file with two staves"),
    ],
    "editor.py": [
        ("from normalize import clean_song, write_midi", "from normalize import clean_song, revoice, write_midi"),
        ("""            fingers = {id(n): n.hand + ("g" if n.gliss else (str(f) if f else ""))
                       for n, f in zip(self.notes, self.finger) if n.hand}
            clean = replace(self.song.clean, notes=self.notes, controls=self.song.controls)
            count = write_midi(path, clean, key_fifths=self.song.key_fifths, fingers=fingers)""",
         """            label = {id(n): n.hand + ("g" if n.gliss else (str(f) if f else ""))
                     for n, f in zip(self.notes, self.finger) if n.hand}
            # hands may have been edited since the clean: voice each hand again (one voice a
            # hand), and carry every label over to the note that replaces its own
            clean, pairs = revoice(replace(self.song.clean, notes=self.notes, controls=self.song.controls))
            fingers = {id(new): label[id(old)] for old, new in pairs if id(old) in label}
            count = write_midi(path, clean, key_fifths=self.song.key_fifths, fingers=fingers)"""),
    ],
    "midi_loader.py": [
        # MusicXML / PIG scores already have exact bars and values: only an explicit request cleans them
        ("    return maybe_normalize(song, normalize, normalize_options)\n",
         "    if normalize is None and not path.lower().endswith((\".mid\", \".midi\", \".kar\")):\n"
         "        normalize = False            # (auto_normalize is for played-in MIDI; ask for the rest)\n"
         "    return maybe_normalize(song, normalize, normalize_options)\n"),
    ],
    "tests/test_normalize.py": [
        ("""    p = midi_path("demo_song.mid")
    pianist.set_app_setting("auto_normalize", True)
    assert midi_loader.load_song(p).normalized         # None follows the app setting
    assert not midi_loader.load_song(p, normalize=False).normalized
    pianist.set_app_setting("auto_normalize", False)
    assert not midi_loader.load_song(p).normalized""",
         """    p = midi_path("demo_song.mid")
    old = pianist.app_setting("auto_normalize", False)
    try:                                               # (the user's own setting comes back)
        pianist.set_app_setting("auto_normalize", True)
        assert midi_loader.load_song(p).normalized     # None follows the app setting
        assert not midi_loader.load_song(p, normalize=False).normalized
        pianist.set_app_setting("auto_normalize", False)
        assert not midi_loader.load_song(p).normalized
    finally:
        pianist.set_app_setting("auto_normalize", old)"""),
        ("\n\ndef test_writer_fingers_survive_a_round_trip(tmp_path):",
         '''

def test_revoice_after_a_hand_edit_keeps_one_voice_a_hand():
    """A note moved to the other hand may overlap that hand's held note: revoice cuts it there."""
    from dataclasses import replace
    notes, _ = _piece(bars=6)
    clean = N.normalize(notes)
    r = next(n for n in clean.notes if n.hand == "R" and any(
        l.hand == "L" and l.start < n.start < l.end for l in clean.notes))     # starts inside a left-hand note
    moved = [replace(n, hand="L", track=1) if n is r else n for n in clean.notes]
    starts = sorted({n.start for n in moved if n.hand == "L"})                 # (the overlap is really there)
    assert any(max(n.end for n in moved if n.hand == "L" and n.start == a) > b + 1e-9
               for a, b in zip(starts, starts[1:]))
    edited, pairs = N.revoice(replace(clean, notes=moved))
    assert len(pairs) == len(moved) and edited.stats["left"] == clean.stats["left"] + 1
    for hand in "RL":
        starts = sorted({n.start for n in edited.notes if n.hand == hand})
        for a, b in zip(starts, starts[1:]):
            assert max(n.end for n in edited.notes if n.hand == hand and n.start == a) <= b + 1e-9
    assert edited.notes == [new for _, new in pairs]
    assert N.revoice(edited)[0].notes == edited.notes             # already one voice: nothing moves


def test_writer_fingers_survive_a_round_trip(tmp_path):'''),
    ],
}

bad = 0
for rel, edits in EDITS.items():
    path = os.path.join(ROOT, rel)
    if not os.path.exists(path):
        print("MISSING", path); bad += 1; continue
    text = open(path, encoding="utf-8").read()
    for i, (old, new) in enumerate(edits):
        if text.count(old) != 1:
            print("NO MATCH (%d) in %s: edit #%d found %d times - file left unchanged" % (0, rel, i + 1, text.count(old)))
            bad += 1
            break
        text = text.replace(old, new)
    else:
        open(path, "w", encoding="utf-8").write(text)
        print("patched", rel)
sys.exit(1 if bad else 0)
