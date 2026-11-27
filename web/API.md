# API cho ứng dụng Video Dub

Ứng dụng máy tính dùng API này để đẩy truyện AI viết lên trang web.

- Địa chỉ gốc: `SITE_URL` của trang (ví dụ `https://truyenhay.vn`).
- Xác thực: header `X-Api-Key: <API key>`. Mỗi thành viên lấy key ở **Tài khoản → API cho app** (`/tai-khoan?tab=api`), có thể tạo key mới bất cứ lúc nào (key cũ hết hiệu lực).
- Thân yêu cầu và phản hồi là JSON UTF-8 (trừ upload ảnh bìa: `multipart/form-data`).
- Lỗi luôn trả `{"detail": "<thông báo tiếng Việt>"}` với mã trạng thái phù hợp:
  `401` thiếu/sai key · `403` không phải chủ truyện (hoặc tài khoản bị khoá) · `404` không có truyện · `413` gửi quá nhiều chương · `422` dữ liệu sai · `400` ảnh lỗi.
- Chỉ chủ truyện (hoặc quản trị viên) được sửa truyện.

## `GET /api/me`

Kiểm tra key.

```json
{"id": 3, "username": "mavan", "role": "uploader"}
```

`role`: `reader` | `uploader` | `admin`.

## `POST /api/stories`

Tạo hoặc cập nhật truyện theo cặp (chủ key, `source_id`). Gọi lại với cùng `source_id` sẽ cập nhật truyện cũ (giữ nguyên `slug`).

```json
{
  "source_id": "du-an-123",
  "title": "Tiên Lộ Mênh Mông",
  "description": "Giới thiệu…",
  "genres": ["Tiên hiệp", "Huyền huyễn"],
  "status": "ongoing",
  "origin": "ai",
  "author_name": "Bút Danh"
}
```

| Trường | Bắt buộc | Ghi chú |
|---|---|---|
| `source_id` | có | chuỗi 1–200 ký tự, mã dự án bên app |
| `title` | có | 1–200 ký tự |
| `description` | không | mặc định `""` |
| `genres` | không | tên thể loại; tên chưa có sẽ được tạo mới (tối đa 20) |
| `status` | không | `ongoing` (mặc định) hoặc `completed` |
| `origin` | không | `ai` (mặc định), `original`, `translated` |
| `author_name` | không | tên tác giả hiển thị; mặc định là tên hiển thị của chủ key |

Phản hồi `200`:

```json
{"id": 12, "slug": "tien-lo-menh-mong", "url": "https://truyenhay.vn/truyen/tien-lo-menh-mong"}
```

`url` là địa chỉ tuyệt đối, dùng `SITE_URL` nếu có, không thì lấy theo địa chỉ yêu cầu.

## `POST /api/stories/{id}/cover`

`multipart/form-data`, trường `file` là ảnh (jpg/png/webp…, tối đa 8 MB). Ảnh được cắt về 400×600 và lưu dạng webp.

```json
{"ok": true, "url": "https://truyenhay.vn/media/covers/12-a1b2c3d4.webp"}
```

## `PUT /api/stories/{id}/chapters/{n}`

Thêm hoặc sửa chương số `n` (1–100000).

```json
{"title": "Thiếu niên rời núi", "content": "Đoạn 1…\n\nĐoạn 2…", "publish": true}
```

- `content` bắt buộc (tối đa 200.000 ký tự). Đoạn văn cách nhau bằng một dòng trống; nếu không có dòng trống nào thì mỗi dòng là một đoạn.
- `title` không kèm chữ "Chương n" (trang tự thêm).
- `publish` mặc định `true`; `false` = lưu nháp (không hiện với bạn đọc).
- Sửa chương đã đăng không làm truyện nhảy lên mục "Mới cập nhật".

```json
{"ok": true, "url": "https://truyenhay.vn/truyen/tien-lo-menh-mong/chuong-5"}
```

## `PUT /api/stories/{id}/chapters`

Gửi nhiều chương một lần, **tối đa 200** chương mỗi lần (quá thì trả `413`).

```json
{"chapters": [{"n": 1, "title": "…", "content": "…"}, {"n": 2, "title": "…", "content": "…"}]}
```

Mỗi phần tử có thể kèm `"publish": false`. Phản hồi:

```json
{"ok": true, "count": 2}
```

## `GET /api/stories/{id}`

```json
{
  "id": 12, "slug": "tien-lo-menh-mong", "url": "https://truyenhay.vn/truyen/tien-lo-menh-mong",
  "title": "Tiên Lộ Mênh Mông", "chapter_count": 48, "last_chapter": 50, "status": "ongoing"
}
```

- `chapter_count`: số chương đang hiện với bạn đọc (đã đăng, đã tới giờ hẹn).
- `last_chapter`: số chương lớn nhất đã có trên trang, **kể cả nháp/hẹn giờ** — app dùng để biết đã đẩy tới đâu.

## Ví dụ (PowerShell / curl)

```bash
curl -H "X-Api-Key: vd_xxx" https://truyenhay.vn/api/me
curl -X POST -H "X-Api-Key: vd_xxx" -H "Content-Type: application/json" \
     -d '{"source_id":"p1","title":"Truyện thử"}' https://truyenhay.vn/api/stories
curl -X PUT -H "X-Api-Key: vd_xxx" -H "Content-Type: application/json" \
     -d '{"title":"Mở đầu","content":"Xin chào."}' https://truyenhay.vn/api/stories/12/chapters/1
curl -X POST -H "X-Api-Key: vd_xxx" -F "file=@cover.jpg" https://truyenhay.vn/api/stories/12/cover
```

Python (gói `requests`, đã có trong engine):

```python
import requests
S = requests.Session()
S.headers["X-Api-Key"] = api_key
st = S.post(f"{base}/api/stories", json={"source_id": project_id, "title": title, "genres": genres}).json()
S.put(f"{base}/api/stories/{st['id']}/chapters/{n}", json={"title": t, "content": text}).raise_for_status()
```

---

## Webhook ngân hàng (chưa bật mặc định)

`POST /api/donations/webhook` — móc nối để sau này tự xác nhận ủng hộ qua dịch vụ báo biến động số dư (SePay, Casso).
Cần đặt `DONATE_WEBHOOK_SECRET`; nếu chưa đặt, endpoint trả `503`.

- SePay: cấu hình webhook gửi header `Authorization: Apikey <DONATE_WEBHOOK_SECRET>`; body dạng
  `{"id": 123, "content": "UH 12 mavan", "transferType": "in", "transferAmount": 50000, …}`.
- Casso: header `Secure-Token: <DONATE_WEBHOOK_SECRET>`; body `{"data": [{"id": 1, "description": "UH 12 mavan", "amount": 50000}]}`.

Nội dung chuyển khoản theo mẫu `UH {story_id} {username}` (mã QR trên trang điền sẵn). Webhook sẽ xác nhận báo cáo
"Tôi đã ủng hộ" khớp số tiền, hoặc tạo khoản đã xác nhận mới. Mỗi giao dịch chỉ ghi một lần (theo `id`).
Lưu ý: webhook chỉ thấy tiền vào **tài khoản gắn với SePay/Casso** (thường là tài khoản của trang); ủng hộ chuyển thẳng
vào tài khoản riêng của tác giả vẫn do tác giả tự xác nhận trong mục **Đăng truyện → Ủng hộ**.
