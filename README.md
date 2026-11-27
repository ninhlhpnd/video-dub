# Video Dub – Lồng tiếng Việt tự động

App Windows: kéo thả video → nhận dạng lời → dịch tiếng Việt (giữ timeline) → xóa giọng gốc (giữ nhạc/tiếng động) → đọc tiếng Việt → nhạc nền + phụ đề (có thể che phụ đề gốc bằng dải màu) → xuất mp4 → đăng/hẹn giờ lên YouTube, Fanpage Facebook.

Hai chế độ:
- **Lồng tiếng**: dịch lời thoại và lồng tiếng. Bật "Mỗi nhân vật một giọng" thì Gemini nghe để phân biệt người nói và nam/nữ, mỗi nhân vật một giọng VieNeu.
- **Review phim**: Gemini xem video và viết lời kể; tiếng phim gốc tự nhỏ lại khi có lời kể. Chọn số phút > 0 để AI chọn cảnh chính và tự cắt ghép (tóm tắt phim dài, xem từng đoạn 15 phút).

Khi đăng bài, AI tự viết tiêu đề, mô tả ngắn gây tò mò và hashtag.

## Cài đặt trên máy mới
Yêu cầu: Windows 10/11, Python 3.x, card NVIDIA (khuyên dùng, không có vẫn chạy bằng CPU), Flutter + Visual Studio 2022 (C++ desktop) để build app.

```powershell
git clone https://github.com/ninhlhpnd/video-dub.git
cd video-dub
powershell -ExecutionPolicy Bypass -File setup.ps1
```
Script tạo 2 môi trường Python, tải FFmpeg và build app. Mô hình AI (~2GB) tự tải ở lần chạy đầu.

Mở app: `app\build\windows\x64\runner\Release\video_dub.exe`

## Lần đầu sử dụng
1. **Cài đặt → Gemini API key** (miễn phí tại aistudio.google.com). Gói miễn phí: model Flash ~20 lượt/ngày, Flash-Lite ~500 lượt/ngày; app tự chuyển model khi hết lượt hoặc máy chủ quá tải.
2. (Tùy chọn) **YouTube**: OAuth client "Desktop app" trên Google Cloud, bật YouTube Data API v3, chọn file `client_secret.json`. Chưa qua audit của Google thì video bị giữ ở chế độ riêng tư.
3. (Tùy chọn) **Facebook**: Cài đặt → Kết nối Fanpage, làm theo hướng dẫn trong hộp thoại (App ID, App Secret, User Token với quyền `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`). App tự đổi sang Page Token không hết hạn.

## Truyện dài nhiều chương → video từng tập
Màn hình chính → **Truyện dài** → **Bộ truyện mới**: chọn thể loại, ý tưởng, số chương (tới vài nghìn), độ dài mỗi chương (mặc định 3000 ký tự ≈ 1 trang A4) và độ dài mỗi tập video (15-20 phút).

1. **Lập sổ tay**: AI thiết kế thế giới, quy luật/hệ thống sức mạnh, 8-14 nhân vật (kèm ngoại hình để vẽ), địa danh, giọng văn, kết cục và chia thành các quyển (~40 chương/quyển). Sửa được hết trước khi viết.
2. **Viết thêm 1/10/30/100 chương**: mỗi chương AI nhận sổ tay + tóm tắt các quyển đã xong + dàn ý chi tiết quyển đang viết (lập khi bắt đầu quyển, dựa trên những gì đã thật sự viết) + trạng thái hiện tại của nhân vật + các nút thắt còn mở + tóm tắt 12 chương gần nhất + nguyên văn 2 chương liền trước. Sau mỗi chương AI cập nhật trạng thái/nút thắt, nên truyện nghìn chương vẫn liền mạch mà mỗi lần gọi chỉ ~25 nghìn token. Dừng giữa chừng không mất gì, bấm viết tiếp là chạy tiếp.
3. Sửa tay một chương thì chương sau tự bám theo bản đã sửa. **Viết lại từ chương N** xóa chương N trở đi và trả trí nhớ AI về đúng lúc sau chương N-1.
4. **Tạo tập video**: lấy các chương tiếp theo đủ ~17 phút thành một project Truyện kể (đầu tập có lời nhắc tập trước, cuối tập hé lộ tập sau; nhân vật và phong cách tranh giữ nguyên cả bộ; tiêu đề YouTube dạng "Tên truyện - Tập N: …", mô tả có link đọc truyện chữ). Sau đó chạy tiếp các bước chia cảnh → vẽ ảnh → đọc → ghép video → đăng như truyện ngắn.
5. **Đăng lên web đọc truyện** (thư mục `web/`): đăng/cập nhật các chương mới hoặc đã sửa, có thể bật tự đăng sau mỗi lượt viết.

Người viết (Cài đặt → Viết truyện): Gemini Flash (miễn phí, giới hạn lượt/ngày), Claude API (trả theo token; khối sổ tay và dàn ý quyển được lưu đệm prompt nên các chương sau rẻ hơn), hoặc **Claude Code** đã đăng nhập trên máy (`claude -p`, chế độ không công cụ, tính vào hạn mức gói Pro/Max).

Dữ liệu: `<thư mục project>\_series\<id>\series.json` và `chapters\0001.json`… (mỗi chương lưu kèm bản chụp trí nhớ AI).

## Web đọc truyện
Thư mục `web/`: trang đọc truyện (FastAPI + SQLite) cho độc giả đọc, người dùng tự đăng truyện, ủng hộ (donate) tác giả qua QR ngân hàng, và nhập truyện Trung từ link/file .txt để AI tự dịch và đăng từng chương. Xem `web/README.md`.

## Cấu trúc
| Thư mục | Nội dung |
|---|---|
| `engine/` | Python. `dub.py` là lệnh chính, các bước ở `vdub/` |
| `engine/.venv` | Môi trường chính: torch CUDA, faster-whisper, demucs, Gemini |
| `engine/.venv_tts` | Môi trường giọng VieNeu-TTS (tách riêng), gọi qua `vieneu_worker.py` |
| `tools/` | ffmpeg, mô hình, token YouTube, file nghe thử (không đưa lên git) |
| `app/` | Giao diện Flutter Windows |
| `web/` | Web đọc truyện (Python FastAPI + SQLite), chạy riêng |

Project lưu ở `%USERPROFILE%\Videos\VideoDub\` (đổi được trong Cài đặt). Cài đặt và các key lưu ở `%APPDATA%\VideoDub\settings.json` (không nằm trong repo).

## Chạy engine bằng dòng lệnh
```
cd engine
.venv\Scripts\python dub.py new --video D:\a.mp4 --workspace D:\VideoDub
.venv\Scripts\python dub.py run --project <thư mục project> --settings %APPDATA%\VideoDub\settings.json
.venv\Scripts\python dub.py run --from tts --project <...>      # sau khi sửa phụ đề
.venv\Scripts\python dub.py preview --voice "vieneu:Trúc Ly"    # nghe thử giọng
```
Mỗi dòng stdout là một JSON (`progress`, `log`, `step`, `done`, `error`).

## Ghi chú kỹ thuật
- **Nhận dạng:** faster-whisper `large-v3-turbo` trên GPU (dùng cuDNN/cuBLAS đi kèm torch).
- **Mỗi bước chạy ở tiến trình riêng:** ctranslate2 và torch mang 2 bản `cudnn64_9.dll` khác nhau, nạp chung sẽ crash.
- **Tách giọng:** Demucs `htdemucs`, cắt đoạn 5 phút để không tràn VRAM.
- **Giọng đọc:**
  - VieNeu-TTS: 25 giọng, chạy CPU, miễn phí, mặc định "Trúc Ly".
  - Edge TTS: online, có bản chỉnh cao độ/tốc độ.
  - Câu quá dài thì nhờ Gemini rút gọn, rồi tăng tốc tối đa `max_speed`; vẫn lố thì câu sau lùi lại một chút.
- **VieNeu trên Windows:** cache Hugging Face tách file `.onnx` và `.data` ra 2 thư mục blob khác nhau nên onnxruntime từ chối nạp. `vieneu_worker.py` tải thẳng vào `tools/models/vieneu`.
- **Che phụ đề gốc:** dò vùng chữ in cứng bằng cạnh sáng/tối qua nhiều khung hình, phủ `drawbox` và đặt phụ đề Việt giữa dải.
- **Xuất video:** thử `h264_nvenc` trước, lỗi thì dùng `libx264`.
