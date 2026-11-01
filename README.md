# Video Dub – Lồng tiếng Việt tự động

App Windows: kéo thả video → nhận dạng lời → dịch tiếng Việt (giữ timeline) → xóa giọng gốc (giữ nhạc/tiếng động) → đọc tiếng Việt → nhạc nền + phụ đề (có thể che phụ đề gốc bằng dải màu) → xuất mp4 → đăng/hẹn giờ lên YouTube, Fanpage Facebook.

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

## Cấu trúc
| Thư mục | Nội dung |
|---|---|
| `engine/` | Python. `dub.py` là lệnh chính, các bước ở `vdub/` |
| `engine/.venv` | Môi trường chính: torch CUDA, faster-whisper, demucs, Gemini |
| `engine/.venv_tts` | Môi trường giọng VieNeu-TTS (tách riêng), gọi qua `vieneu_worker.py` |
| `tools/` | ffmpeg, mô hình, token YouTube, file nghe thử (không đưa lên git) |
| `app/` | Giao diện Flutter Windows |

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
