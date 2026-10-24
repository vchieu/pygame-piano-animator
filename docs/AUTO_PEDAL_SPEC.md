# AUTO PEDAL — SPEC (khớp với cài đặt hiện tại)

Cập nhật theo `auto_pedal.py` hiện tại (đổi pedal theo **hợp âm tay trái**, có `mid_bar`). Bản spec đầu tiên mô tả "pedal theo từng ô nhịp, cắt đôi note anchor"; hướng đó đã được thay thế, xem mục 10.

---

## 1. Hành vi tóm tắt

- Pedal sustain được tự động sinh từ **chuyển động hợp âm của tay trái (LH/bass)**, không phải mỗi ô một lần.
- Pedal **nhấn** ở đầu ô nhịp đầu tiên (không phải pickup) mà LH có attack ở phách 1.
- Pedal chỉ **đổi** (nhả rồi đạp lại) khi hợp âm LH thật sự đổi:
  - **Ở vạch nhịp:** ô sau có attack LH mới ở phách 1 **và** hợp âm khác ô trước. Hợp âm lặp, Alberti cùng hợp âm: giữ pedal liên tục qua vạch nhịp. Ô sau không có attack LH (LH nghỉ hoặc giữ nốt): giữ pedal.
  - **Giữa ô (`mid_bar`, mặc định bật):** tại một nốt bass LH dài, nằm đúng phách, làm đổi màu hợp âm.
- Điểm đổi: nhả pedal ngay **sau** attack của nốt bass mới (`d1` sau onset), đạp lại sau `gap_s` = 30 ms. Nhờ đó nốt bass mới được pedal mới bắt lấy.
- Pickup đầu bài không có pedal. Ô cuối nhả ở `min(hết ô, duration + 0.3 s)`.
- Không giữ cùng một hợp âm quá `max_hold_bars` (mặc định 4) ô: tới hạn thì làm mới ở vạch nhịp có attack LH.

## 2. Các file và vai trò

| File | Vai trò |
|---|---|
| `auto_pedal.py` | Module thuần hàm, `build(song, ...) -> (controls, spans)`. Không phụ thuộc UI/audio, chỉ import `LEFT, RIGHT, Note` từ `midi_loader` |
| `midi_loader.py` | `MidiSong`: `bar_times`, `beat_times`, `quarter_times`, `pickup_end`, `raw_controls`, `controls`, `pedal_mode`, `pedal_spans`; `set_pedal_mode("score"\|"auto")` |
| `musicxml_loader.py` | Điền `bar_times`, `beat_times`, `quarter_times`, `pickup_end` theo thứ tự chơi (đã tính repeat) |
| `sf_synth.py` | Xử lý sustain: nhả pedal không làm tắt phím LH còn đang giữ nên **không cần cắt note** |
| `staff.py` | `draw_pedal_brackets` vẽ bracket "Ped." dưới khuôn nhạc dưới |
| `app_settings.py` | Nút Pedal: "From the score" / "Auto by measure" (lưu `pedal_mode`) |

`set_pedal_mode("auto")` gọi `auto_pedal.build(self)`; nếu không có bảng ô nhịp (`bar_times` rỗng) thì tự lùi về `"score"`. `raw_controls` (pedal gốc của file) luôn được giữ nguyên; quay lại `"score"` thì khôi phục từ đó.

## 3. Đơn vị thời gian và dữ liệu vào

- Mọi thời điểm tính bằng **giây**. Độ dài một phách tại thời điểm `t` lấy từ `song.quarter_length(t)` (từ `quarter_times`, mặc định 0.5 s).
- Bảng ô nhịp (`measure_table`): dựng từ `bar_times`; thêm 0.0 ở đầu nếu thiếu, thêm mốc cuối bằng `song.duration`. Ô đầu là **pickup** nếu `pickup_end > 0` và hết ô ≤ `pickup_end`.
- Không có `bar_times` thì không sinh pedal tự động.

## 4. Định nghĩa dùng chung

- **LH / bass** (`lh_notes`): nốt `hand == LEFT` hoặc `pitch < 60`, bỏ grace và nốt dài ≤ 0. Nếu bài không có nốt nào như vậy thì dùng mọi nốt.
- **Cửa sổ attack ở vạch nhịp** (`attack_window(q)`): từ `0.2q` **trước** đến `0.3q` **sau** vạch (120 bpm: 0.1 s / 0.15 s). Nhận cả nốt đánh sớm hơn vạch.
- **Cụm attack** (`attack_cluster`): onset sớm nhất trong cửa sổ, cộng các nốt LH lệch ≤ 40 ms so với nó. Rỗng nghĩa là LH không attack ở phách 1.
- **Anchor** (`pick_anchor`): nốt LH **thấp nhất** trong cụm attack (ưu tiên `hand == LEFT`). Nếu LH không attack thì lấy onset đầu tiên của ô (hàm dự phòng).
- **Chữ ký hợp âm** (`harmony_signature(notes, t0, win, tol)`): `(bass_pc, pcs)`. `bass_pc` là pitch class của nốt LH thấp nhất vang trong `[t0 - tol, t0 + win)`. `pcs` gồm các pitch class chiếm ≥ 12% tổng độ dài vang, cộng `bass_pc`.
- **Đổi hợp âm ở vạch nhịp** (`harmony_changed`): `bass_pc` khác, hoặc cùng bass nhưng Jaccard của hai `pcs` ≤ 0.5 (ví dụ C trưởng sang C thứ).
- **Đổi màu hợp âm giữa ô** (`pcs_changed`): chỉ so hai tập `pcs`, Jaccard ≤ 0.5. Không xét bass riêng, nên bass nhảy gốc sang quãng năm trong cùng hợp âm (oom-pah) không tính là đổi.

## 5. Điểm đổi pedal: `d1`, clamp và gap

```python
d1 = q/4 if dur >= q else dur/2          # split_lengths(dur, q); d2 chỉ để tham khảo
d1 = min(d1, max(0.005, dur - gap_s - 0.005))   # đạp lại trước khi nốt bass hết
t_rel  = min(onset + d1, hết_ô_chứa_điểm_đổi)
t_down_mới = t_rel + gap_s
```

- `dur` là độ dài nốt anchor, `q` là độ dài một phách (giây). `d1` bằng một móc kép nếu nốt từ nốt đen trở lên, ngược lại bằng nửa nốt.
- Spec ban đầu cắt đôi nốt anchor. Hiện **không cắt**: `d1` chỉ để tính `t_rel`, nốt giữ nguyên.
- Event cùng thời điểm: UP (0) đứng trước DOWN (127). Event liên tiếp cùng giá trị được gộp.

## 6. Thuật toán `build`

```
build(song, gap_s=0.03, max_hold_bars=4, mid_bar=True,
      mid_min_beats=2.0, mid_min_gap_beats=1.5) -> (controls, spans)
```

Duyệt các ô không phải pickup theo thứ tự:

1. **Khởi động:** chưa nhấn pedal thì tìm cụm attack LH ở đầu ô; không có thì sang ô sau. Có thì `t_down = đầu ô`, lưu chữ ký hợp âm của ô.
2. **Giữa ô (nếu `mid_bar`):** xét từng nốt bass (thấp nhất của mỗi cụm onset) nằm giữa ô. Đổi pedal tại nốt đó khi thoả **cả bốn**:
   - nằm đúng một phách (lệch ≤ 0.25 phách so với `beat_times`; nếu không có `beat_times` thì bỏ qua điều kiện này);
   - dài ≥ `mid_min_beats` phách (loại Alberti và walking bass);
   - cách lần đạp trước ≥ `mid_min_gap_beats` phách;
   - `pcs_changed(hợp âm đang đạp, hợp âm từ nốt này)`. Hợp âm mới tính trên đoạn `[onset, min(độ dài nốt, hết ô)]`.
3. **Ô cuối:** nhả pedal tại `min(hết ô, duration + 0.3)` rồi kết thúc.
4. **Vạch nhịp sang ô sau:**
   - Không có cụm attack LH ở ô sau: giữ pedal.
   - Có: tính chữ ký hợp âm của ô sau. Pedal đổi nếu `harmony_changed(...)` hoặc đã giữ ≥ `max_hold_bars` ô; ngược lại giữ.
   - Khi một nốt bass dài (≥ `mid_min_beats` phách) mở ô, cửa sổ chữ ký giới hạn trong độ dài nốt đó, để hợp âm kế tiếp của cùng ô không lẫn vào.

Đầu ra:
- `controls`: `[(t, 64, 127 | 0), ...]`, đã sort và gộp.
- `spans`: `[(t_down, t_rel), ...]` cho khuôn nhạc. Hai span liên tiếp cách nhau đúng `gap_s`.

## 7. Audio và scheduler

- `MidiSong.controls` chính là danh sách CC64 mà player phát; mode `"auto"` thay nó bằng kết quả `build`. Pedal gốc của file nằm ở `raw_controls`, không bị mất.
- `sf_synth` giữ phím đang bấm khi pedal nhả, nên nốt bass mới không bị cắt và không cần ghép/cắt nốt.
- Seek, loop: trạng thái pedal tại `t` là event cuối cùng có `t_event <= t` (đã có sẵn qua `control_state`).

## 8. Hiển thị khuôn nhạc

- `draw_pedal_brackets(surf, spans, sys_start, sys_end, x_of, y, s)` vẽ hoàn toàn theo thời gian, không giả định span khớp ô nhịp. Mỗi span được cắt theo từng system.
- Hai span cách nhau < 0.1 s được coi là một điểm đổi và vẽ dấu ∧. Nếu đổi `gap_s` lớn hơn 0.1 thì phải nâng ngưỡng này.
- Nhãn "Ped." chỉ vẽ một lần cho mỗi lời gọi.
- Bracket không còn bằng đúng một ô nhịp: nó dài tới điểm đổi hợp âm thật (có thể nhiều ô, hoặc ngắn hơn một ô khi có `mid_bar`).

## 9. Setting

- `pedal_mode` trong settings app: `"score"` (mặc định, giữ pedal của file) hoặc `"auto"`.
- Đổi trong màn hình Settings thì gọi `song.set_pedal_mode(...)` ngay trên bài đang mở.
- Các tham số của `build` (`gap_s`, `max_hold_bars`, `mid_bar`, `mid_min_beats`, `mid_min_gap_beats`) hiện **chưa** đưa lên Setting. Muốn thêm thì truyền từ nơi gọi `build` trong `set_pedal_mode`.

## 10. Khác biệt so với spec cũ

| Spec cũ | Hiện tại |
|---|---|
| Đổi pedal ở **mọi** vạch nhịp | Chỉ đổi khi hợp âm LH đổi (và mid-bar) |
| Anchor là nốt đầu ô bất kỳ | Anchor là nốt LH thấp nhất trong cụm attack ở phách 1 |
| Cắt đôi nốt anchor (`NoteSegment`, `perf_notes`) | Không cắt nốt, synth đã xử lý |
| Tính theo QL / Fraction / ticks | Tính theo giây, tempo qua `quarter_length(t)` |
| `PerformancePlan`, `PedalMode` enum, `pedal_spans_display` riêng | `build` trả `(controls, spans)`; mode là chuỗi `"score"/"auto"` |
| Bracket vẽ đúng một ô nhịp | Bracket theo span thật |
| Cửa sổ attack cố định | Co giãn theo tempo, nhận cả nốt đánh sớm hơn vạch |
| Ô cuối nhả ở hết ô | Nhả ở `min(hết ô, duration + 0.3)` |

## 11. Edge cases

| Tình huống | Xử lý |
|---|---|
| Pickup đầu bài | Không pedal; pedal bắt đầu ở ô đầy đủ đầu tiên có attack LH |
| Ô mở đầu bằng dấu lặng LH | Không có cụm attack, giữ pedal (hoặc chưa nhấn nếu chưa bắt đầu) |
| LH đánh sớm hơn vạch (MIDI biểu diễn) | Vẫn nhận là attack của ô sau, anchor là chính nốt đó |
| LH vào muộn ở phách 2 với bass mới dài | Đổi pedal ở nốt đó khi `mid_bar` bật |
| Nốt bass staccato rất ngắn | `d1` clamp để đạp lại trước khi nốt hết |
| Alberti, walking bass, oom-pah cùng hợp âm | Một span liên tục |
| Oom-pah đổi hợp âm giữa ô (C sang G7) | Đổi ở nốt bass mới (`mid_bar`) |
| Cùng hợp âm kéo dài nhiều ô | Làm mới sau `max_hold_bars` ô |
| Bài không có nốt LH nào | Mọi nốt được coi là LH |
| Không có `bar_times` | Tự lùi về `"score"` |
| Đổi nhịp, repeat | Theo `bar_times` do loader sinh ra (đã theo thứ tự chơi) |

## 12. Test

`tests/test_auto_pedal.py` (chạy bằng pytest) gồm:
- `split_lengths`, `lh_notes`, `harmony_signature`, `harmony_changed`, `has_lh_attack`, `pick_anchor`.
- `build`: hai ô, pickup/lead-in, ô không có attack LH, Alberti cùng hợp âm, hợp âm lặp, `max_hold_bars`, cùng bass đổi màu, LH đánh sớm, anchor là bass chứ không phải giai điệu RH, nốt bass staccato, ô cuối.
- `mid_bar`: đổi ở phách 3, tắt `mid_bar`, LH vào phách 2, walking bass không bị chặt, oom-pah cùng hợp âm, oom-pah C sang G7, bass lệch phách.
- `set_pedal_mode` và việc giữ `raw_controls`; vẽ `draw_pedal_brackets`; `StaffView`; nút Settings (cần pygame).

## 13. Hạn chế đã biết

- Hợp âm đổi giữa ô bằng nốt bass ngắn (ví dụ Alberti đổi hợp âm ở phách 3) bị coi là nốt qua, chỉ bắt được ở vạch nhịp sau. Cải tiến: kiểm tra theo cửa sổ hợp âm thay vì theo từng nốt bass.
- Chỉ xét LH. Giai điệu RH không ảnh hưởng điểm đổi pedal.
- Với MIDI biểu diễn, vạch nhịp suy từ tempo map có thể lệch bản chơi thật; pickup MIDI dựa trên heuristic của loader (`pickup_end`).
- Các ngưỡng (Jaccard 0.5, 12%, `mid_min_beats`...) chọn theo lẽ thường, cần chỉnh bằng cách nghe thử bài thật.
