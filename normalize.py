"""
normalize.py - Clean a played-in piano MIDI into a two-staff score: one tidy voice per hand.

A recorded or hand-made MIDI file says *when the keys went down and up*, not what a score
would print. Opened as it is, it gives a staff full of odd values (a dotted 16th here, a
32nd there, triplets where the tempo and the music disagree, two voices wherever a held
note overlaps the next one). This module turns the notes - with their hands already decided
(hand_split.py / the fingering editor) - into what an engraver would start from:

  1. Pulse.     The notes' onsets are a comb: find the period whose phases bunch up
                (Rayleigh statistic over all onsets), refine it to ~0.002 %, and read the
                tempo off it. The file's own tempo event is ignored - a file can say 120
                while the music sits on a 180 grid (then every note looks like a triplet).
  2. Snap.      A phase-locked loop follows the player through the piece (slow drift, a
                little rubato) and puts each chord on a grid cell: the coarse cell, or -
                only when that is clearly a worse fit - a half cell (the 32nd of an
                ornament). Chords are notes within CHORD_TOL of one another.
  3. Bars.      The beat is a multiple of the cell that puts the tempo in range (180 -> 90
                bpm, so the pulse reads as eighths). The downbeat is the beat position where
                left-hand and long notes land, preferring "the first note is beat 1".
  4. Voices.    Each hand becomes one voice: a chord shares one length, and ends where the
                next chord of that hand begins (legato fill); a real gap stays a rest. Held
                notes that overlapped the next onset are cut there (counted in the report;
                voices="keep" keeps them, and a notation program then shows a second voice).
  5. Output.    `Clean` (notes on an exact clock, bar and beat times) and `write_midi`: a
                format-1 file with a conductor track, "Right hand" on channel 0 and "Left
                hand" on channel 1 - two staves, whatever opens it.

Only numpy and the standard library are used. Typical use:

    from midi_loader import load_song
    from normalize import normalize, write_midi
    song = load_song("song.mid")                      # hands from markers / hand_split
    clean = normalize(song.notes, controls=song.raw_controls)
    write_midi("song_clean.mid", clean)

or `python normalize.py song.mid [out.mid]`. `clean_song(song)` gives a MidiSong the player
and the staff view can use directly.
"""
from __future__ import annotations

import math
import os
import struct
from dataclasses import dataclass, field, replace
from typing import List, Optional, Sequence, Tuple

import numpy as np

LEFT, RIGHT = "L", "R"
MIDDLE_C = 60

CHORD_TOL = 0.045        # s: onsets this close to a chord's first note belong to it
MIN_CELL = 0.14          # s: the finest cell worth looking for (a hand can't play cleaner)
MAX_CELL = 0.80
SEARCH_T = 40.0          # s of music the coarse search for the cell looks at
CELL_KEEP = 0.90         # the largest period reaching this share of the best comb score is the cell
PLL_GAIN = 0.15          # how fast the grid follows the player (share of each error taken up)
FINE_TOL = 0.28          # of a cell: further from the coarse grid than this, a half cell may fit better
FINE_RATIO = 0.5         # ... when its error is under this share of the coarse error
MAX_BPM = 130.0          # the beat (quarter) is the multiple of the cell that keeps the tempo under this
REST_MIN_BEAT = 0.75     # a gap shorter than this share of a beat between a hand's chords is not written as a rest
PPQ = 480
# Written note values, in units (half a cell): 32nd, 16th, dotted 16th, 8th, dotted 8th, quarter,
# dotted quarter, half, dotted half, whole. A notation program shows anything else (10, 13, ...)
# as tied pieces, and it is there that it starts a second voice.
STD_LEN = (1, 2, 3, 4, 6, 8, 12, 16, 24, 32)


# --------------------------------------------------------------------------- #
# Result
# --------------------------------------------------------------------------- #

@dataclass
class Clean:
    """A cleaned score: notes on an exact clock, and what a writer or a staff view needs."""
    notes: list                          # note objects (hands kept), start/end in seconds on the new clock
    bpm: float                           # the tempo of that clock (a quarter note)
    beats_per_bar: int
    unit: float                          # seconds per unit (a half cell: a 32nd at the default tempo)
    units_per_beat: int
    bar_times: List[float]
    beat_times: List[float]
    pickup_end: float = 0.0
    report: List[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    controls: list = field(default_factory=list)     # [(time, controller, value)] on the new clock
    # kept for mapping other times (pedal) from the old clock to the new
    _origin: float = 0.0
    _unit_old: float = 0.0
    _u0: int = 0
    _warp_t: Optional[np.ndarray] = None
    _warp_d: Optional[np.ndarray] = None

    def map_time(self, t: float) -> float:
        """A time on the player's clock -> the same moment on the cleaned clock (seconds)."""
        d = float(np.interp(t, self._warp_t, self._warp_d)) if self._warp_t is not None and len(self._warp_t) else 0.0
        return ((t - self._origin - d) / self._unit_old - self._u0) * self.unit


# --------------------------------------------------------------------------- #
# 1. The pulse
# --------------------------------------------------------------------------- #

def _chords(notes: Sequence, tol: float = CHORD_TOL) -> List[list]:
    """Notes in start order, grouped: a note joins the group when it starts within tol of its first."""
    out: List[list] = []
    for n in sorted(notes, key=lambda n: (n.start, n.pitch)):
        if out and n.start - out[-1][0].start <= tol:
            out[-1].append(n)
        else:
            out.append([n])
    return out


def _comb(ts: np.ndarray, periods: np.ndarray) -> np.ndarray:
    """z(P) = mean of exp(2 pi i t / P) for every period (chunked: a long piece has thousands of onsets)."""
    out = np.empty(len(periods), dtype=complex)
    step = max(1, 2_000_000 // max(1, len(ts)))
    for i in range(0, len(periods), step):
        p = periods[i:i + step]
        out[i:i + step] = np.exp(2j * np.pi * ts[None, :] / p[:, None]).mean(axis=1)
    return out


def find_cell(times: Sequence[float]) -> Tuple[float, float, float]:
    """
    (cell, origin, strength): the grid the onsets sit on.

    `cell` is the largest period whose onsets bunch up nearly as well as the best one's
    (so a piece of quarter notes gets the quarter, a piece with 16ths gets the 16th, not
    its half), `origin` a time on the grid, `strength` the comb score of it (0..1).
    """
    ts = np.asarray(sorted(times), dtype=float)
    if len(ts) < 8:
        raise ValueError("too few notes to find a pulse")
    # Coarse search on the first SEARCH_T seconds. A period P is only told from its neighbours
    # when the phase over the window moves by well under a cell: step <= 0.1 P^2 / window
    # (so the steps grow with P; a fixed step would lose the peak on a long piece).
    window = ts[ts <= ts[0] + SEARCH_T]
    if len(window) < 8:
        window = ts[:64]
    span = max(1.0, float(window[-1] - window[0]))
    periods = []
    p = MIN_CELL
    while p < MAX_CELL:
        periods.append(p)
        p += 0.1 * p * p / span
    periods = np.asarray(periods)
    r = np.abs(_comb(window, periods))
    ok = np.nonzero(r >= CELL_KEEP * r.max())[0]
    j = int(ok[-1])
    while j > 0 and r[j - 1] > r[j]:                     # up to the top of that peak
        j -= 1
    # Then the whole piece, finely: +-1.2 % in steps of 1e-5 (a cell moves 0.09 over 1200 s at 0.14 s)
    fine = periods[j] * (1.0 + np.linspace(-0.012, 0.012, 2401))
    z = _comb(ts, fine)
    k = int(np.argmax(np.abs(z)))
    cell = float(fine[k])
    origin = float((np.angle(z[k]) / (2 * np.pi) * cell) % cell)
    return cell, origin, float(abs(z[k]))


# --------------------------------------------------------------------------- #
# 2. Snapping, following the player
# --------------------------------------------------------------------------- #

def _snap(groups: List[list], cell: float, origin: float):
    """
    Put every group of notes on the grid, in units of half a cell.

    Returns (units, drift, stats): the unit each group lands on, the grid's offset from the
    origin at that group (seconds - how far the player had drifted) and counters.
    """
    half = cell / 2.0
    # The grid was fitted to the whole piece; the player may have started off it (tempo
    # drift moves the start and the end opposite ways). Begin where the first chords sit.
    head = np.array([sum(n.start for n in g) / len(g) for g in groups[:24]])
    z0 = np.exp(2j * np.pi * (head - origin) / cell).mean()
    d = float(np.angle(z0) / (2 * np.pi) * cell) if abs(z0) > 0.3 else 0.0
    prev = -10 ** 9
    units, drift = [], []
    stats = {"coarse": 0, "fine": 0, "outliers": 0, "on_grid": 0}
    for g in groups:
        t = sum(n.start for n in g) / len(g)
        x = t - origin - d
        k = math.floor(x / cell + 0.5)
        e = x - k * cell
        if abs(e) <= FINE_TOL * cell:
            u = 2 * k
            d += PLL_GAIN * e
            stats["coarse"] += 1
            stats["on_grid"] += 1
        else:
            uf = math.floor(x / half + 0.5)
            ef = x - uf * half
            if uf % 2 and abs(ef) < FINE_RATIO * abs(e):
                u = uf
                stats["fine"] += 1
            else:
                u = 2 * k
                stats["outliers"] += 1
        u = max(u, prev)
        prev = u
        units.append(u)
        drift.append(d)
    return units, drift, stats


# --------------------------------------------------------------------------- #
# 3. Tempo, bars
# --------------------------------------------------------------------------- #

def pick_beat(cell: float, max_bpm: float = MAX_BPM) -> Tuple[int, float]:
    """(cells per beat, bpm): the smallest power of two of cells that keeps the quarter note under max_bpm."""
    c = 1
    while 60.0 / (cell * c) > max_bpm and c < 16:
        c *= 2
    raw = 60.0 / (cell * c)
    nearest = round(raw)
    bpm = float(nearest) if nearest and abs(raw - nearest) / raw < 0.0025 else round(raw, 2)
    return c, bpm


def _downbeat(items: List[tuple], bar_u: int, upb: int) -> int:
    """
    Where the bar lines fall: the unit (mod bar_u, a whole cell) a bar starts on.

    The grid only fixes the cells, not which of them opens a bar, so every cell of a bar is
    a candidate. `items` = (unit, hand, pitch, length in units) of every note. Each note
    votes for the cell it sits on: left-hand notes count more, the lowest quarter of them
    (the bass lands on 1) and long ones more still. The first note's own cell wins unless
    another one beats it by a quarter - a piece starting on a pickup is the rarer case.
    """
    lh = sorted(p for _, h, p, _ in items if h == LEFT)
    low = lh[len(lh) // 4] if lh else -1
    score = [0.0] * bar_u
    for u, hand, pitch, length in items:
        if u % 2:
            continue
        w = 1.0 + 0.5 * min(length / upb, 4.0)
        if hand == LEFT:
            w += 1.5 + (2.0 if pitch <= low else 0.0)
        score[u % bar_u] += w
    first = min(u for u, _, _, _ in items) % bar_u
    first -= first % 2
    best = max(range(0, bar_u, 2), key=lambda r: score[r])
    return best if score[best] > 1.25 * score[first] + 1e-9 else first


# --------------------------------------------------------------------------- #
# 4. One voice per hand
# --------------------------------------------------------------------------- #

def _tidy_len(u: int, e: int) -> int:
    """
    The end of a note that starts at unit u and was meant to end at e, made easy to write.

    A long note must not end between two cells (an odd unit): a notation program rounds its
    end up to the next cell and the little notes that follow (an ornament) then start while it
    still sounds - which it prints as a second voice. So it ends one unit earlier, and the
    length is brought down to a written note value; the difference is a short rest.
    """
    n = e - u
    if n >= 4 and e % 2:
        n -= 1
    if n > STD_LEN[-1]:
        return u + n
    return u + max(v for v in STD_LEN if v <= n)


def _voice(by_u: dict, voices: str, chord_end: str, rest_min: int, counters: dict,
           tidy: bool = True) -> List[tuple]:
    """
    [(start unit, end unit, [(note, end_raw)])] for one hand.

    "single": a chord has one length and ends where the hand's next chord starts, unless a
    gap of rest_min units or more is left (then it is a rest). "keep": every note keeps its own
    length (so overlaps stay), gaps shorter than rest_min are still filled.
    """
    us = sorted(by_u)
    out = []
    pick = {"max": max, "min": min, "median": lambda v: sorted(v)[len(v) // 2]}[chord_end]
    for i, u in enumerate(us):
        group = []
        seen = set()
        for n, e in by_u[u]:                                  # one note per key
            if n.pitch not in seen:
                seen.add(n.pitch)
                group.append((n, e))
        nxt = us[i + 1] if i + 1 < len(us) else None

        def coarse(e_float: float) -> int:
            """A written length: whole cells when it is long enough to be one, else the nearest unit."""
            if e_float - u >= 1.5:
                return max(u + 2, 2 * math.floor(e_float / 2.0 + 0.5))
            return max(u + 1, math.floor(e_float + 0.5))

        ends = [coarse(e) for _, e in group]
        if voices == "single":
            e = pick(ends)
            if nxt is not None:
                if e > nxt:
                    counters["cut"] += sum(1 for x in ends if x > nxt)
                    e = nxt
                elif nxt - e < rest_min:
                    e = nxt
                elif nxt - e > 0:
                    counters["rests"] += 1
            if tidy:
                t = _tidy_len(u, e)
                if t != e:
                    counters["tidied"] += 1
                    e = t
            ends = [e] * len(group)
        else:
            if nxt is not None:
                ends = [nxt if 0 < nxt - x < rest_min else x for x in ends]
        out.append((u, ends, [n for n, _ in group]))
    return out


# --------------------------------------------------------------------------- #
# The whole thing
# --------------------------------------------------------------------------- #

def normalize(notes: Sequence, controls: Optional[Sequence[tuple]] = None, beats_per_bar: int = 4,
              max_bpm: float = MAX_BPM, voices: str = "single", chord_end: str = "median",
              rest_min: Optional[int] = None, cells_per_beat: Optional[int] = None,
              downbeat: str = "auto", tidy: bool = True) -> Clean:
    """
    Clean `notes` (objects with .pitch .start .end .velocity .hand, hands already decided).

    beats_per_bar:   the time signature's numerator over quarter notes (4 for 4/4).
    max_bpm:         the quarter note is the multiple of the cell that keeps the tempo below this.
    cells_per_beat:  force it (2: eighth-note cells, 4: sixteenth-note cells ...).
    voices:          "single" (one voice a hand, legato) or "keep" (overlaps stay).
    chord_end:       how long a chord is when its notes were held for different times.
    rest_min:        gaps (in units, half a cell) shorter than this are filled; default 3/4 of a beat.
    downbeat:        "auto", or "first" to make the first note beat 1.
    tidy:            (voices="single") every note gets a written note value and no long note ends
                     between two cells, so a notation program prints one voice per staff.
    controls:        [(time, controller, value)], mapped onto the new clock.
    """
    notes = [n for n in notes]
    if not notes:
        raise ValueError("no notes")
    report: List[str] = []
    groups = _chords(notes)
    gtimes = [sum(n.start for n in g) / len(g) for g in groups]
    cell, origin, strength = find_cell(gtimes)
    units, drift, st = _snap(groups, cell, origin)
    fit = st["on_grid"] / max(1, len(groups))
    half = cell / 2.0

    cpb, bpm = pick_beat(cell, max_bpm) if not cells_per_beat else \
        (cells_per_beat, round(60.0 / (cell * cells_per_beat), 2))
    upb = 2 * cpb                                           # units per beat
    if PPQ % upb:
        raise ValueError("cells_per_beat %d does not divide the resolution" % cpb)
    unit_new = 60.0 / (bpm * upb)
    if rest_min is None:
        rest_min = max(2, round(REST_MIN_BEAT * upb))

    # --- hands, with each note's unit and the end it asked for
    wt = np.array([sum(n.start for n in g) / len(g) for g in groups])
    wd = np.array(drift)
    per_hand = {RIGHT: {}, LEFT: {}}
    items = []
    for g, u, d in zip(groups, units, drift):
        for n in g:
            hand = n.hand or (LEFT if n.pitch < MIDDLE_C else RIGHT)
            e_float = (n.end - origin - d) / half
            per_hand[hand].setdefault(u, []).append((n, e_float))
            items.append((u, hand, n.pitch, max(1.0, e_float - u)))

    bar_u = beats_per_bar * upb
    first_u = min(u for u, _, _, _ in items)
    if downbeat == "first":
        phase = first_u % bar_u
    else:
        phase = _downbeat(items, bar_u, upb)
    # bar lines fall on the units congruent to `phase` (mod bar_u): u0 is the last one at or
    # before the first note, and becomes time 0
    u0 = first_u - ((first_u - phase) % bar_u)
    lead = first_u - u0                                      # units of silence in the first bar

    counters = {"cut": 0, "rests": 0, "merged": 0, "tidied": 0}
    out_notes = []
    for hand in (RIGHT, LEFT):
        before = sum(len(v) for v in per_hand[hand].values())
        for u, ends, ns in _voice(per_hand[hand], voices, chord_end, rest_min, counters, tidy):
            for n, e in zip(ns, ends):
                kw = dict(start=(u - u0) * unit_new, end=(e - u0) * unit_new, hand=hand,
                          track=0 if hand == RIGHT else 1)
                if hasattr(n, "tuplet_num"):
                    kw.update(tuplet_num=None, tuplet_type=None, printed=None)
                out_notes.append(replace(n, **kw))
        counters["merged"] += before - sum(1 for o in out_notes if o.hand == hand)
    out_notes.sort(key=lambda n: (n.start, n.pitch))

    # --- the clock: bars and beats from the first bar line to past the last note
    end_u = max(round(n.end / unit_new) for n in out_notes)
    nbars = max(1, math.ceil(end_u / bar_u))
    bar_times = [i * bar_u * unit_new for i in range(nbars + 1)]
    beat_times = [i * upb * unit_new for i in range(nbars * beats_per_bar + 1)]
    pickup_end = bar_times[1] if lead >= upb else 0.0

    clean = Clean(out_notes, bpm, beats_per_bar, unit_new, upb, bar_times, beat_times, pickup_end,
                  report, {}, [], origin, half, u0, wt, wd)
    if controls:
        sw = [(clean.map_time(t), c, v) for t, c, v in controls]
        clean.controls = sorted(sw, key=lambda x: (x[0], x[1]))

    # --- what was done
    rh = sum(1 for n in out_notes if n.hand == RIGHT)
    clean.stats = dict(cell=cell, strength=strength, fit=fit, bpm=bpm, notes_in=len(notes),
                       notes_out=len(out_notes), right=rh, left=len(out_notes) - rh,
                       fine=st["fine"], outliers=st["outliers"], cut=counters["cut"],
                       rests=counters["rests"], merged=counters["merged"], tidied=counters["tidied"],
                       pickup_units=lead,
                       phase=phase)
    report.append("grid: cell %.4f s, %d cells to the beat -> %.2f bpm, %d/4; %.0f%% of the chords sit "
                  "within a quarter cell of it" % (cell, cpb, bpm, beats_per_bar, 100 * fit))
    if st["fine"]:
        report.append("%d chord(s) put on the half-cell grid (ornaments)" % st["fine"])
    if st["outliers"]:
        report.append("%d chord(s) far from every cell, put on the nearest coarse one" % st["outliers"])
    if counters["cut"]:
        report.append("cut %d held note(s) where their hand's next chord begins" % counters["cut"])
    if counters["tidied"]:
        report.append("shortened %d note(s) to a written note value / a cell boundary (a short rest follows)"
                      % counters["tidied"])
    if counters["merged"]:
        report.append("merged %d note(s) that landed on the same key and unit" % counters["merged"])
    if lead:
        report.append("the first note is %d unit(s) into its bar%s" % (lead, " (a pickup)" if pickup_end else ""))
    if fit < 0.8:
        report.append("WARNING: only %.0f%% of the chords fit the grid - this is freer than a fixed pulse; "
                      "check the result" % (100 * fit))
    return clean


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


# --------------------------------------------------------------------------- #
# Output: a MIDI file with two staves
# --------------------------------------------------------------------------- #

def _vlq(n: int) -> bytes:
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append(0x80 | (n & 0x7F))
        n >>= 7
    return bytes(reversed(out))


def _track(events: List[tuple]) -> bytes:
    """events: (tick, order, bytes) -> an MTrk chunk. Order breaks ties: offs, controllers, ons."""
    body = bytearray()
    last = 0
    for tick, _, data in sorted(events, key=lambda e: (e[0], e[1])):
        body += _vlq(tick - last) + data
        last = tick
    body += _vlq(0) + b"\xff\x2f\x00"
    return b"MTrk" + struct.pack(">I", len(body)) + bytes(body)


def _meta(kind: int, data: bytes) -> bytes:
    return bytes([0xFF, kind]) + _vlq(len(data)) + data


def write_midi(path: str, clean: Clean, key_fifths: int = 0, program: int = 0,
               flat_velocity: Optional[int] = None, fingers: Optional[dict] = None) -> int:
    """
    Write `clean` as a format-1 Standard MIDI File: a conductor track (tempo, time signature,
    key), "Right hand" on channel 0 and "Left hand" on channel 1, one voice each. The sustain
    (and other) controllers, if any, go to both hands' channels. Returns the number of notes.

    `fingers` (optional) maps a note's id() to its fingering label ("R3", "L1", "Rg"); each is
    written as a text event right before that note, in the form midi_loader.read_markers reads
    (the fingering editor's markers), so an exported score keeps the fingering.
    """
    if PPQ % clean.units_per_beat:
        raise ValueError("units_per_beat %d does not divide PPQ %d" % (clean.units_per_beat, PPQ))
    tpu = PPQ // clean.units_per_beat
    tick = lambda t: max(0, int(round(t / clean.unit)) * tpu)
    tempo = int(round(60_000_000 / clean.bpm))
    conductor = [(0, 0, _meta(0x03, b"Conductor")),
                 (0, 1, _meta(0x51, tempo.to_bytes(3, "big"))),
                 (0, 2, _meta(0x58, bytes([clean.beats_per_bar, 2, 24, 8]))),
                 (0, 3, _meta(0x59, bytes([key_fifths & 0xFF, 0])))]
    tracks = [_track(conductor)]
    total = 0
    for hand, ch, name in ((RIGHT, 0, b"Right hand"), (LEFT, 1, b"Left hand")):
        ev = [(0, 0, _meta(0x03, name)), (0, 1, bytes([0xC0 | ch, program]))]
        k = 0                       # each note's marker and its note-on share a tie-break pair,
        for n in clean.notes:       # so a chord's markers stay with their own notes
            if n.hand != hand:
                continue
            vel = flat_velocity or max(1, min(127, int(n.velocity)))
            a, b = tick(n.start), max(tick(n.start) + 1, tick(n.end))
            ev.append((b, 0, bytes([0x80 | ch, n.pitch, 0])))
            label = (fingers or {}).get(id(n))
            if label:
                ev.append((a, 2 * k + 1, _meta(0x01, str(label).encode("ascii"))))
            ev.append((a, 2 * k + 2, bytes([0x90 | ch, n.pitch, vel])))
            k += 1
            total += 1
        for t, c, v in clean.controls:
            ev.append((tick(t), 1, bytes([0xB0 | ch, c, v])))
        tracks.append(_track(ev))
    data = b"MThd" + struct.pack(">IHHH", 6, 1, len(tracks), PPQ) + b"".join(tracks)
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)
    return total


# --------------------------------------------------------------------------- #
# For the app
# --------------------------------------------------------------------------- #

def clean_song(song, **options):
    """
    A MidiSong made of `song`'s notes, cleaned: on the new clock, two tracks (the right hand,
    the left), the bars and beats of that clock, the pedal moved with the notes. The hands are
    those the song's notes carry. `options` go to `normalize`.
    """
    from midi_loader import MidiSong, TrackInfo
    options.setdefault("controls", getattr(song, "raw_controls", None))
    clean = normalize(song.notes, **options)
    rh = sum(1 for n in clean.notes if n.hand == RIGHT)
    tracks = [TrackInfo(0, "Right hand", 0, rh, RIGHT), TrackInfo(1, "Left hand", 0, len(clean.notes) - rh, LEFT)]
    duration = max((n.end for n in clean.notes), default=0.0)
    out = MidiSong(clean.notes, tracks, duration, clean.bar_times, clean.beat_times, song.path, clean.controls,
                   key_fifths=getattr(song, "key_fifths", 0))
    out.quarter_times = [(0.0, 60.0 / clean.bpm)]
    out.pickup_end = clean.pickup_end
    out.cleanup = list(getattr(song, "cleanup", [])) + clean.report
    # So the app knows this song sits on the cleaned clock, and an editor's
    # export can write the two-staff MIDI of exactly what it cleaned.
    out.normalized = True
    out.clean = clean
    return out


def main(argv=None) -> int:
    import argparse
    import os
    ap = argparse.ArgumentParser(description="Clean a MIDI file into a two-staff score (one voice per hand).")
    ap.add_argument("src")
    ap.add_argument("dst", nargs="?")
    ap.add_argument("--max-bpm", type=float, default=MAX_BPM, help="keep the quarter note under this (default %(default)s)")
    ap.add_argument("--beats", type=int, default=4, help="beats per bar (default 4)")
    ap.add_argument("--voices", choices=("single", "keep"), default="single")
    ap.add_argument("--no-tidy", action="store_true", help="keep every length as played (may print a second voice)")
    ap.add_argument("--chord-end", choices=("median", "max", "min"), default="median")
    ap.add_argument("--downbeat", choices=("auto", "first"), default="auto")
    a = ap.parse_args(argv)
    from midi_loader import load_song
    song = load_song(a.src, normalize=False)      # (normalized once, below - not twice)
    clean = normalize(song.notes, controls=getattr(song, "raw_controls", None), beats_per_bar=a.beats,
                      max_bpm=a.max_bpm, voices=a.voices, chord_end=a.chord_end, downbeat=a.downbeat,
                      tidy=not a.no_tidy)
    dst = a.dst or os.path.splitext(a.src)[0] + "_clean.mid"
    n = write_midi(dst, clean, key_fifths=getattr(song, "key_fifths", 0))
    for line in clean.report:
        print(line)
    print("wrote %s: %d notes (right %d, left %d)" % (dst, n, clean.stats["right"], clean.stats["left"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
