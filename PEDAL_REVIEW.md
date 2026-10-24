# Review: Auto pedal (Hand-thesia) - lỗi cần fix và hướng cải thiện

Phạm vi đã đọc: `auto_pedal.py`, `MidiSong.set_pedal_mode` (`midi_loader.py`), `musicxml_loader.py`, `editor.py` (`load_song`, `_rebuild`), `staff.py` (`draw_pedal_brackets`), `app_settings.py`, `sf_synth.py` (phần pedal), `tests/test_auto_pedal.py`, `docs/ARCHITECTURE.md`, `CLAUDE.md`.

**Chưa đọc được** (cần để kiểm chứng vài điểm, xem mục 8): `main.py`, `common.py` (`Transport`, `Performance`, `MidiOut`), `hands.py` (`HandAnimator.performance`), `docs/AUTO_PEDAL_SPEC.md`.

---

## 0. Tóm tắt ưu tiên

| # | Mức | Vấn đề | Chỗ |
|---|---|---|---|
| B1 | **Cao** | Chế độ auto làm mất pedal sostenuto (66) và soft (67) của file | `MidiSong.set_pedal_mode` |
| B2 | **Cao** | `build()` là O(số measure × số note), và bị gọi lại ở **mỗi lần** `_rebuild` của editor (mỗi lần sửa ngón) | `auto_pedal.py`, `editor._rebuild` |
| B3 | **Cao** | Nhánh "LH vào giữa ô nhịp" dùng `continue` làm mất release của ô nhịp cuối và bỏ qua check bar line; pedal nhấn từ đầu ô dù LH chưa vào | `build()` |
| B4 | **Cao** | MusicXML: "From the score" **không bao giờ có pedal** vì loader không đọc `<pedal>` / `damper-pedal` | `musicxml_loader.py` |
| B5 | Trung bình | Bass của "harmony signature" là nốt thấp nhất trong **cả ô nhịp**, không phải nốt tấn công | `harmony_signature` |
| B6 | Trung bình | `on_beat()` luôn đúng với nhịp x/8 (lưới beat 0.5q, dung sai 0.25q) | `build()` |
| B7 | Trung bình | File MIDI dạng performance (MAESTRO...): lưới bar của pretty_midi không khớp nhạc thật, auto pedal theo bar line vô nghĩa | thiết kế |
| B8 | Trung bình | Pedal tính theo giờ MIDI thô, trong khi âm thanh theo `HandAnimator.performance` (note bị đánh trễ / nhả sớm) | tích hợp |
| B9 | Thấp | Fallback về "score" khi không có bar nhưng nút Settings vẫn hiện "Auto by harmony" | `set_pedal_mode`, Settings |
| B10 | Thấp | Bracket "Ped." trên khuông: thiếu tick/nhãn khi nhấn lại sau quãng nhả dài, tick giả khi pedal kéo qua system | `staff.draw_pedal_brackets` |
| B11 | Thấp | Panel Settings 9 hàng có thể tràn quá tâm cửa sổ ở màn hình thấp | `app_settings.py` |
| B12 | Thấp | Tests yếu / thiếu, docs lệch code | tests, docs |

---

## 1. Kiến thức piano pedal cần nắm (liên hệ thẳng với code)

**Ba pedal.** Phải = *sustain* (damper nhấc khỏi dây, mọi dây đang rung và cả cộng hưởng đồng cảm cùng vang). Giữa = *sostenuto* (chỉ giữ các phím **đang bấm lúc nhấn pedal**). Trái = *una corda / soft* (nhẹ và đục hơn). App chỉ xử lý 64/66/67 dạng công tắc (ngưỡng 64), nên không có half-pedal.

**Legato / syncopated pedaling** (kỹ thuật chuẩn): nhấn pedal **sau** khi phím mới đã bấm, nhả và nhấn lại ngay khi hòa âm đổi. Nhờ đó âm mới không bị dập, âm cũ bị dập sạch. Code hiện tại đã mô phỏng đúng ý này: nhả sau attack của bass `release_delay` (40-80 ms), nhấn lại sau `gap_s` = 30 ms.

**Khi nào đổi pedal:**
1. Hòa âm đổi (đổi bass hoặc đổi màu hợp âm). Đây là quy tắc chính của `auto_pedal`.
2. Nốt giai điệu *ngoài hợp âm* (nốt lướt, appoggiatura) kéo dài thì pedal bẩn; pianist thường đổi pedal hoặc bỏ pedal. App **chưa** xét điều này (chỉ đọc LH).
3. Sau khoảng lặng, hoặc đổi texture (hết Alberti, vào hợp âm khối).
4. Nhịp có harmonic rhythm nhanh (hymn, chorale: mỗi phách một hợp âm) thì đổi **mỗi phách**, không chỉ mỗi 2 phách.

**Khi nào KHÔNG dùng pedal** (hoặc rất ít): Bach / Baroque, Mozart-Haydn (articulation rời), staccato, passage chromatic nhanh ở âm vực trầm (rất đục). Cần một "kiểu pedal" (light / romantic) thay vì một quy tắc cho mọi bài.

**Âm vực:** nốt trầm ngân dài, đổi theo hòa âm là được. Âm vực cao damp nhanh, chịu được pedal giữ lâu hơn. Pedal point (bass giữ nguyên, hòa âm trên đổi) thường giữ pedal hoặc đổi theo RH.

**Cuối bài:** pedal giữ cho hợp âm cuối ngân tự nhiên (thường 1-3 s), không cắt sau 0.3 s.

**Kỹ thuật dập thật:** damper dập dây trong khoảng 50-100 ms. Soundfont piano thường có release dài (0.3-1.5 s) nên khi pedal nhả, hợp âm cũ vẫn "trôi" qua hợp âm mới dù pedal đã đổi. Không sửa được ở lớp pedal, phải xử lý ở `sf2.py` (xem D6).

---

## 2. Lỗi cần fix

### B1. Auto mode làm mất sostenuto / soft của file (Cao)

`MidiSong.set_pedal_mode("auto")` thay toàn bộ `self.controls` bằng danh sách CC64 do `build()` tạo. Mọi sự kiện 66/67 trong `raw_controls` biến mất. `ARCHITECTURE.md` còn ghi nhận đây là chủ ý ("66/67 are not played"), nhưng không có lý do để bỏ chúng: auto pedal chỉ nên thay **sustain**.

**Sửa** (`midi_loader.py`):

```python
def set_pedal_mode(self, mode: str) -> None:
    if mode == "auto":
        from auto_pedal import build
        sustain, spans = build(self)
        if not sustain:                      # không có bar table: giữ nguyên pedal của file
            controls = pedal_switches(self.raw_controls)
            spans = []
            self.pedal_mode = "score"
        else:
            others = [c for c in pedal_switches(self.raw_controls) if c[1] != SUSTAIN]
            controls = sorted(sustain + others, key=lambda c: (c[0], c[2] != 0))   # UP trước DOWN
            self.pedal_mode = "auto"
    else:
        ...
```

**Test thêm:** song có `raw_controls=[(1.0, 66, 127), (2.0, 66, 0), (1.0, 64, 127)]`, gọi `set_pedal_mode("auto")`, assert các sự kiện 66 còn nguyên và mọi CC64 đều do auto sinh ra.

---

### B2. Độ phức tạp O(measure × note) và tính lại ở mỗi `_rebuild` (Cao)

Mỗi measure, `build()` gọi: `attack_cluster` (quét toàn bộ `lh`), `mid_anchors` (quét toàn bộ), `harmony_signature` (quét toàn bộ, và còn **lọc lại** `lh_notes(notes)` trên toàn danh sách mỗi lần gọi), `lh_sounding` (quét toàn bộ). Bài 20 phút với ~10k nốt LH và ~600 measure là hàng chục triệu phép so sánh trên main thread.

Tệ hơn: `FingeringEditor._rebuild()` gọi `self.song.set_pedal_mode(...)` mỗi lần sửa ngón, dù ngón tay **không ảnh hưởng** pedal (chỉ `n.hand` ảnh hưởng, qua `is_lh_note`).

**Sửa 1 - index theo thời gian** (`auto_pedal.py`):

```python
class _Index:
    """Notes sorted by start, with bisect windows."""
    def __init__(self, notes):
        self.notes = sorted(notes, key=lambda n: n.start)
        self.starts = [n.start for n in self.notes]
        self.max_dur = max((n.duration for n in self.notes), default=0.0)

    def starting(self, lo, hi):            # lo <= start < hi
        a = bisect.bisect_left(self.starts, lo)
        b = bisect.bisect_left(self.starts, hi)
        return self.notes[a:b]

    def sounding(self, t0, t1):            # overlaps [t0, t1)
        a = bisect.bisect_left(self.starts, t0 - self.max_dur)
        b = bisect.bisect_left(self.starts, t1)
        return [n for n in self.notes[a:b] if n.end > t0]
```

Trong `build()` tạo `idx = _Index(lh)` một lần; `attack_cluster`, `mid_anchors`, `lh_sounding`, `harmony_signature` nhận `idx` (giữ chữ ký nhận `list` để tests cũ chạy: nếu không có `.starting` thì tự bọc `_Index`). Bỏ `lh_notes(notes)` bên trong `harmony_signature` khi đã nhận danh sách LH (thêm tham số `filtered=True`).

**Sửa 2 - memo** (`midi_loader.py`):

```python
def _pedal_key(self):
    return (len(self.notes), len(self.bar_times),
            hash(tuple((n.start, n.end, n.pitch, n.hand) for n in self.notes)))

# trong set_pedal_mode, nhánh auto:
key = self._pedal_key()
memo = getattr(self, "_pedal_memo", None)
if memo and memo[0] == key:
    sustain, spans = memo[1]
else:
    sustain, spans = build(self)
    self._pedal_memo = (key, (sustain, spans))
```

Trong `editor._rebuild`, trước `self.song.set_pedal_mode(...)`: `self.song._pedal_memo = getattr(old, "_pedal_memo", None)`. Đổi tay (`set_hand`) đổi `n.hand` nên key đổi và pedal được tính lại đúng, còn sửa ngón thì dùng memo.

**Test:** `build()` trên 20.000 nốt LH / 1.000 measure phải < 0.3 s (đặt ngưỡng rộng cho CI). Test `_rebuild` không gọi lại `build` khi chỉ đổi ngón (mock/đếm).

---

### B3. Nhánh "LH vào giữa ô nhịp" (Cao)

Đoạn đầu vòng lặp:

```python
if not cl:
    anchors = mid_anchors(lh, m, q)
    if anchors:
        a0 = anchors[0]
        st.update(t_down=m.start, sig=sig_at(a0, m, q), t0=a0.start, held=1)
        continue
    continue
```

Hai vấn đề:

1. `t_down=m.start`: pedal nhấn từ đầu ô nhịp, trong khi LH chỉ vào ở `a0.start`. Phần RH trước đó bị pedal giữ, giai điệu bị nhòe dù chưa có hòa âm LH nào. Pianist nhấn pedal sau bass.
2. `continue` bỏ qua phần còn lại của vòng lặp, gồm khối `nxt is None` (nhả pedal ở measure cuối). Nếu measure cuối là measure LH vào muộn, pedal không bao giờ được đóng nên **không có sự kiện nào** (sự kiện chỉ được `append` khi đóng span). Ngoài ra check bar line giữa measure này và measure sau bị bỏ qua.

**Sửa:**

```python
for i, m in enumerate(ms):
    q = qlen(m.start)
    if st["t_down"] is None:
        cl = attack_cluster(lh, m, q)
        if cl:
            a0 = _lowest(cl)
            st.update(t_down=m.start, sig=sig_at(a0, m, q), t0=a0.start, held=1)
        else:
            anchors = mid_anchors(lh, m, q)
            if not anchors:
                continue                        # measure dẫn nhập / chưa có LH
            a0 = anchors[0]
            st.update(t_down=a0.start, sig=sig_at(a0, m, q), t0=a0.start, held=1)
    # ... không `continue`: chạy tiếp mid_bar, cuối bài, bar line
```

(Khối mid_bar không kích hoạt lại ở `a0` vì `a.start - t_down = 0 < mid_min_gap_beats`.)

**Test:** (a) LH vào ở phách 2 của measure đầu: span đầu bắt đầu ở `a0.start`, không phải `m.start`. (b) Measure cuối là measure LH vào muộn: vẫn có span và sự kiện nhả.

---

### B4. MusicXML: "From the score" không có pedal nào (Cao)

`load_musicxml` truyền `controls=[]` vào `MidiSong`. Score MusicXML có pedal in sẵn (`<direction-type><pedal type="start|stop|change|continue"/>` hoặc `<sound damper-pedal="yes|no|0-100"/>`) bị bỏ hoàn toàn. Nghĩa là nút "From the score" với MusicXML = không pedal, và "Auto" ghi đè một thứ chưa từng tồn tại.

**Sửa** (sketch, `musicxml_loader.py`):

```python
def _pedal_events(direction):
    """['down' | 'up' | 'change'] trong một <direction>."""
    out = []
    for dt in _kids(direction, "direction-type"):
        for p in _kids(dt, "pedal"):
            typ = p.attrib.get("type", "")
            if typ in ("start", "resume"):
                out.append("down")
            elif typ in ("stop", "discontinue"):
                out.append("up")
            elif typ == "change":
                out.append("change")
    if not out:
        sound = _kid(direction, "sound")
        if sound is not None and "damper-pedal" in sound.attrib:
            v = sound.attrib["damper-pedal"].strip().lower()
            try:
                down = float(v) >= 50
            except ValueError:
                down = v == "yes"
            out.append("down" if down else "up")
    return out
```

Trong `_read_measure` (thêm tham số `controls: list`), trước `if tag != "note": continue`:

```python
if tag == "direction":
    off = _num(c, "offset") or 0
    t = at + tempo.sec(start_div + max(0, cursor + off)) - origin
    for ev in _pedal_events(c):
        if ev == "down":
            controls.append((t, 64, 127))
        elif ev == "up":
            controls.append((t, 64, 0))
        else:                                   # change: nhả rồi nhấn lại
            controls.append((t, 64, 0))
            controls.append((t + 0.03, 64, 127))
    continue
```

`_read_all_parts` trả thêm `controls` (gom từ mọi part, đã đi qua thứ tự lặp nên repeat tự có pedal lần 2); `load_musicxml` truyền vào `MidiSong(..., controls, ...)`. `MidiSong.__init__` đã dedupe bằng `pedal_switches`, nên hai part cùng ghi một pedal cũng không sao.

Thêm helper trong `auto_pedal.py` rồi dùng cho cả chế độ score (xem B10):

```python
def spans_from_controls(controls):
    out, down = [], None
    for t, c, v in controls:
        if c != SUSTAIN:
            continue
        if v >= 64 and down is None:
            down = t
        elif v < 64 and down is not None:
            out.append((down, t))
            down = None
    return out
```

**Test:** tạo XML nhỏ có `<pedal type="start"/>` ở phách 1 và `<pedal type="stop"/>` ở phách 4 → `song.controls == [(t0,64,127),(t1,64,0)]`. Thêm test cho `change`, `damper-pedal`, và một đoạn repeat.

---

### B5. Bass lấy từ cả cửa sổ chứ không phải từ nốt tấn công (Trung bình)

`harmony_signature(notes, t0, win, tol)` chọn bass = nốt **thấp nhất được đánh trong toàn cửa sổ** `[t0 - tol, t0 + win)`. Trong `sig_at`, `win` có thể là cả phần còn lại của ô nhịp. Ví dụ ô nhịp C3 (phách 1) rồi G2 (phách 3) → bass pc = G, trong khi hợp âm của ô là C. Ngoài ra `tol` = 0.1 s cố định: ở tempo nhanh (nốt 16 cách 0.1 s) nốt bass của **phách trước** lọt vào cửa sổ và có thể thành "bass".

**Sửa:** `build()` đã biết nốt anchor `a` (thấp nhất của cluster tấn công). Cho `harmony_signature` nhận `bass: Optional[Note]`:

```python
def harmony_signature(notes, t0, win=1.0, tol=0.1, bass=None):
    ...
    low_note = bass if bass is not None else (low_new if low_new is not None else low_old)
    ...
    pcs.add(low_note.pitch % 12)
    return (low_note.pitch % 12, frozenset(pcs))
```

và `sig_at` / mid-bar `new` truyền `bass=a`. Đồng thời `tol = min(0.1, 0.15 * q)` thay vì 0.1 cố định. Test cũ (không truyền `bass`) vẫn chạy.

**Test:** ô nhịp C3 ở phách 1 + G2 ở phách 3 → `sig[0] == 0`.

---

### B6. `on_beat()` vô nghĩa với nhịp x/8 (Trung bình)

`MusicXML`: `beat_times` sinh theo `beat_type` (6/8 → 6 beat mỗi 0.5q). `on_beat` dùng dung sai `0.25 * q` quanh beat; lưới cách nhau 0.5q thì mọi thời điểm đều cách beat gần nhất ≤ 0.25q. Kết quả: luôn `True`, không lọc được nốt trượt nhịp. Tương tự `mid_min_beats=2.0` tính theo **quarter**, nên trong 6/8 nốt bass dài một phách dotted-quarter (1.5q) không bao giờ đủ điều kiện đổi giữa ô.

**Sửa:**

```python
def on_beat(t, q):
    if not beats:
        return True
    i = bisect.bisect_left(beats, t)
    near = [beats[j] for j in (i - 1, i) if 0 <= j < len(beats)]
    gap = (beats[i] - beats[i - 1]) if 0 < i < len(beats) else q
    tol = 0.25 * min(q, gap)
    return any(abs(b - t) <= tol + 1e-6 for b in near)
```

Và đổi ngưỡng "nốt dài" sang đơn vị **phách** (độ dài beat cục bộ) thay vì quarter. Cần `quarter_length`/beat gap, và nên đọc thêm mẫu số nhịp.

**Cần kiểm tra thêm (mình không chắc 100%):** theo trí nhớ, `pretty_midi.get_downbeats()` tính downbeat mỗi `numerator` beat mà bỏ qua mẫu số, tức file MIDI 6/8 có thể cho bar_times sai (bar dài gấp đôi). Hãy thử với một file MIDI 6/8; nếu đúng thì cần tự tính downbeat từ `time_signature_changes` (ảnh hưởng cả khuông nhạc lẫn auto pedal).

---

### B7. File performance MIDI: lưới bar không đáng tin (Trung bình, thiết kế)

Với file thu (MAESTRO, Disklavier, MIDI chơi tay) tempo map thường là hằng số, `bar_times` do pretty_midi sinh ra là lưới đều, không bám nhịp thật. Khi đó "LH tấn công ở phách 1" ít khi đúng, và pedal đổi theo ranh giới sai: phần lớn measure rơi vào "không có attack → giữ" rồi bị `max_hold_bars` chặt.

**Hướng sửa:** thêm phát hiện độ khớp lưới và một nhánh không phụ thuộc bar (xem D1).

```python
def grid_fit(song):
    """Tỷ lệ onset LH nằm trong ±0.12q của một beat. < 0.5: lưới không đáng tin."""
```

---

### B8. Pedal theo giờ MIDI thô, âm thanh theo `performance` (Trung bình)

`CLAUDE.md`: âm và phím sáng theo `HandAnimator.performance` (note bị đánh trễ tới 0.25 s, nhả sớm để giữ top speed), không phải giờ MIDI thô. `build()` chỉ biết `song.notes` (giờ thô). Hệ quả:

- Bass bị đánh trễ hơn `release_delay + gap` (≈ 90 ms): pedal đã nhả-nhấn xong **trước** khi bass vang; vẫn ổn nhưng hợp âm cũ bị dập sớm hơn dự định.
- Phím bass bị nhả sớm (trước lúc nhấn lại) → note bị cắt dù đáng lẽ pedal giữ.

**Hướng sửa (cần xem `hands.py`/`common.py`):** tính pedal sau khi có `performance` (bản dựng hands), hoặc sau `build` làm bước "hiệu chỉnh": với mỗi lần đổi pedal, dịch `t_rel` về sau `max(a.start, strike_thật(a)) + d1`. Chưa nên làm nếu chưa đo; trước tiên cần xác nhận pedal được tính ở đâu trong luồng load.

---

### B9. Fallback im lặng (Thấp)

Nếu `build()` trả rỗng (PIG, hoặc không có bar), `set_pedal_mode` đặt `pedal_mode="score"` nhưng Settings vẫn ghi "Auto by harmony" và người dùng không biết vì sao không nghe thay đổi.

**Sửa:** `self.cleanup.append("auto pedal: no bar lines, kept the file's pedal")` (đã in ra console) và hiển thị trong player/editor `say(...)` một dòng.

---

### B10. Bracket "Ped." trên khuông (Thấp)

`draw_pedal_brackets`:

- Tick bắt đầu chỉ vẽ nếu `ca` là span đầu của cả bài hoặc bị cắt ở đầu system. Pedal nhấn lại sau một **quãng nhả dài** (`let_go`) không có tick và không có nhãn "Ped.".
- Nhãn "Ped." chỉ in một lần mỗi system, ở span đầu tiên nhìn thấy.
- Span kéo từ system trước sang system này vẽ tick bắt đầu giả ở `sys_start` (trông như nhấn pedal mới).
- Chỉ vẽ khi `pedal_mode == "auto"`: pedal có trong file (score mode) không được hiển thị.

**Sửa gọn:** coi một span là "nhấn mới" khi `idx == 0` hoặc `sa - spans[idx-1][1] >= 0.1`; vẽ tick + "Ped." chỉ khi `sa >= sys_start` (không bị cắt) và là nhấn mới. Span bị cắt đầu: chỉ nối từ mép trái. Bỏ điều kiện `pedal_mode == "auto"`, thay bằng "có `pedal_spans`", với `set_pedal_mode("score")` gán `spans = spans_from_controls(controls)` (giới hạn: nếu hơn ~12 span/system thì bỏ vẽ để file performance không rối khuông).

---

### B11. Panel Settings (Thấp, cần xác nhận)

`ROWS = 9`, `ROW_H_MIN = 40`, panel cao `9*40 + 20 = 380` bắt đầu từ `TOP_BAR_H + 16`. Công thức hàng "thu nhỏ để panel nằm trên tâm cửa sổ" chỉ co được tới 40 px. Với `TOP_BAR_H` khoảng 40 thì cần cửa sổ cao ≳ 870 px để panel không vượt tâm; thấp hơn thì panel đè lên bàn phím xem trước và vùng grab (kéo bàn phím), nên bấm vào khoảng trống của panel có thể bắt đầu kéo bàn phím.

**Sửa:** panel cuộn được bằng bánh xe, hoặc chia hai cột khi cửa sổ thấp; hoặc kiểm tra va chạm (`panel.bottom > h//2`) rồi vô hiệu hoá `grab` bên trong `panel`. Cũng cập nhật docstring đầu `app_settings.py` (chưa nhắc Pedal).

---

### B12. Tests và tài liệu

**Tests (`tests/test_auto_pedal.py`):**
- `test_draw_pedal_brackets_helper` bọc `draw_pedal_brackets` trong `try/except Exception: return`: nếu hàm hỏng, test **vẫn pass**. Bỏ try/except (cần `pygame.font.init()` mà thôi).
- `import main`, `import pianist` thừa trong `test_staff_view_draw_with_auto_pedal`.
- `test_settings_screen_pedal_mode_toggle` tạo `main.App` không dọn dẹp.
- Thiếu: sostenuto/soft còn nguyên (B1), hiệu năng (B2), memo (B2), LH vào muộn (B3), bass từ cluster (B5), `on_beat` trong 6/8 (B6), `pickup` (`measure_table` với `pickup_end`), song không có LH (fallback `lh_notes`), `set_pedal_mode` khi không có bar.

**Docs:**
- `ARCHITECTURE.md`: nhãn nút ghi "Auto by measure", code là "Auto by harmony".
- `CLAUDE.md`: bảng Modules chưa có `auto_pedal.py` (và `docs/AUTO_PEDAL_SPEC.md`); thêm dòng "Auto pedal" trong Conventions: pedal tính từ `song.notes` + `hand`, đổi tay phải tính lại; sửa ngón không cần.
- `ARCHITECTURE.md` mục `set_pedal_mode`: sau B1 bỏ câu "66/67 not played".
- Settings hiện gọi `self.app.mode.song.set_pedal_mode(...)` với `app.mode` chính là `SettingsScreen` (không có `.song`) nên nhánh này nhiều khả năng không bao giờ chạy; cần `main.py` để xác nhận cách player/editor đọc `pedal_mode` khi mở bài (xem mục 8).

---

## 3. Cải thiện thiết kế

**D1. Nhánh không phụ thuộc lưới bar (cho performance MIDI).** Khi `grid_fit(song) < 0.5`:

```python
def build_gridfree(song, min_hold=0.35, gap_s=0.03):
    clusters = cluster_lh(lh, tol=0.04)                    # nhóm onset LH
    cur_sig, t_down, last_change = None, None, -1e9
    for cl in clusters:
        a = _lowest(cl); t = a.start
        new = harmony_signature(lh, t, win=max(1.0, 2*q), tol=0.05, bass=a)
        if t_down is None:
            t_down, cur_sig, last_change = t + 0.01, new, t
            continue
        strong = (a.duration >= 1.5 * q) or len(cl) >= 3   # bass dài hoặc hợp âm khối
        if (t - last_change >= min_hold and strong
                and harmony_changed(cur_sig, new)):
            ... press_change(a, ...)                        # như hiện tại
            cur_sig, last_change = new, t
        elif t - last_change >= max_hold_s:                 # giữ quá lâu: làm tươi
            ...
    ... nhả ở cuối
```

Ý tưởng: dùng onset LH thật làm ranh giới, bỏ phụ thuộc "phách 1".

**D2. Kiểu pedal (`pedal_style`):** `light` (cổ điển/Baroque: chỉ pedal khi LH ngân, bỏ qua passage mà `độ dài nốt / IOI < 0.6`), `normal` (hiện tại), `heavy` (romantic: đổi ít hơn, `max_hold_bars` lớn hơn). Thực chất là gói tham số: `max_hold_bars`, `mid_min_beats`, `mid_min_gap_beats`, ngưỡng Jaccard.

**D3. Texture hợp âm khối (hymn/chorale):** cho phép đổi giữa ô khi cluster LH có ≥ 3 nốt đánh cùng lúc trên phách, dù nốt ngắn (< `mid_min_beats`), vì Alberti/walking bass là nốt đơn.

**D4. Bỏ qua nốt hoa mỹ làm anchor:** nốt LH rất ngắn sát trước bar line (acciaccatura trong MIDI) hiện có thể thành anchor, và cluster 40 ms loại mất nốt bass thật. Bỏ nốt có `duration < 0.1 * q` khỏi `attack_cluster` khi có nốt dài hơn trong cửa sổ (tương tự `staff.find_grace_runs`).

**D5. Giai điệu RH ngoài hợp âm:** nếu một nốt RH dài ≥ 1q có pitch class không thuộc `cur_sig.pcs` và không lướt (không phải bước liền), thì xem là điểm đổi pedal ứng viên. Tùy chọn, cho kiểu `normal`+.

**D6. Damper thực tế:** trong `sf_synth.SoundfontOut._release`, khi nhả vì pedal lift có thể yêu cầu `sf2.Voice.release(fast=True)` (release ~0.08-0.15 s thay vì release mẫu) để hợp âm cũ không trôi qua hợp âm mới. Chỉ làm khi nhả do pedal, không với note-off thường.

**D7. Công cụ đo (`tools/pedal_check.py`):** theo văn hóa repo (maestro_check, regress). Với file có pedal thật (MAESTRO `raw_controls`, MusicXML sau B4): lấy các cạnh *nhả* của CC64 thật làm ground truth, so với cạnh nhả của `build()` trong cửa sổ ±0.15 s, báo precision / recall / F1 và độ lệch trung vị, gom theo kiểu file. Mọi ngưỡng hiện tại (Jaccard 0.5, `mid_min_beats` 2, `max_hold_bars` 4, `gap_s`) hiện được chọn "bằng lý luận" (ghi trong ARCHITECTURE) nên cần một thước đo trước khi chỉnh.

**D8. Chế độ thứ ba:** `"auto_if_empty"` (Auto khi file không có pedal): giữ pedal thật của MAESTRO/MusicXML có sẵn, chỉ sinh pedal cho file không có. Thêm vào `PEDAL_MODES` và nhánh trong `set_pedal_mode`:

```python
has_own = any(c[1] == SUSTAIN for c in pedal_switches(self.raw_controls))
if mode == "auto_if_empty":
    mode = "score" if has_own else "auto"
```

**D9. Cuối bài:** `FINAL_TAIL_S` 0.3 s và `Transport.END_PAD_T` 0.5 s đều ngắn so với hợp âm cuối thật. Nếu muốn vang tự nhiên, nâng cả hai (vd. tail 1.5 s và end pad tương ứng); nếu không thì chỉ cần ghi chú đây là giới hạn của `END_PAD_T`.

---

## 4. Thứ tự làm đề nghị

1. **B1** (5 phút, tác động rõ).
2. **B3** (sửa vòng lặp `build`, kèm 2 test).
3. **B2** (index + memo; đo trước/sau).
4. **B5**, **B6** (độ chính xác hòa âm).
5. **B4** (đọc `<pedal>` MusicXML) cùng **B10** (hiển thị pedal của score).
6. **D7** (công cụ đo) rồi mới chỉnh ngưỡng; sau đó **B7/D1**, **D2**, **D3**.
7. **B8** sau khi xem `hands.py` / `common.py`.
8. **B9**, **B11**, **B12** (dọn dẹp, tests, docs).

---

## 5. Test bổ sung (gợi ý nhanh)

```python
def test_auto_keeps_sostenuto_and_soft():
    song = _song([Note(48, 0.0, 2.0, 80, 0, LEFT), Note(55, 2.0, 4.0, 80, 0, LEFT)],
                 [0.0, 2.0, 4.0], 4.0)
    song.raw_controls = [(1.0, 66, 127), (3.0, 66, 0), (1.0, 67, 127)]
    song.set_pedal_mode("auto")
    assert {c for c in song.controls if c[1] == 66} == {(1.0, 66, 127), (3.0, 66, 0)}

def test_lh_enters_mid_bar_pedal_starts_at_the_note():
    notes = [Note(48, 0.5, 2.0, 80, 0, LEFT)]            # LH vào ở phách 2
    _, spans = auto_pedal.build(_beat_song(notes, [0.0, 2.0], 2.0))
    assert spans[0][0] == pytest.approx(0.5)

def test_last_measure_lh_late_still_released():
    notes = [Note(48, 0.0, 2.0, 80, 0, LEFT), Note(55, 2.5, 4.0, 80, 0, LEFT)]
    ev, spans = auto_pedal.build(_beat_song(notes, [0.0, 2.0, 4.0], 4.0))
    assert ev[-1][2] == 0

def test_bass_is_the_attack_note_not_the_lowest_in_bar():
    notes = [Note(48, 0.0, 1.0, 80, 0, LEFT), Note(43, 1.0, 2.0, 80, 0, LEFT)]
    sig = auto_pedal.harmony_signature(notes, 0.0, win=2.0, bass=notes[0])
    assert sig[0] == 0

def test_build_scales(benchmark_or_timer):
    # 1000 measure x 20 nốt LH: < 0.3 s
    ...
```

---

## 6. Rủi ro khi sửa

- B5 đổi cách chọn bass → có thể làm đổi số lần pedal ở vài bài; chạy `pedal_check` (D7) hoặc ít nhất so `len(spans)` trên các file test trước/sau.
- B2 memo: key phải gồm `hand` (đổi tay đổi LH). Đừng dùng `id(note)` làm key (editor `replace()` tạo object mới).
- B3 bỏ `continue` nghĩa là measure vào muộn bây giờ cũng chạy khối bar line; kiểm tra lại các test "LH rests then enters".
- B4: `<direction>` đứng trước `<note>` đầu tiên của measure nên `cursor` là 0; direction đứng sau `<backup>` phải dùng đúng `cursor` của voice hiện tại (như code mẫu).

---

## 7. Điều mình **chưa** chắc

- pretty_midi và nhịp x/8 (B6) cần xác nhận bằng file thật.
- Layout Settings (B11) phụ thuộc giá trị `TOP_BAR_H`.
- Luồng "Settings → song đang mở" (B12) phụ thuộc `main.py`.

---

## 8. File nên gửi thêm để review sâu hơn

1. `main.py`: nơi gọi `set_pedal_mode` khi mở bài trong player (`Visualizer`/`App._load`), cách `App.mode` liên hệ với Settings.
2. `common.py`: `Transport` (gửi CC pedal, trạng thái pedal khi **seek**, `end_time`), `Performance`, `MidiOut`.
3. `hands.py`: phần `HandAnimator.performance` / `_fingered_performance` (để làm B8).
4. `docs/AUTO_PEDAL_SPEC.md`: để đối chiếu spec với code.
5. Một vài file test: một MIDI performance (đoạn MAESTRO ngắn), một MusicXML có `<pedal>`, một file 6/8: để chạy `pedal_check` và kiểm chứng B6/B7.
