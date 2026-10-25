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
