# Trang đọc truyện (web)

Trang web đọc truyện chữ tiếng Việt đi kèm ứng dụng **Video Dub**. Truyện đến từ 3 nguồn:

1. **Truyện AI viết** trong app máy tính, đẩy lên qua JSON API (xem [API.md](API.md)).
2. **Truyện Trung** người đăng nhập từ link mục lục hoặc file `.txt`; máy chủ tự tải, lập bảng tên riêng Hán Việt rồi dịch sang tiếng Việt bằng AI (Gemini hoặc Claude) và đăng từng chương.
3. **Thành viên tự đăng** truyện sáng tác (soạn từng chương hoặc tải file `.txt` tự tách chương).

Bạn đọc có thể theo dõi, đánh giá, bình luận, lưu lịch sử đọc và **ủng hộ** tác giả/trang qua mã VietQR, MoMo, PayPal/Ko-fi.

> **Không có cổng thanh toán.** Trang không thu tiền và không giữ tiền: mã QR (do img.vietqr.io tạo) chuyển thẳng vào tài khoản người nhận. Nút "Tôi đã ủng hộ" chỉ ghi lại báo cáo để tác giả/quản trị **tự kiểm tra tài khoản rồi xác nhận**. Đã có sẵn bảng `donations` và endpoint `POST /api/donations/webhook` (chưa bật) để sau này nối SePay/Casso tự xác nhận — xem cuối [API.md](API.md).

Công nghệ: Python 3.11+, FastAPI, Jinja2 (render phía máy chủ, tốt cho SEO), SQLite (WAL), CSS/JS viết tay, không cần build.

## Cài đặt & chạy thử trên máy (Windows)

Cách nhanh nhất:

```powershell
cd web
powershell -ExecutionPolicy Bypass -File run.ps1 -Seed    # lần đầu: tạo .venv, cài thư viện, thêm dữ liệu mẫu
powershell -ExecutionPolicy Bypass -File run.ps1          # các lần sau
```

Mở http://localhost:8000. Thêm `-Dev` để tự tải lại khi sửa code, `-Port 9000` để đổi cổng.

Làm tay (Windows/Linux/macOS):

```bash
cd web
python -m venv .venv
.venv\Scripts\activate            # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env            # Linux/macOS: cp .env.example .env  rồi sửa
python seed.py                    # (tuỳ chọn) dữ liệu mẫu
uvicorn app.main:app --reload     # http://127.0.0.1:8000
```

`seed.py` tạo 6 truyện mẫu có bìa vẽ tự động và các tài khoản **chỉ để thử**: `admin / admin123`, `tacgia / tacgia123`, `bandoc / bandoc123`. Đừng chạy seed trên máy chủ thật (hoặc đổi mật khẩu/xoá các tài khoản này). `python seed.py --reset` xoá DB rồi tạo lại.

**Người đăng ký đầu tiên trở thành quản trị viên.**

Chạy kiểm thử: `python -m pytest -q` (không gọi mạng, AI được giả lập).

## Biến môi trường

Đặt trong `web/.env` (mẫu: [.env.example](.env.example)) hoặc biến môi trường hệ thống (được ưu tiên).

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `SECRET_KEY` | tự tạo, lưu `data/secret_key.txt` | khoá ký cookie đăng nhập |
| `DATA_DIR` | `web/data` | chứa `site.db`, `covers/`, `avatars/` |
| `SITE_NAME` | `Truyện Hay` | tên trang |
| `SITE_TAGLINE` | … | khẩu hiệu, mô tả mặc định |
| `SITE_URL` | (theo yêu cầu) | địa chỉ công khai, vd `https://truyenhay.vn`; dùng cho link tuyệt đối trong API, sitemap, canonical |
| `COOKIE_SECURE` | `0` | đặt `1` khi chạy sau HTTPS |
| `TRANSLATOR` | `gemini` | AI dịch mặc định: `gemini` \| `claude` (\| `mock` để thử giao diện, không gọi AI) |
| `GEMINI_API_KEY` | | key Google AI Studio |
| `GEMINI_MODELS` | `gemini-flash-latest,gemini-flash-lite-latest` | danh sách model, hết lượt/quá tải thì chuyển model sau |
| `CLAUDE_API_KEY` | | key Anthropic (platform.claude.com) |
| `CLAUDE_MODEL` | `claude-sonnet-5-5` | model Claude dùng dịch |
| `WORKER` | `1` | `1`: luồng dịch chạy nền trong tiến trình web; `0`: tắt, chạy riêng `python -m app.worker` |
| `FETCH_DELAY` | `1.5` | giây nghỉ giữa hai lần tải trang nguồn |
| `SITE_DONATE_BANK` / `_ACCOUNT` / `_NAME` / `_MOMO` / `_LINK` | | thông tin ủng hộ trang (mã BIN ngân hàng, số TK, chủ TK, MoMo, link PayPal/Ko-fi); sửa được trong **Quản trị → Cài đặt** |
| `DONATE_WEBHOOK_SECRET` | | khoá webhook SePay/Casso; trống = tắt |

## Các chức năng chính

- **Trang chủ**: truyện nổi bật (quản trị chọn), mới cập nhật, đọc nhiều (tuần/mọi lúc), hoàn thành, truyện mới, thể loại.
- **Danh sách/tìm kiếm** `/danh-sach`: lọc thể loại, tình trạng, nguồn (sáng tác/dịch/AI), sắp xếp; tìm không dấu ("kiem dao" ra "Kiếm Đạo", "tien hiep" ra truyện Tiên hiệp).
- **Trang truyện** `/truyen/{slug}`: thông tin, đọc từ đầu/đọc tiếp, mục lục 100 chương/trang (cũ/mới), theo dõi, chấm sao, ủng hộ, bảng vinh danh, bình luận, RSS.
- **Trang đọc** `/truyen/{slug}/chuong-{n}`: chỉnh cỡ chữ, giãn dòng, phông có chân/không chân, độ rộng, nền sáng/giấy ngà/tối (lưu trên trình duyệt), phím ← →, thanh tiến độ, ô chọn chương, bình luận theo chương. Lượt xem tính 1 lần/người/chương/ngày, bỏ qua bot.
- **Tủ truyện** `/tu-truyen`: truyện theo dõi (đánh dấu số chương mới), lịch sử đọc. Chưa đăng nhập thì lịch sử lưu trên trình duyệt.
- **Đăng truyện** `/dang-truyen`: tạo/sửa truyện (bìa tự cắt 400×600 webp), viết chương, nháp, hẹn giờ đăng, tải file `.txt` tự tách chương theo "Chương 12: …", "Chương 12 - …", "第12章", đăng/nháp/xoá hàng loạt, đánh lại số chương, xác nhận ủng hộ.
- **Tài khoản** `/tai-khoan`: hồ sơ, ảnh đại diện, thông tin nhận ủng hộ (ngân hàng VietQR, MoMo, PayPal, Ko-fi), **API key cho app**, đổi mật khẩu.
- **Nhập truyện Trung** `/nhap-truyen` (quản trị và thành viên được cấp vai trò "Người đăng"), xem phần dưới.
- **Quản trị** `/quan-tri`: thống kê, thành viên (vai trò, khoá), truyện (nổi bật, ẩn, xoá), việc dịch, ủng hộ trang, cài đặt.
- **SEO**: `sitemap.xml` (chỉ mục + sitemap từng truyện), `robots.txt`, canonical, Open Graph, JSON-LD, RSS từng truyện, nén gzip, cache file tĩnh.

## Nhập & dịch truyện Trung

1. Vào **Nhập truyện Trung**, dán link trang **mục lục** hoặc chọn file `.txt` (tự nhận UTF-8/GBK/GB18030/Big5), chọn AI và số chương dịch trước (mặc định 10). Phải tích xác nhận có quyền dịch/đăng lại.
2. Luồng nền lần lượt: tải mục lục (tự lật trang mục lục có "下一页") → dịch tên truyện/tác giả/giới thiệu/thể loại → **lập bảng thuật ngữ** (tên nhân vật, địa danh, môn phái, công pháp, cảnh giới… theo âm Hán Việt) từ 3 chương đầu → dịch từng chương, **đăng ngay khi xong**.
3. Mỗi chương gửi kèm bảng thuật ngữ, 3 đoạn cuối bản dịch chương trước và tóm tắt cốt truyện đang có để giữ mạch và xưng hô; AI trả thêm thuật ngữ mới và tóm tắt cập nhật.
4. Trang việc dịch hiện tiến độ (tự cập nhật), lỗi từng chương, chi phí đã tốn và **ước tính chi phí Claude** cho 10/50/100 chương; có nút Tạm dừng / Chạy tiếp / Dịch tiếp N chương / Thử lại chương lỗi / Dịch lại một chương / Lấy chương mới từ nguồn / Đổi AI.
5. **Bảng thuật ngữ** sửa được; mục sửa tay được khoá, AI không ghi đè.

Bộ bóc trang ở `app/importer/extract.py`: có `SITE_RULES` (bộ chọn CSS theo tên miền) để thêm luật riêng cho từng trang, không khớp thì dùng cách đoán chung (khối link 第N章 nhiều nhất; khối chứa nhiều chữ Hán nhất; lọc dòng quảng cáo; gộp "下一页" cùng chương).

Claude dùng Messages API (stream, structured output JSON) với `cache_control` trên phần system + bảng thuật ngữ (phần cố định giữa các chương) để giảm giá; bảng thuật ngữ thay đổi thì cache làm lại. Gemini dùng `google-genai`, tự đợi khi bị giới hạn tốc độ (429) và chuyển model dự phòng khi hết lượt ngày/quá tải.

**Worker**: mặc định chạy thành luồng nền trong tiến trình web — chỉ chạy uvicorn **1 worker** (`--workers 1`). Muốn tách: đặt `WORKER=0` cho web và chạy `python -m app.worker` (một tiến trình duy nhất). Toàn bộ trạng thái nằm trong DB (`jobs`, `source_chapters`), tắt/mở lại lúc nào cũng tiếp tục được: việc đang chạy dở về hàng đợi, chương đang dịch dở được dịch lại.

## Kết nối app Video Dub

1. Đăng ký tài khoản trên trang (nên nhờ quản trị cấp vai trò "Người đăng" nếu cần nhập truyện Trung).
2. Vào **Tài khoản → API cho app**, bấm **Chép key**.
3. Trong app máy tính, dán địa chỉ trang (`SITE_URL`) và API key. App gọi `GET /api/me` để kiểm tra, rồi `POST /api/stories`, `PUT /api/stories/{id}/chapters/{n}`… Chi tiết: [API.md](API.md).

## Chạy thật trên VPS (Ubuntu + systemd + nginx)

```bash
sudo adduser --system --group truyen
sudo mkdir -p /srv/truyen && sudo chown truyen: /srv/truyen
# chép thư mục web/ vào /srv/truyen/web, rồi:
cd /srv/truyen/web
python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # đặt SECRET_KEY (vd: python3 -c "import secrets;print(secrets.token_urlsafe(48))"),
                       # SITE_URL=https://ten-mien, COOKIE_SECURE=1, các API key
```

`/etc/systemd/system/truyen.service`:

```ini
[Unit]
Description=Trang doc truyen
After=network.target

[Service]
User=truyen
WorkingDirectory=/srv/truyen/web
ExecStart=/srv/truyen/web/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --proxy-headers --forwarded-allow-ips=127.0.0.1
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now truyen
```

nginx (`/etc/nginx/sites-available/truyen`), sau đó `certbot --nginx -d ten-mien`:

```nginx
server {
    server_name ten-mien;
    client_max_body_size 60m;          # upload file .txt / ảnh bìa
    location /static/ { alias /srv/truyen/web/static/; expires 7d; }
    location /media/covers/ { alias /srv/truyen/web/data/covers/; expires 30d; }
    location /media/avatars/ { alias /srv/truyen/web/data/avatars/; expires 30d; }
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## Chạy từ máy nhà bằng Cloudflare Tunnel

Không cần mở cổng router, không cần IP tĩnh:

1. Chạy trang: `run.ps1` (hoặc cài làm dịch vụ bằng NSSM/Task Scheduler cho tự chạy khi bật máy).
2. Cài `cloudflared`, đăng nhập: `cloudflared tunnel login` (tên miền phải quản lý trên Cloudflare).
3. `cloudflared tunnel create truyen` → `cloudflared tunnel route dns truyen ten-mien`.
4. Tạo `%USERPROFILE%\.cloudflared\config.yml`:

   ```yaml
   tunnel: truyen
   credentials-file: C:\Users\<ban>\.cloudflared\<id>.json
   ingress:
     - hostname: ten-mien
       service: http://localhost:8000
     - service: http_status:404
   ```

5. `cloudflared tunnel run truyen` (hoặc `cloudflared service install` để chạy nền). Đặt `SITE_URL=https://ten-mien`, `COOKIE_SECURE=1`. IP thật của bạn đọc lấy từ header `CF-Connecting-IP`.

## Sao lưu

Mọi dữ liệu nằm trong `DATA_DIR` (mặc định `web/data`): `site.db` + thư mục `covers/`, `avatars/`, `secret_key.txt`.

- Sao lưu DB an toàn khi trang đang chạy (WAL): `sqlite3 data/site.db ".backup data/backup-$(date +%F).db"` hoặc trong Python: `sqlite3.connect("data/site.db").backup(sqlite3.connect("backup.db"))`.
- Chép thêm `covers/`, `avatars/`. Ví dụ cron hằng ngày: nén cả thư mục backup rồi đẩy lên nơi khác (rclone, Google Drive…).
- Không chép riêng `site.db` khi đang chạy mà thiếu `site.db-wal` — dùng `.backup` như trên.

## Giới hạn đã biết

- Bộ bóc truyện Trung là đoán chung: hợp với các trang kiểu "biquge"/mục lục tĩnh. Sẽ **không** chạy với trang tải nội dung bằng JavaScript/API (nhiều trang mobile mới), trang chặn bot/Cloudflare, trang mã hoá chữ bằng font riêng hoặc trộn ký tự, trang bắt đăng nhập/VIP (起点, 晋江 chương VIP…), mục lục phân trang bằng `<select>`/AJAX. Khi đó dùng file `.txt` hoặc thêm luật vào `SITE_RULES`.
- Chất lượng bản dịch phụ thuộc AI; nên đọc lại vài chương đầu và chỉnh bảng thuật ngữ.
- Lịch sử đọc/cài đặt đọc của khách chưa đăng nhập chỉ lưu trên trình duyệt.
- Không gửi email: quên mật khẩu thì quản trị bấm **Đặt lại mật khẩu** trong Quản trị → Thành viên rồi báo mật khẩu tạm.
- SQLite phù hợp tới vài trăm nghìn lượt đọc/ngày trên một máy; chỉ chạy một tiến trình ghi (1 uvicorn worker + luồng dịch).
