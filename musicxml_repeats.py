"""
musicxml_repeats.py - Parse MusicXML repeat barlines and voltas (endings), and expand playback order.
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple
import xml.etree.ElementTree as ET


def _ending_numbers(text: Optional[str]) -> List[int]:
    """Parse comma/hyphen separated ending numbers, e.g. '1, 2' or '1-2'."""
    nums = []
    for tok in re.split(r"[,\s]+", text or ""):
        m = re.match(r"^(\d+)(?:-(\d+))?\.?$", tok)
        if m:
            a = int(m.group(1))
            b = int(m.group(2) or a)
            nums.extend(range(a, max(a, b) + 1))
    return [n for n in nums if n > 0] or [1]


def parse_repeat_info(measure_elems: List[ET.Element]) -> List[dict]:
    """
    Parse repeat and ending flags for each measure element in a part.
    """
    out = []
    for m in measure_elems:
        info = dict(fwd=False, bwd=False, times=2, end_start=None, end_stop=False,
                    segno=None, coda=None, dacapo=False, dalsegno=None, tocoda=None, fine=False)
        for bar in m.findall("barline"):
            rep = bar.find("repeat")
            if rep is not None:
                if rep.get("direction") == "forward":
                    info["fwd"] = True
                elif rep.get("direction") == "backward":
                    info["bwd"] = True
                    try:
                        times = int(rep.get("times"))
                    except (TypeError, ValueError):
                        times = 0
                    if times >= 1:
                        info["times"] = times
            end = bar.find("ending")
            if end is not None:
                if end.get("type") == "start":
                    info["end_start"] = _ending_numbers(end.get("number"))
                elif end.get("type") in ("stop", "discontinue"):
                    info["end_stop"] = True
        for so in m.iter("sound"):
            if so.get("segno") is not None:
                info["segno"] = so.get("segno")
            if so.get("coda") is not None:
                info["coda"] = so.get("coda")
            if so.get("dacapo") is not None:
                info["dacapo"] = so.get("dacapo") != "no"
            if so.get("dalsegno") is not None:
                info["dalsegno"] = so.get("dalsegno")
            if so.get("tocoda") is not None:
                info["tocoda"] = so.get("tocoda")
            if so.get("fine") is not None:
                info["fine"] = True
        out.append(info)
    return out


def expand_repeats(info: List[dict]) -> Tuple[List[int], List[Optional[int]]]:
    """
    Simulate playback order following repeats, voltas (endings), and jumps.
    Returns:
      (order, ending_pass)
      order: 0-based indices of measures in playback order.
    """
    n = len(info)
    segno_at, coda_at, max_end = {}, {}, 1
    for i, mm in enumerate(info):
        if mm["segno"] is not None:
            segno_at[mm["segno"]] = i
        if mm["coda"] is not None:
            coda_at[mm["coda"]] = i
        if mm["end_start"]:
            max_end = max(max_end, max(mm["end_start"]))
    order, ending_pass = [], []
    rs, pass_no, cur_end = 0, 1, None
    jumped = back = False
    i = guard = 0
    while i < n and guard < 20000 and len(order) < 20000:
        guard += 1
        mm = info[i]
        if mm["fwd"] and not back:
            rs, pass_no = i, 1
        back = False
        eff = max_end if jumped else pass_no
        if mm["end_start"] and eff not in mm["end_start"]:
            j = i
            while j < n and not info[j]["end_stop"]:
                j += 1
            i = j + 1
            continue
        if mm["end_start"]:
            cur_end = eff
        order.append(i)
        ending_pass.append(cur_end)
        if mm["end_stop"]:
            cur_end = None
        if jumped and mm["fine"]:
            break
        if jumped and mm["tocoda"] is not None and mm["tocoda"] in coda_at:
            i = coda_at[mm["tocoda"]]
            continue
        if mm["bwd"] and not jumped:
            if pass_no < mm["times"]:
                pass_no += 1
                i = rs
                back = True
                continue
            pass_no, rs = 1, i + 1
        if not jumped and mm["dacapo"]:
            jumped, i = True, 0
            continue
        if not jumped and mm["dalsegno"] is not None and mm["dalsegno"] in segno_at:
            jumped, i = True, segno_at[mm["dalsegno"]]
            continue
        i += 1
    return order, ending_pass


def get_musicxml_repeat_order(xml_content: str) -> Optional[List[int]]:
    """
    Extract repeat playback order from MusicXML string.
    Returns 0-based measure index order, or None if no repeats exist or parse fails.
    """
    try:
        root = ET.fromstring(xml_content)
        part = root.find("part")
        if part is None:
            return None
        measures = part.findall("measure")
        if not measures:
            return None
        info = parse_repeat_info(measures)
        order, _ = expand_repeats(info)
        return order
    except Exception:
        return None

