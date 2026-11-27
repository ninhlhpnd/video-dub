"""JSON API cho app máy tính (X-Api-Key)."""
import io

from PIL import Image

from app.auth import create_user
from app.db import val


def _key(username, role=None):
    u = create_user(username, "matkhau123", username.title(), role=role)
    return u["api_key"]


def test_me_and_auth_errors(client):
    key = _key("writer")
    r = client.get("/api/me", headers={"X-Api-Key": key})
    assert r.status_code == 200
    assert r.json() == {"id": 1, "username": "writer", "role": "admin"}  # người đầu tiên là admin
    r = client.get("/api/me")
    assert r.status_code == 401 and r.json() == {"detail": "Thiếu header X-Api-Key"}
    assert client.get("/api/me", headers={"X-Api-Key": "sai"}).status_code == 401


def test_story_and_chapter_flow(client):
    _key("admin0")  # chiếm vị trí admin
    key = _key("aiwriter")
    h = {"X-Api-Key": key}
    body = {"source_id": "proj-123", "title": "Tiên Lộ Mênh Mông", "description": "Giới thiệu",
            "genres": ["Tiên hiệp", "Thể loại Mới"], "status": "ongoing", "origin": "ai", "author_name": "Bút AI"}
    r = client.post("/api/stories", json=body, headers=h)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["slug"] == "tien-lo-menh-mong"
    assert d["url"] == "http://testserver/truyen/tien-lo-menh-mong"
    sid = d["id"]
    # upsert theo (chủ, source_id): gọi lại không tạo truyện mới
    body["title"] = "Tiên Lộ Mênh Mông (bản sửa)"
    body["status"] = "completed"
    assert client.post("/api/stories", json=body, headers=h).json()["id"] == sid
    assert val("SELECT COUNT(*) FROM stories") == 1
    assert val("SELECT COUNT(*) FROM genres WHERE name='Thể loại Mới'") == 1

    r = client.put(f"/api/stories/{sid}/chapters/1", json={"title": "Khởi đầu", "content": "Đoạn 1.\n\nĐoạn 2."}, headers=h)
    assert r.status_code == 200
    assert r.json() == {"ok": True, "url": "http://testserver/truyen/tien-lo-menh-mong/chuong-1"}
    r = client.put(f"/api/stories/{sid}/chapters/2", json={"title": "Nháp", "content": "Chưa đăng", "publish": False}, headers=h)
    assert r.status_code == 200
    r = client.put(f"/api/stories/{sid}/chapters", headers=h,
                   json={"chapters": [{"n": i, "title": f"C{i}", "content": f"Nội dung {i}"} for i in range(3, 8)]})
    assert r.json() == {"ok": True, "count": 5}
    r = client.get(f"/api/stories/{sid}", headers=h)
    assert r.json() == {"id": sid, "slug": "tien-lo-menh-mong", "url": "http://testserver/truyen/tien-lo-menh-mong",
                        "title": "Tiên Lộ Mênh Mông (bản sửa)", "chapter_count": 6, "last_chapter": 7, "status": "completed"}
    # trang đọc hiển thị chương vừa đẩy lên
    page = client.get("/truyen/tien-lo-menh-mong/chuong-1")
    assert page.status_code == 200 and "<p>Đoạn 2.</p>" in page.text
    assert client.get("/truyen/tien-lo-menh-mong/chuong-2").status_code == 404  # bản nháp

    # ảnh bìa
    buf = io.BytesIO()
    Image.new("RGB", (800, 1000), (200, 50, 50)).save(buf, "PNG")
    r = client.post(f"/api/stories/{sid}/cover", files={"file": ("bia.png", buf.getvalue(), "image/png")}, headers=h)
    assert r.status_code == 200 and r.json()["ok"] is True
    assert r.json()["url"].startswith("http://testserver/media/covers/")
    bad = client.post(f"/api/stories/{sid}/cover", files={"file": ("x.png", b"not an image", "image/png")}, headers=h)
    assert bad.status_code == 400 and "detail" in bad.json()


def test_permissions_and_validation(client):
    admin_key = _key("boss")
    k1, k2 = _key("owner1"), _key("other2")
    sid = client.post("/api/stories", json={"source_id": "a", "title": "Truyện A"}, headers={"X-Api-Key": k1}).json()["id"]
    r = client.put(f"/api/stories/{sid}/chapters/1", json={"content": "x"}, headers={"X-Api-Key": k2})
    assert r.status_code == 403 and r.json()["detail"]
    assert client.get(f"/api/stories/{sid}", headers={"X-Api-Key": k2}).status_code == 403
    # admin sửa được
    assert client.put(f"/api/stories/{sid}/chapters/1", json={"content": "x"}, headers={"X-Api-Key": admin_key}).status_code == 200
    assert client.get("/api/stories/9999", headers={"X-Api-Key": k1}).status_code == 404
    r = client.post("/api/stories", json={"source_id": "b", "title": "B", "status": "sai"}, headers={"X-Api-Key": k1})
    assert r.status_code == 422 and isinstance(r.json()["detail"], str)
    r = client.put(f"/api/stories/{sid}/chapters", headers={"X-Api-Key": k1},
                   json={"chapters": [{"n": i, "content": "x"} for i in range(1, 202)]})
    assert r.status_code == 413
    # hai người dùng cùng source_id là hai truyện khác nhau
    sid2 = client.post("/api/stories", json={"source_id": "a", "title": "Truyện A"}, headers={"X-Api-Key": k2}).json()
    assert sid2["id"] != sid and sid2["slug"] == "truyen-a-2"
