#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
xml_regrid_piano.py

Đọc MusicXML partwise, bỏ ý nghĩa ô nhịp cũ, chuyển thành danh sách nốt theo đơn vị phách đen
(1 phách đen = 1 "giây" của engine), rồi gọi engine MIDI của normalize_piano_musicxml.py để
chuẩn hóa về nhịp mong muốn, 2 tay, 1 voice mỗi tay.

Vì nốt được đưa vào engine theo đơn vị phách đen, `--max-bpm 90` (mặc định) làm engine chọn
1 beat = 1 phách đen; nhờ vậy lưới tìm được đúng là phách đen / nốt của bản gốc. Output MusicXML
đúng như bản gốc về nhịp; `clean.bpm` được ghi đè bằng `--tempo-mark` (lấy từ file input nếu có)
chỉ để ghi tempo marking, không làm thay đổi độ dài nốt đã viết.

`--min-dur` ở script này tính bằng phách đen (không phải giây).
"""

import argparse
import normalize_piano_musicxml as N


def read_score_meta(path):
    """(fifths, bpm) của file MusicXML: <key><fifths> đầu tiên và tempo đầu tiên (hoặc None)."""
    root = N.load_musicxml_root(path)
    fifths = None
    for key in root.iter("key"):
        txt = key.findtext("fifths")
        if txt is not None and txt.strip():
            try:
                fifths = int(txt)
            except ValueError:
                fifths = None
            break
    bpm = None
    for sound in root.iter("sound"):
        if sound.get("tempo"):
            try:
                bpm = float(sound.get("tempo"))
                break
            except ValueError:
                pass
    if bpm is None:
        for per in root.iter("per-minute"):
            if per.text and per.text.strip():
                try:
                    bpm = float(per.text)
                    break
                except ValueError:
                    pass
    return fifths, bpm


def read_bar_meta(path):
    """
    (beats, anchor) của file MusicXML, để re-grid đúng ô nhịp gốc.

    beats : số phách của số chỉ nhịp x/4 đầu tiên (None nếu không phải x/4 hoặc không đọc được).
    anchor: thời điểm (phách đen) của vạch nhịp đầu tiên: 0.0, hoặc độ dài ô nhịp lấy đà nếu
            measure đầu là ô nhịp ngắn (implicit / số 0).
    """
    root = N.load_musicxml_root(path)
    parts = root.findall("part")
    first_attrs = root.find(".//attributes")
    if first_attrs is None or not parts:
        return None, None
    time_elem = first_attrs.find("time")
    if time_elem is None:
        return None, None
    beats = N.get_child_int(time_elem, "beats", 0)
    if beats < 1 or N.get_child_int(time_elem, "beat-type", 0) != 4:
        return None, None

    divisions = N.get_child_int(first_attrs, "divisions", 1) or 1
    states = [{"divisions": divisions, "beats": beats, "beat_type": 4} for _ in parts]
    firsts = [p.find("measure") for p in parts]
    implicit = any(m is not None and (m.get("implicit") == "yes" or m.get("number") == "0")
                   for m in firsts)
    ends = []
    for pi, m in enumerate(firsts):
        if m is None:
            continue
        N.parse_measure(pi, m, states[pi], None)
        ends.append(states[pi].get("measure_end", 0) / max(1, states[pi]["divisions"]))
    anchor = 0.0
    if implicit and ends and 0 < max(ends) < beats:
        anchor = float(max(ends))
    return beats, anchor


def extract_notes_from_musicxml(path, split_midi, hand="auto", split_chords=False):
    """
    MusicXML -> [Note] với thời gian tính bằng phách đen.
    - Phân tay giống run_musicxml: dùng N.assign_hand với tay theo part (part cao = tay phải).
      --split-chords chỉ có nghĩa với hand auto/pitch.
    - Nốt nối (tie start -> stop cùng part/voice/pitch, liền nhau) được gộp thành một nốt dài,
      không bị gõ lại.
    """
    root = N.load_musicxml_root(path)

    if root.tag != "score-partwise":
        raise ValueError("Chỉ hỗ trợ score-partwise.")

    parts = root.findall("part")
    part_measures = [p.findall("measure") for p in parts]
    max_measures = max((len(x) for x in part_measures), default=0)

    if max_measures == 0:
        return []

    first_attrs = root.find(".//attributes")
    initial_divisions = 1
    initial_beats = 4
    initial_beat_type = 4

    if first_attrs is not None:
        initial_divisions = N.get_child_int(first_attrs, "divisions", 1) or 1
        time_elem = first_attrs.find("time")
        if time_elem is not None:
            initial_beats = N.get_child_int(time_elem, "beats", 4) or 4
            initial_beat_type = N.get_child_int(time_elem, "beat-type", 4) or 4

    states = [
        {"divisions": initial_divisions, "beats": initial_beats, "beat_type": initial_beat_type}
        for _ in parts
    ]

    # Lượt 1: gom mọi event với thời gian tuyệt đối (phách đen).
    raw = []  # (chỉ số part, Event, start_q, end_q)
    t = 0.0

    for mi in range(max_measures):
        parsed = []
        implicit = False

        for pi, ms in enumerate(part_measures):
            if mi >= len(ms):
                continue

            m = ms[mi]
            if m.get("implicit") == "yes" or m.get("number") == "0":
                implicit = True

            parsed.append((pi, N.parse_measure(pi, m, states[pi], None)))

        if not parsed:
            continue

        # Độ dài ô nhịp, tính bằng phách đen (divisions của mỗi part có thể khác nhau).
        ends_q = [st.get("measure_end", 0) / max(1, st["divisions"]) for st in states]
        nominal_q = max(N.expected_duration(st) / max(1, st["divisions"]) for st in states)
        end_q = max(ends_q, default=0)
        measure_len = end_q if implicit and 0 < end_q < nominal_q else max(nominal_q, end_q)

        for pi, evs in parsed:
            div = max(1, states[pi]["divisions"])
            for ev in evs:
                if ev.items:
                    raw.append((pi, ev, t + ev.onset / div, t + (ev.onset + ev.duration) / div))

        t += measure_len

    # Tay theo part giống run_musicxml: part có pitch trung bình cao hơn là tay phải.
    pitch_sum, pitch_cnt = {}, {}
    for pi, ev, _s, _e in raw:
        for midi, _o in ev.items:
            if midi is not None:
                pitch_sum[pi] = pitch_sum.get(pi, 0) + midi
                pitch_cnt[pi] = pitch_cnt.get(pi, 0) + 1
    part_hand = N.build_part_hand_map({pi: pitch_sum[pi] / pitch_cnt[pi] for pi in pitch_sum})

    # Lượt 2: tạo Note; nốt nối được gộp thành một nốt dài.
    notes = []
    open_ties = {}  # (part, voice, pitch) -> Note đang chờ tie stop

    for pi, ev, start_q, end_q in raw:
        items = [(m, o) for m, o in ev.items if m is not None]
        if not items:
            continue

        per_note = split_chords and hand in ("auto", "pitch")
        ev_hand = None if per_note else N.assign_hand(ev, hand, split_midi, part_hand)

        for midi, orig in items:
            h = ev_hand or (N.LEFT if midi < split_midi else N.RIGHT)
            kinds = N._tie_types(orig)
            key = (pi, ev.voice, midi)
            prev = open_ties.get(key)

            if "stop" in kinds and prev is not None and abs(prev.end - start_q) < 1e-6:
                prev.end = end_q
                if "start" not in kinds:
                    del open_ties[key]
                continue

            note = N.Note(pitch=midi, start=start_q, end=end_q, velocity=90, track=0, hand=h,
                          lyric=N.note_lyrics(orig) or None)
            notes.append(note)
            if "start" in kinds:
                open_ties[key] = note
            else:
                open_ties.pop(key, None)

    notes.sort(key=lambda n: (n.start, n.pitch))
    return notes


def build_parser():
    p = argparse.ArgumentParser(
        description="Re-grid MusicXML về nhịp mong muốn, 2 tay piano, 1 voice mỗi tay."
    )

    p.add_argument("input", help="File MusicXML đầu vào (.xml/.musicxml/.mxl)")
    p.add_argument("output", help="File MusicXML đầu ra (.xml)")

    p.add_argument("--beats", type=int, default=None,
                   help="Số phách mỗi ô nhịp. Mặc định lấy từ số chỉ nhịp x/4 đầu tiên của file "
                        "input (không có thì 4).")
    p.add_argument("--split", default="C4",
                   help="Ngưỡng tách tay, mặc định C4.")
    p.add_argument("--hand", choices=("auto", "staff", "pitch"), default="auto",
                   help="Cách phân tay.")
    p.add_argument("--split-chords", action="store_true",
                   help="Tách từng nốt trong chord theo --split.")

    p.add_argument("--offset-beats", type=float, default=0.0,
                   help="Dịch toàn bộ nốt sang phải bao nhiêu phách đen.")

    # Vì wrapper dùng đơn vị phách đen, max-bpm ~90 để engine ưu tiên phách đen.
    p.add_argument("--max-bpm", type=float, default=90.0,
                   help="Giới hạn tempo suy luận trong wrapper này (mặc định 90: 1 beat = 1 phách đen).")

    p.add_argument("--downbeat", choices=("auto", "first"), default="auto")

    p.add_argument("--fine", action="store_true",
                   help="cho phép nốt/độ dài trên nửa ô lưới (mặc định: không ghi nốt ngắn hơn 16th)")
    p.add_argument("--min-dur", type=float, default=0.0, metavar="BEATS",
                   help="bỏ nốt ngắn hơn BEATS phách đen trước khi chuẩn hóa (mặc định 0 = giữ hết). "
                        "Lưu ý: đơn vị ở script này là phách đen, không phải giây")
    p.add_argument("--lh-max", type=int, default=None, metavar="N",
                   help="tay trái tối đa N nốt mỗi hợp âm (giữ bass trước). 0 = không giới hạn "
                        "(mặc định 0, hoặc 3 khi --simplify)")
    p.add_argument("--rh-max", type=int, default=None, metavar="N",
                   help="tay phải tối đa N nốt mỗi hợp âm (giữ nốt cao nhất trước). 0 = không giới hạn "
                        "(mặc định 0, hoặc 1 khi --simplify)")
    p.add_argument("--drop-ornaments", action="store_true",
                   help="bỏ nốt láy (nốt rất ngắn, cách nốt dài cùng beat / hợp âm dài kề bên 1-2 bán cung)")
    p.add_argument("--simplify", action="store_true",
                   help="đơn giản hóa: --lh-max 3 --rh-max 1 --drop-ornaments (ghi rõ --lh-max/--rh-max "
                        "thì giá trị đó thắng, kể cả 0)")

    p.add_argument("--key", type=int, default=None,
                   help="Số dấu hóa của khóa. Mặc định lấy từ file input, không có thì 2 (D major).")
    p.add_argument("--tempo-mark", type=float, default=None,
                   help="Tempo marking ghi vào XML. Mặc định lấy từ file input, không có thì 113.")

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)

    split_midi = N.note_name_to_midi(args.split)

    try:
        fifths_in, bpm_in = read_score_meta(args.input)
    except Exception as e:
        raise SystemExit("Không đọc được file input: %s" % e)

    key = args.key if args.key is not None else (fifths_in if fifths_in is not None else 2)
    tempo_mark = args.tempo_mark if args.tempo_mark is not None else (
        bpm_in if bpm_in is not None else 113.0)

    notes = extract_notes_from_musicxml(
        args.input,
        split_midi=split_midi,
        hand=args.hand,
        split_chords=args.split_chords,
    )

    if not notes:
        raise SystemExit("Không trích xuất được nốt nào từ file input.")

    if args.offset_beats:
        for n in notes:
            n.start += args.offset_beats
            n.end += args.offset_beats

    lh_max = args.lh_max if args.lh_max is not None else (3 if args.simplify else 0)
    rh_max = args.rh_max if args.rh_max is not None else (1 if args.simplify else 0)
    drop_orn = args.drop_ornaments or args.simplify

    # Số phách và vị trí vạch nhịp lấy từ file gốc (nếu x/4), để không đoán sai nhịp / downbeat.
    beats_in, anchor_in = read_bar_meta(args.input)
    beats = args.beats if args.beats is not None else (beats_in or 4)
    anchor = None
    if beats_in and beats == beats_in and args.downbeat == "auto":
        anchor = anchor_in + (args.offset_beats or 0.0)

    try:
        clean = N.normalize(notes, beats_per_bar=beats, bar_anchor=anchor, max_bpm=args.max_bpm,
                            downbeat=args.downbeat, voices="single", tidy=True,
                            fine=args.fine, min_dur=args.min_dur,
                            lh_max=lh_max, rh_max=rh_max, drop_ornaments=drop_orn)
    except (ValueError, RuntimeError) as e:
        raise SystemExit("Không chuẩn hóa được %s: %s" % (args.input, e))

    # Chỉ là tempo marking; không làm thay đổi độ dài nốt đã viết.
    clean.bpm = tempo_mark

    n_written = N.write_musicxml(args.output, clean, key_fifths=key)

    for line in clean.report:
        print(line)

    print(f"Đã xuất: {args.output}")
    print(f"Tổng số nốt: {n_written}")
    print(f"Tay phải: {clean.stats.get('right', 0)}, tay trái: {clean.stats.get('left', 0)}")


if __name__ == "__main__":
    main()
