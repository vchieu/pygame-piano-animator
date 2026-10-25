"""
SCRATCH PROTOTYPE - reference for the auto-pedal fix plan (section 11). NOT repository code.

It simulates the revised harmony rule on a loaded MidiSong (hands from the file) and prints, per measure/beat,
where the pedal would change and why. Assumes 4 beats per bar (the implementation must use beat_times and
the real time signature). Used only to validate the rule on the two scores and the unit-test scenarios.
"""
import bisect
from midi_loader import LEFT, RIGHT
W_MIN = 0.07

def pcs_of(notes, t0, t1, struck_from=None):
    w = {}
    for n in notes:
        if getattr(n, "grace", False) or n.duration <= 0: continue
        if struck_from is not None and n.start < struck_from: continue
        a, b = max(n.start, t0), min(n.end, t1)
        if b > a: w[n.pitch % 12] = w.get(n.pitch % 12, 0) + (b - a)
    tot = sum(w.values()) or 1.0
    return {pc for pc, v in w.items() if v / tot >= W_MIN}, set(w)

def conflict(old_root, union):
    rel = {(p - old_root) % 12 for p in union}
    return (1 in rel) or (6 in rel) or (3 in rel and 4 in rel)

def new_chord_on_bass(nb, new_core, old_all, strict=False):
    for iv in ((0, 4, 7), (0, 3, 7)):
        tones = {(nb + i) % 12 for i in iv}
        # root (the new bass) plus at least one other tone of the triad is heard in the new window,
        # and the triad brings a tone nothing under the current pedal has played
        ok = (tones <= new_core) if strict else (nb in new_core and len((tones - {nb}) & new_core) >= 1)
        if ok and (tones - old_all):
            return True
    return False

def verdict(old_core, old_all, old_bass, new_core, new_bass_pc, bass_only, strict=False):
    if bass_only:
        return new_bass_pc != old_bass, "c:bass-only"
    if conflict(old_bass, old_core | new_core): return True, "a:conflict"
    if new_chord_on_bass(new_bass_pc, new_core, old_all, strict): return True, "b:new-chord-on-bass"
    return False, "same"

def run(song, max_hold=4, verbose=True):
    notes = [n for n in song.notes if n.duration > 0 and not getattr(n, "grace", False)]
    lh = [n for n in notes if n.hand == LEFT]
    lh.sort(key=lambda n: n.start)
    bars = song.bar_times; beats = sorted(song.beat_times)
    out = []; st = None   # st: t_p, bass pitch, low pitch, held
    for k in range(len(bars) - 1):
        b0, b1 = bars[k], bars[k + 1]
        bl = (b1 - b0) / 4.0
        cands = []
        for j in range(4):
            t = b0 + j * bl
            cl = [n for n in lh if t - 0.2 * bl <= n.start < t + 0.3 * bl] if j == 0 else \
                 [n for n in lh if abs(n.start - t) <= 0.25 * bl]
            if cl:
                t0 = min(n.start for n in cl)
                cl = [n for n in cl if n.start - t0 <= 0.04]
                cands.append((j, min(cl, key=lambda n: n.pitch), cl))
        if st is None:
            if cands and cands[0][0] == 0:
                a = cands[0][1]; st = dict(t=b0, bass=a.pitch, low=a.pitch, held=1)
                out.append((k + 1, 1, "DOWN"))
            else:
                continue
        for j, a, cl in cands:
            if j == 0 and out and out[-1] == (k + 1, 1, "DOWN"):
                continue
            at = b0 + j * bl
            if j == 0:
                end = b1
                old_core, old_all = pcs_of(notes, st["t"], at, struck_from=st["t"] - 0.05)
                new_core, new_all = pcs_of(notes, at - 0.04, end, struck_from=at - 0.05)
                bass_only = len(new_core - {a.pitch % 12}) == 0
                ch, why = verdict(old_core, old_all, st["bass"] % 12, new_core, a.pitch % 12, bass_only)
                if not ch and st["held"] >= max_hold: ch, why = True, "max_hold"
                if ch:
                    st = dict(t=at, bass=a.pitch, low=a.pitch, held=1); out.append((k + 1, j + 1, why))
                else:
                    st["held"] += 1
                    if verbose: out.append((k + 1, j + 1, "hold(" + why + ")"))
                continue
            # mid-bar candidate
            if a.pitch > st["low"] + 7:
                if verbose: out.append((k + 1, j + 1, "skip:register")); 
                continue
            if b1 - at < 2 * bl - 1e-6 or at - st["t"] < 1.0 * bl - 0.02:
                continue
            if any(n.pitch < a.pitch for n in lh if at + 0.04 < n.start < b1):
                if verbose: out.append((k + 1, j + 1, "skip:lower-bass-later"))
                continue                                  # a lower bass note follows in the window: not the bass
            old_core, old_all = pcs_of(notes, st["t"], at, struck_from=st["t"] - 0.05)
            new_core, new_all = pcs_of(notes, at - 0.04, b1, struck_from=at - 0.05)
            bass_only = len(new_core - {a.pitch % 12}) == 0
            ch, why = verdict(old_core, old_all, st["bass"] % 12, new_core, a.pitch % 12, bass_only, strict=True)
            if ch:
                st = dict(t=at, bass=a.pitch, low=a.pitch, held=1); out.append((k + 1, j + 1, why))
            elif verbose:
                out.append((k + 1, j + 1, "no(" + why + ")"))
        if st: st["low"] = min(st["low"], *[n.pitch for n in lh if st["t"] - 0.05 <= n.start < b1] or [st["low"]])
    return out
