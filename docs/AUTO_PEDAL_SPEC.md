# AUTO PEDAL THEO Ô NHỊP — SPEC THIẾT KẾ

Tài liệu này dành cho model/dev đọc source Python (import MusicXML/MIDI rồi play theo bản nhạc) và triển khai tính năng tự động thêm pedal. Spec độc lập với source: chỉ nói cần làm gì, dữ liệu nào, và cần tìm gì trong code.

---

## 0. Cách hiểu thuật toán

- Pedal của ô nhịp N **nhấn** tại `t_down`. Ô nhịp đầu tiên đủ nhịp có `t_down` = đầu ô nhịp. Các ô sau có `t_down` = `t_release` của ô trước.
- Pedal của ô nhịp N **nhả** tại `t_release` = onset của note đầu tiên (anchor) ở ô nhịp N+1 cộng `d1`.
- Tại `t_release` pedal nhả rồi nhấn lại ngay: đây là điểm chuyển pedal. Note anchor bị cắt đôi: phần 1 dài `d1`, phần 2 bắt đầu đúng `t_release`. Pedal ô sau bắt đầu từ phần 2.
- Công thức cắt: nếu `dur/4 >= 1/16 note` thì `d1 = 1/16 note`, ngược lại `d1 = dur/2`. Sau đó `d2 = dur - d1`.
  - Tính theo quarterLength (QL), `1/16 note = 0.25 QL`. Điều kiện `dur/4 >= 0.25` tương đương `dur >= 1.0 QL` (từ nốt đen trở lên).
  - Ví dụ: nốt tròn 4.0 cho `0.25 + 3.75`. Nốt đen 1.0 cho `0.25 + 0.75`. Móc đơn 0.5 cho `0.25 + 0.25`. Móc kép 0.25 cho `0.125 + 0.125`.
- **Hai lớp dữ liệu, không trộn:**
  - Lớp **hiển thị** (khuôn nhạc): giữ note gốc, không có note bị cắt. Pedal vẽ từ đầu đến hết ô nhịp.
  - Lớp **trình diễn** (audio, animation, scheduler): dùng note đã cắt và pedal event thật.
- Ô nhịp thiếu (pickup/anacrusis) ở đầu bài **không** nhấn pedal.

## 1. Kiến trúc

Tạo module thuần hàm, ví dụ `auto_pedal.py`, không phụ thuộc UI hay audio:

```
build_performance(notes, measures, settings) -> PerformancePlan
PerformancePlan:
    perf_notes:          list[NoteSegment]       # cho scheduler/audio
    pedal_events:        list[PedalEvent]        # DOWN/UP đã sort
    pedal_spans_perf:    list[(t_down, t_release, measure_no)]
    pedal_spans_display: list[(measure_start, measure_end, measure_no)]
```

- **Không mutate** stream gốc. Nếu dùng music21 thì không sửa `Part/Measure` đang dùng để render notation. Chỉ đọc rồi sinh list mới.
- Cache plan theo `(file_id, pedal_mode, pickup_override)`. Rebuild khi đổi file hoặc đổi setting.
- Mọi phép tính ở **miền beat/QL**, dùng `fractions.Fraction` để tránh sai số float (music21 vốn trả Fraction). Với MIDI thì dùng ticks (số nguyên). Chỉ đổi sang giây ở bước cuối qua tempo map. Nhờ đó đổi tempo hoặc speed không làm lệch pedal.

## 2. Data model

```python
@dataclass
class Measure:
    start; end; number; is_pickup: bool; time_sig

@dataclass
class NoteSegment:
    note_id; pitch; onset; dur; velocity; staff   # staff/hand
    tie_group: int | None      # cùng note gốc
    is_continuation: bool      # phần 2: KHÔNG retrigger attack

@dataclass
class PedalEvent:
    t; kind: Literal["DOWN", "UP"]; measure_no
```

## 3. Bước 1: bảng ô nhịp (`Measure` table)

- **MusicXML:** lấy từ measure có sẵn. Ô nhịp đầu là pickup nếu `number == 0`, hoặc `implicit="yes"`, hoặc `highestTime < barDuration`. Chỉ xét ô nhịp **đầu tiên**.
- **MIDI:** không có khái niệm measure, nên dựng từ Time Signature meta và `ticks_per_beat`: `bar_len = num * 4/den * tpb`. Phải xử lý đổi nhịp giữa bài.
  - Pickup trong MIDI không phát hiện chắc chắn được. Thêm heuristic (ví dụ đo độ lệch của tổng độ dài so với lưới bar) kèm **override thủ công** `pickup_beats` ở setting.
- **Lặp (repeat/volta/D.C.):** pedal phải tính trên timeline **đã unroll** theo thứ tự chơi (music21: `expandRepeats()`). Với khuôn nhạc thì mỗi ô nhịp chỉ vẽ một lần. Model cần xem source hiện xử lý repeat ở tầng nào rồi chọn đúng tầng để chèn.
- Hợp nhất mọi part/hand thành **một** dòng note, vì chỉ có một pedal.

## 4. Bước 2: tìm anchor của ô nhịp N+1

```python
def pick_anchor(notes, m):
    cand = [n for n in notes
            if m.start <= n.onset < m.end
            and n.dur > 0                    # bỏ grace note
            and not n.is_tie_continuation]   # nốt nối từ ô trước không phải attack mới
    if not cand:
        return None
    t0 = min(n.onset for n in cand)
    cluster = [n for n in cand if n.onset == t0]
    return sorted(cluster, key=lambda n: (n.staff, -n.pitch))[0]   # deterministic
```

Hợp âm có nhiều note cùng onset thì chọn một anchor ổn định (để thành hàm riêng, dễ đổi). Mặc định chỉ cắt anchor. Có thể thêm option cắt cả cluster.

## 5. Bước 3 và 4: cắt note và sinh pedal

```python
SIXTEENTH = Fraction(1, 4)

def split_lengths(dur):
    d1 = SIXTEENTH if dur / 4 >= SIXTEENTH else dur / 2
    return d1, dur - d1

def build_auto_pedal(measures, notes):
    ms = [m for m in measures if not m.is_pickup]
    spans, splits = [], {}
    t_down = ms[0].start
    for i, m in enumerate(ms):
        if i + 1 < len(ms):
            nxt = ms[i + 1]
            a = pick_anchor(notes, nxt)
            if a:
                d1, d2 = split_lengths(a.dur)
                t_rel = min(a.onset + d1, nxt.end)    # clamp
                splits[a.note_id] = (t_rel, d1, d2)
            else:
                t_rel = nxt.start                     # ô nhịp toàn nghỉ: đổi pedal tại vạch
        else:
            t_rel = m.end                             # ô nhịp cuối: nhả ở cuối
        spans.append((t_down, t_rel, m.number))
        t_down = t_rel
    events = [e for (a, b, n) in spans
              for e in (Ev(a, "DOWN", n), Ev(b, "UP", n))]
    events.sort(key=lambda e: (e.t, e.kind != "UP"))  # cùng t thì UP trước DOWN
    return spans, splits, events
```

Display spans đơn giản là `(m.start, m.end)` của từng ô nhịp không phải pickup.

Invariant nên `assert`:
- `d1 + d2 == dur`
- `t_rel` tăng nghiêm ngặt
- mỗi `DOWN` đều có `UP`

## 6. Tích hợp audio và scheduler

- **Ngữ nghĩa sustain:** pedal đang DOWN thì note-off bị hoãn tới khi pedal UP. Khi UP, nhả mọi note không còn được ngón giữ. Note đang được giữ theo score vẫn kêu bình thường.
- **Phần 2 của note bị cắt** (`is_continuation=True`) **không** phát attack mới và **không** gửi note-off giữa hai phần. Gộp chúng lại: note-on ở phần 1, note-off ở cuối phần 2. Âm thanh vẫn y hệt note gốc, phần cắt chỉ làm mốc cho pedal. Animation phím và tay cũng coi đó là một lần nhấn liên tục.
- Cùng timestamp thì xử lý **UP trước DOWN**, nếu không DOWN sẽ bị huỷ. Có thể thêm setting `pedal_gap_ms` (mặc định 0) nếu muốn khoảng nhả nghe rõ hơn.
- **Seek, loop, đổi tempo:** trạng thái pedal tại thời điểm `t` là event cuối cùng có `t_event <= t`. Khi seek giữa bài phải khôi phục đúng trạng thái này.

## 7. Hiển thị khuôn nhạc

- Dùng `pedal_spans_display` để vẽ dưới khuôn nhạc dưới. Mỗi ô nhịp một bracket: `Ped.` ở ô đầu, đường kẻ từ đầu ô nhịp đến hết ô nhịp, có nét nhỏ ở hai đầu. Vẽ ở mode `AUTO_MEASURE`.
- Không vẽ note bị cắt. Vị trí x của bracket lấy từ chỗ renderer đã tính x của vạch nhịp.
- Mode `SCORE` thì vẽ theo pedal có trong file (nếu có).

## 8. Setting

```python
class PedalMode(Enum):
    SCORE          # theo bản nhạc
    AUTO_MEASURE   # tự động theo ô nhịp (thuật toán trên)
    # OFF          # tuỳ chọn
```

- Màn hình Setting thêm lựa chọn Pedal: "Theo bản nhạc" / "Tự động theo ô nhịp". Có thể thêm ô tuỳ chọn `pickup_beats` cho MIDI.
- Lưu vào file settings hiện có. Đổi mode thì rebuild plan, áp dụng ở lần play/stop kế tiếp.
- Khi `AUTO_MEASURE` bật, **bỏ qua** pedal có sẵn trong file (CC64 trong MIDI, `<pedal>` trong MusicXML) để tránh chồng pedal.

## 9. Edge cases và quyết định mặc định

| Tình huống | Xử lý mặc định |
|---|---|
| Ô nhịp pickup đầu | Không pedal. Pedal bắt đầu ở đầu ô nhịp 1, không cắt note |
| Ô nhịp cuối | Nhấn ở đầu, nhả ở cuối ô nhịp, không cắt note |
| Ô nhịp toàn nghỉ (không có anchor) | Đổi pedal tại vạch nhịp, không cắt |
| Anchor là nốt nối (tie) từ ô trước | Bỏ qua, lấy attack mới đầu tiên |
| Grace note (dur = 0) | Không làm anchor |
| Anchor ngắn, `t0 + d1` vượt hết ô nhịp | Clamp `t_rel <= nxt.end` |
| Đổi nhịp (time sig change) | Mỗi ô nhịp dùng độ dài riêng |
| Hợp âm nhiều staff | Anchor chọn theo `(staff, -pitch)`, chỉ cắt anchor |
| Có repeat | Tính trên timeline đã unroll |

## 10. Test case

Dùng nhịp 4/4, ô 1 và ô 2 đều đủ nhịp:

1. Ô 2 bắt đầu bằng nốt tròn 4.0 QL: `d1=0.25`, `d2=3.75`, `t_rel = start_m2 + 0.25`. Pedal ô 1 là `[start_m1, start_m2+0.25]`, pedal ô 2 bắt đầu từ `start_m2+0.25`.
2. Ô 2 bắt đầu bằng móc kép 0.25: `d1=0.125`, `d2=0.125`.
3. Có pickup: ô pickup không có pedal, pedal đầu tiên bắt đầu ở đầu ô nhịp 1.
4. Ô nhịp bắt đầu bằng dấu lặng 1 phách rồi mới có nốt: anchor là nốt đó, `t_rel = onset + d1` (nằm trong ô nhịp, không phải đầu ô).
5. Ô nhịp toàn nghỉ: `t_rel` đúng tại vạch nhịp.
6. Tổng trường âm (`d1 + d2`) bằng đúng note gốc, với cả tuplet (dùng Fraction để kiểm).
7. Render audio hai lần (có cắt và không cắt, pedal tắt): âm thanh note anchor không bị retrigger.

## 11. Checklist để model tìm trong source

1. **Importer:** nơi parse MusicXML/MIDI và cấu trúc dữ liệu note, measure (có lưu measure number, tie, staff không).
2. **Đơn vị thời gian:** đang dùng giây, tick hay QL. Cần một chỗ đổi từ beat sang giây.
3. **Scheduler/player:** nơi sinh note-on/off, và nơi (nếu có) đã xử lý CC64 hay sustain.
4. **Audio engine:** cách xử lý note-off, release envelope, voice.
5. **Animation phím/tay:** có đọc từ cùng list note không. Phải đảm bảo chúng nhận `perf_notes` đã gộp, không giật phím giữa hai phần cắt.
6. **Notation renderer:** nơi tính x của vạch nhịp, nơi vẽ dưới khuôn nhạc.
7. **Settings:** cấu trúc lưu và đọc settings, màn hình setting.
8. **Xử lý repeat và seek/loop:** để chèn plan đúng tầng.

## 12. Điểm cần xác nhận

- **Hợp âm ở đầu ô nhịp:** chỉ cắt một anchor (mặc định) hay cắt mọi note crossing `t_rel`?
- **Pickup của MIDI:** chấp nhận heuristic kèm override thủ công, hay luôn bắt người dùng nhập?
