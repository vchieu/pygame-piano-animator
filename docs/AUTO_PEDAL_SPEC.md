# AUTO PEDAL — SPEC (khớp với cài đặt hiện tại)

Cập nhật theo `auto_pedal.py` hiện tại (đổi pedal theo **hợp âm tay trái**, có `mid_bar`, có giới hạn giữ pedal khi LH không attack, có đuôi vang ở ô cuối). Bản spec đầu tiên mô tả "pedal theo từng ô nhịp, cắt đôi note anchor"; hướng đó đã được thay thế, xem mục 10.

---

## 1. Hành vi tóm tắt

- Pedal sustain được tự động sinh từ **chuyển động hợp âm của tay trái (LH/bass)**, không phải mỗi ô một lần.
- Pedal **nhấn** ở đầu ô nhịp đầu tiên (không phải pickup) mà LH có attack ở phách 1.
- Pedal chỉ **đổi** (nhả rồi đạp lại) khi hợp âm LH thật sự đổi:
  - **Ở vạch nhịp:** ô sau có attack LH mới ở phách 1 **và** hợp âm khác ô trước. Hợp âm lặp, Alberti cùng hợp âm: giữ pedal liên tục qua vạch nhịp.
  - **Giữa ô (`mid_bar`, mặc định bật):** tại một nốt bass LH dài, nằm đúng phách, làm đổi màu hợp âm.
- Ô sau **không có attack LH** (LH nghỉ hoặc giữ nốt): giữ pedal, nhưng **không quá `max_hold_bars` ô** (xem mục 6, bước 4).
- Điểm đổi: nhả pedal sau attack của nốt bass mới một khoảng `release_delay(q)` (40–80 ms), đạp lại sau đúng `gap_s` = 30 ms. Nhờ đó hợp âm cũ bị tắt, nốt bass mới được pedal mới bắt lấy.
- Pickup đầu bài không có pedal. Ô cuối nhả ở `max(min(hết ô, duration), nốt cuối kết thúc) + 0.3 s`, tối đa `duration + 0.45 s`, để hợp âm kết còn vang.
- Không giữ cùng một hợp âm quá `max_hold_bars` (mặc định 4) ô: tới hạn thì làm mới ở vạch nhịp có attack LH.

## 2. Các file và vai trò

| File | Vai trò |
|---|---|
| `auto_pedal.py` | Module thuần hàm, `build(song, ...) -> (controls, spans)`. Không phụ thuộc UI/audio, chỉ import `LEFT, RIGHT, Note` từ `midi_loader` |
| `midi_loader.py` | `MidiSong`: `bar_times`, `beat_times`, `quarter_times`, `pickup_end`, `raw_controls`, `controls`, `pedal_mode`, `pedal_spans`; `set_pedal_mode("score"\|"auto")` |
| `musicxml_loader.py` | Điền `bar_times`, `beat_times`, `quarter_times`, `pickup_end` theo thứ tự chơi (đã tính repeat) |
| `sf_synth.py` | Xử lý sustain: nhả pedal không làm tắt phím còn đang giữ (`_down`) nên **không cần cắt note**. Phím đã nhả trong lúc pedal giữ (`_ringing`) bị tắt khi pedal nhả |
| `staff.py` | `draw_pedal_brackets` vẽ bracket "Ped." dưới khuôn nhạc dưới |
| `app_settings.py` | Nút Pedal: "From the score" / "Auto by harmony" (lưu `pedal_mode`) |

`set_pedal_mode("auto")` gọi `auto_pedal.build(self)`; nếu không có bảng ô nhịp (`bar_times` rỗng) thì tự lùi về `"score"`. `raw_controls` (pedal gốc của file) luôn được giữ nguyên; quay lại `"score"` thì khôi phục từ đó.

## 3. Đơn vị thời gian và dữ liệu vào

- Mọi thời điểm tính bằng **giây**. Độ dài một phách tại thời điểm `t` lấy từ `song.quarter_length(t)` (từ `quarter_times`, mặc định 0.5 s).
- Bảng ô nhịp (`measure_table`): dựng từ `bar_times`.
  - Các mốc cách nhau < `MIN_BAR_S` = 50 ms được gộp thành một (nhiễu float, nối repeat), nên không có ô dài gần 0.
  - Thêm 0.0 ở đầu nếu thiếu (mốc đầu < 50 ms thì dịch về 0.0). Thêm mốc cuối bằng `song.duration`; nếu mốc cuối đã nằm trong 50 ms trước `duration` thì coi nó là mốc cuối.
  - Ô đầu là **pickup** nếu `pickup_end > 0` và hết ô ≤ `pickup_end`.
- Không có `bar_times` thì không sinh pedal tự động.

## 4. Định nghĩa dùng chung

- **LH / bass** (`lh_notes`, `is_lh_note`): nốt `hand == LEFT`, hoặc nốt **chưa gán tay** (`hand != RIGHT`) có `pitch < 60`. Nốt mà bản nhạc gán cho **tay phải không bao giờ là bass**, dù thấp đến đâu (giai điệu RH xuống dưới đô giữa, hợp âm RH). Bỏ grace và nốt dài ≤ 0. Nếu bài không có nốt nào như vậy thì dùng mọi nốt.
- **Cửa sổ attack ở vạch nhịp** (`attack_window(q)`): từ `0.2q` **trước** đến `0.3q` **sau** vạch (120 bpm: 0.1 s / 0.15 s). Nhận cả nốt đánh sớm hơn vạch.
- **Cụm attack** (`attack_cluster`): onset sớm nhất trong cửa sổ, cộng các nốt LH lệch ≤ 40 ms so với nó. Rỗng nghĩa là LH không attack ở phách 1.
- **Anchor** (`pick_anchor`): nốt LH **thấp nhất** trong cụm attack (ưu tiên `hand == LEFT`). Nếu LH không attack thì lấy onset đầu tiên của ô (hàm dự phòng).
- **Chữ ký hợp âm** (`harmony_signature(notes, t0, win, tol)`): `(bass_pc, pcs)`.
  - `bass_pc` là pitch class của nốt LH thấp nhất **được đánh** trong `[t0 - tol, t0 + win)` (`start >= t0 - tol`). Chỉ khi không có nốt nào được đánh trong cửa sổ mới lấy nốt thấp nhất còn vang từ trước. Nhờ vậy nốt bass cũ, thấp hơn, còn ngân chồng sang không che mất bass mới.
  - `pcs` gồm các pitch class chiếm ≥ 12% tổng độ dài vang, cộng `bass_pc`.
- **Đổi hợp âm ở vạch nhịp** (`harmony_changed`): `bass_pc` khác, hoặc cùng bass nhưng Jaccard của hai `pcs` ≤ 0.5 (ví dụ C trưởng sang C thứ).
- **Đổi màu hợp âm giữa ô** (`pcs_changed`): chỉ so hai tập `pcs`, Jaccard ≤ 0.5. Không xét bass riêng, nên bass nhảy gốc sang quãng năm trong cùng hợp âm (oom-pah) không tính là đổi.

## 5. Điểm đổi pedal: delay, clamp và gap

```python
d1 = release_delay(q)            # min(max(q/8, 0.04), 0.08) giây: 62.5 ms ở 120 bpm, 80 ms ở 60 bpm
d1 = min(d1, max(0.005, dur - gap_s - 0.005))          # đạp lại trước khi nốt bass hết
t_rel  = min(onset + d1, hết_ô_chứa_điểm_đổi - gap_s - 0.01)
t_down_mới = t_rel + gap_s                              # gap luôn đúng gap_s
```

- `dur` là độ dài nốt anchor, `q` là độ dài một phách (giây).
- Delay cố định theo giây (40–80 ms) thay cho `q/4` cũ: ở tempo chậm `q/4` (125–250 ms) để hợp âm cũ vang chồng lên bass mới và làm đục.
- Khi điểm đổi sát cuối ô, **`t_rel` lùi sớm lại**, không co `gap_s`. Hai span liên tiếp luôn cách nhau đúng `gap_s`.
- Không cắt nốt anchor: delay chỉ để tính `t_rel`, nốt giữ nguyên.
- Event cùng thời điểm: UP (0) đứng trước DOWN (127). Event liên tiếp cùng giá trị được gộp.

## 6. Thuật toán `build`

```
build(song, gap_s=0.03, max_hold_bars=4, mid_bar=True,
      mid_min_beats=2.0, mid_min_gap_beats=1.5) -> (controls, spans)
```

Duyệt các ô không phải pickup theo thứ tự:

1. **Khởi động:** chưa nhấn pedal (hoặc pedal đang nhả vì LH im, bước 4c) thì tìm cụm attack LH ở đầu ô; không có thì sang ô sau. Có thì `t_down = đầu ô`, lưu chữ ký hợp âm của ô.
2. **Giữa ô (nếu `mid_bar`):** xét từng nốt bass (thấp nhất của mỗi cụm onset) nằm giữa ô. Đổi pedal tại nốt đó khi thoả **cả bốn**:
   - nằm đúng một phách (lệch ≤ 0.25 phách so với `beat_times`; nếu không có `beat_times` thì bỏ qua điều kiện này);
   - dài ≥ `mid_min_beats` phách (loại Alberti và walking bass);
   - cách lần đạp trước ≥ `mid_min_gap_beats` phách;
   - `pcs_changed(hợp âm đang đạp, hợp âm từ nốt này)`. Hợp âm mới tính trên đoạn `[onset, min(độ dài nốt, hết ô)]`.
3. **Ô cuối:** nhả pedal tại `max(min(hết ô, duration), nốt cuối kết thúc) + FINAL_TAIL_S (0.3 s)`, không muộn hơn `duration + FINAL_TAIL_LIMIT_S (0.45 s)` (Transport dừng ở `duration + 0.5 s`), rồi kết thúc.
4. **Vạch nhịp sang ô sau:**
   - a. **Không có cụm attack LH ở ô sau:** giữ pedal, tăng bộ đếm `held`. Nhưng nếu `held >= max_hold_bars`:
     - b. ô sau có onset LH nào đó: làm mới pedal tại onset LH **đầu tiên** của ô (không đòi đúng phách hay nốt dài);
     - c. LH hoàn toàn im trong ô sau (không có nốt LH nào vang): **nhả pedal ở vạch nhịp** và để nhả cho tới khi LH attack lại (bước 1).
   - Có cụm attack: tính chữ ký hợp âm của ô sau. Pedal đổi nếu `harmony_changed(...)` hoặc đã giữ ≥ `max_hold_bars` ô; ngược lại giữ.
   - Khi `mid_bar` bật và một nốt bass dài (≥ `mid_min_beats` phách) mở ô, cửa sổ chữ ký giới hạn trong độ dài nốt đó, để hợp âm kế tiếp của cùng ô không lẫn vào. Khi `mid_bar` tắt, cửa sổ là cả ô.

Đầu ra:
- `controls`: `[(t, 64, 127 | 0), ...]`, đã sort và gộp.
- `spans`: `[(t_down, t_rel), ...]` cho khuôn nhạc. Hai span liên tiếp của một điểm đổi cách nhau đúng `gap_s`.

## 7. Audio và scheduler

- `MidiSong.controls` chính là danh sách CC64 mà player phát; mode `"auto"` thay nó bằng kết quả `build`. Pedal gốc của file nằm ở `raw_controls`, không bị mất.
- `sf_synth` (đã kiểm chứng trong code): khi pedal nhả, `_release` chỉ tắt các phím **đã nhả** mà pedal đang giữ; phím còn bấm (`_down`) giữ nguyên. Nên nốt bass mới không bị cắt và không cần ghép/cắt nốt. Hệ quả: mọi nốt đã nhả phím trước `t_rel` (kể cả nốt của hợp âm mới đánh quá ngắn) bị tắt đúng lúc pedal nhả. Đó là lý do delay không nên dài.
- `Transport.update(dt)` xử lý event theo **từng frame**: mọi event trong `(t0, t1]` được gửi liên tiếp theo thứ tự thời gian. Gap 30 ms chỉ là khoảng trống thật giữa nhả và đạp khi frame ≲ 30 ms (≥ ~33 fps). Nếu một frame dài hơn gap (giật hình, tốc độ phát cao), nhả và đạp lại được gửi cùng lúc và hiệu ứng "nhấc lên" biến mất, nhưng nốt vẫn bị tắt đúng.
- Playback dừng ở `duration + END_PAD_T (0.5 s)` rồi gọi `pedals_up()` và `all_off()`. Đuôi pedal ở ô cuối (0.3 s) nằm trong khoảng này nên được phát; phần sau đó bị cắt.
- Seek, loop: trạng thái pedal tại `t` là event cuối cùng có `t_event <= t` (đã có sẵn qua `control_state`).

## 8. Hiển thị khuôn nhạc

- `draw_pedal_brackets(surf, spans, sys_start, sys_end, x_of, y, s)` vẽ hoàn toàn theo thời gian, không giả định span khớp ô nhịp. Mỗi span được cắt theo từng system.
- Hai span cách nhau < 0.1 s được coi là một điểm đổi và vẽ dấu ∧. Nếu đổi `gap_s` lớn hơn 0.1 thì phải nâng ngưỡng này.
- Nhãn "Ped." chỉ vẽ một lần cho mỗi lời gọi (mỗi system).
- Bracket không còn bằng đúng một ô nhịp: nó dài tới điểm đổi hợp âm thật (có thể nhiều ô, hoặc ngắn hơn một ô khi có `mid_bar`). Span cuối dài hơn `duration` tới 0.3 s; bị cắt ở cuối system.

## 9. Setting

- `pedal_mode` trong settings app: `"score"` (mặc định, giữ pedal của file) hoặc `"auto"`. Nhãn nút: "From the score" / "Auto by harmony".
- Đổi trong màn hình Settings thì gọi `song.set_pedal_mode(...)` ngay trên bài đang mở.
- Các tham số của `build` (`gap_s`, `max_hold_bars`, `mid_bar`, `mid_min_beats`, `mid_min_gap_beats`) hiện **chưa** đưa lên Setting. Muốn thêm thì truyền từ nơi gọi `build` trong `set_pedal_mode`.

## 10. Khác biệt so với spec cũ

| Spec cũ | Hiện tại |
|---|---|
| Đổi pedal ở **mọi** vạch nhịp | Chỉ đổi khi hợp âm LH đổi (và mid-bar) |
| Anchor là nốt đầu ô bất kỳ | Anchor là nốt LH thấp nhất trong cụm attack ở phách 1 |
| Cắt đôi nốt anchor (`NoteSegment`, `perf_notes`) | Không cắt nốt, synth đã xử lý |
| `d1 = q/4` (`split_lengths`) | `release_delay(q)`: 40–80 ms cố định theo giây |
| Tính theo QL / Fraction / ticks | Tính theo giây, tempo qua `quarter_length(t)` |
| `PerformancePlan`, `PedalMode` enum, `pedal_spans_display` riêng | `build` trả `(controls, spans)`; mode là chuỗi `"score"/"auto"` |
| Bracket vẽ đúng một ô nhịp | Bracket theo span thật |
| Cửa sổ attack cố định | Co giãn theo tempo, nhận cả nốt đánh sớm hơn vạch |
| Ô cuối nhả ở hết ô (hoặc `min(hết ô, duration + 0.3)` = không có đuôi) | Ô cuối nhả ở `kết thúc nhạc + 0.3 s` (tối đa `duration + 0.45 s`) |
| LH không attack: giữ pedal vô hạn | Giữ tối đa `max_hold_bars` ô, sau đó làm mới hoặc nhả |
| Nốt RH thấp hơn đô giữa tính là bass | Nốt gán tay phải không bao giờ là bass |
| Gap co lại khi sát cuối ô | Gap luôn đúng `gap_s`, `t_rel` lùi sớm |
| Nhãn "Auto by measure" | "Auto by harmony" |

## 11. Edge cases

| Tình huống | Xử lý |
|---|---|
| Pickup đầu bài | Không pedal; pedal bắt đầu ở ô đầy đủ đầu tiên có attack LH |
| Ô mở đầu bằng dấu lặng LH | Không có cụm attack, giữ pedal (hoặc chưa nhấn nếu chưa bắt đầu), tối đa `max_hold_bars` ô |
| LH nghỉ ≥ `max_hold_bars` ô, vào lại lệch phách | Làm mới pedal ở onset LH đầu tiên của ô |
| LH im hoàn toàn ≥ `max_hold_bars` ô | Nhả pedal ở vạch nhịp, đạp lại khi LH attack ở phách 1 |
| LH đánh sớm hơn vạch (MIDI biểu diễn) | Vẫn nhận là attack của ô sau, anchor là chính nốt đó |
| LH vào muộn ở phách 2 với bass mới dài | Đổi pedal ở nốt đó khi `mid_bar` bật |
| Bass cũ thấp còn ngân chồng sang ô sau | Bass của chữ ký là nốt vừa đánh, nên vẫn nhận ra đổi hợp âm |
| Nốt bass staccato rất ngắn | Delay clamp để đạp lại trước khi nốt hết, gap vẫn đủ |
| Alberti, walking bass, oom-pah cùng hợp âm | Một span liên tục |
| Oom-pah đổi hợp âm giữa ô (C sang G7) | Đổi ở nốt bass mới (`mid_bar`) |
| Cùng hợp âm kéo dài nhiều ô | Làm mới sau `max_hold_bars` ô |
| Bài không có nốt LH nào | Mọi nốt được coi là LH |
| Không có `bar_times` | Tự lùi về `"score"` |
| Hai mốc ô cách nhau < 50 ms | Gộp thành một mốc |
| Đổi nhịp, repeat | Theo `bar_times` do loader sinh ra (đã theo thứ tự chơi) |

## 12. Test

`tests/test_auto_pedal.py` (chạy bằng pytest) gồm:
- `release_delay`, `measure_table` (gộp mốc gần nhau, mốc cuối sát `duration`), `lh_notes` (nốt RH thấp bị loại, nốt chưa gán tay được giữ), `harmony_signature` (kể cả bass là nốt mới đánh, không phải nốt cũ thấp hơn), `harmony_changed`, `has_lh_attack`, `pick_anchor`.
- `build`: hai ô, gap đúng `gap_s`, pickup/lead-in, ô không có attack LH, giữ pedal có giới hạn (làm mới ở onset LH, nhả khi LH im), Alberti cùng hợp âm, hợp âm lặp, `max_hold_bars`, cùng bass đổi màu, bass mới trên nốt thấp còn vang, LH đánh sớm, anchor là bass chứ không phải giai điệu RH (kể cả RH thấp), nốt bass staccato, ô cuối có đuôi vang.
- `mid_bar`: đổi ở phách 3, tắt `mid_bar`, LH vào phách 2, walking bass không bị chặt, oom-pah cùng hợp âm, oom-pah C sang G7, bass lệch phách.
- `set_pedal_mode` và việc giữ `raw_controls`; vẽ `draw_pedal_brackets`; `StaffView` (hai nốt bass khác nhau, mong đợi 2 span); nút Settings "Auto by harmony" (cần pygame).

## 13. Hạn chế đã biết

- Hợp âm đổi giữa ô bằng nốt bass ngắn (ví dụ Alberti đổi hợp âm ở phách 3) bị coi là nốt qua, chỉ bắt được ở vạch nhịp sau. Cải tiến: kiểm tra theo cửa sổ hợp âm thay vì theo từng nốt bass.
- Chỉ xét LH. Giai điệu RH không ảnh hưởng điểm đổi pedal.
- Với MIDI biểu diễn, vạch nhịp suy từ tempo map có thể lệch bản chơi thật; pickup MIDI dựa trên heuristic của loader (`pickup_end`).
- Việc loại nốt RH khỏi bass tin vào nhãn tay của loader. Với MIDI mà `hand_split` gán sai tay, anchor và chữ ký hợp âm sai theo.
- Pedal chỉ khởi động khi LH attack ở phách 1 của một ô. Nếu LH vào lệch phách ngay từ đầu bài (hoặc sau khi nhả vì LH im), pedal chưa bắt đầu cho tới ô có attack ở phách 1.
- Các ngưỡng (Jaccard 0.5, 12%, `mid_min_beats`, delay 40–80 ms, đuôi 0.3 s...) chọn theo lẽ thường, cần chỉnh bằng cách nghe thử bài thật.
- Hiệu năng: mỗi lần quét duyệt toàn bộ nốt LH, tức O(số ô × số nốt). Đo thử bài 300 ô, 4800 nốt: ~0.2 s mỗi lần gọi `build`. Nếu bài dài hơn nhiều mà UI khựng khi đổi mode, sort theo `start` và dùng `bisect` để cắt đoạn.
