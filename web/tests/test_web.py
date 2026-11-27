"""Kiểm thử luồng web: đăng ký/đăng nhập, trang đọc, bình luận, CSRF, webhook."""
from conftest import csrf_of, register

from app.auth import create_user
from app.db import q1, val
from app.services import create_story, upsert_chapter


def test_register_first_user_is_admin_and_login(client):
    r = register(client, "admin_dau")
    assert r.status_code == 303
    assert q1("SELECT role FROM users WHERE username='admin_dau'")["role"] == "admin"
    assert client.get("/tai-khoan").status_code == 200
    # người thứ hai là bạn đọc
    tok = csrf_of(client)
    client.post("/dang-xuat", data={"csrf": tok})
    r = register(client, "ban_doc")
    assert q1("SELECT role FROM users WHERE username='ban_doc'")["role"] == "reader"
    client.post("/dang-xuat", data={"csrf": csrf_of(client)})
    # sai mật khẩu
    r = client.post("/dang-nhap", data={"csrf": csrf_of(client), "username": "ban_doc", "password": "sai"},
                    follow_redirects=False)
    assert r.status_code == 400
    assert "Sai tên đăng nhập" in r.text
    r = client.post("/dang-nhap", data={"csrf": csrf_of(client), "username": "ban_doc", "password": "matkhau123"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert client.get("/tu-truyen").status_code == 200
    # trang cần đăng nhập chuyển về /dang-nhap khi chưa đăng nhập
    client.post("/dang-xuat", data={"csrf": csrf_of(client)})
    r = client.get("/tai-khoan", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/dang-nhap")


def test_register_validation(client):
    r = register(client, "a b")
    assert r.status_code == 400
    assert "Tên đăng nhập" in r.text


def test_post_without_csrf_rejected(client):
    register(client, "user1")
    r = client.post("/binh-luan", data={"story_id": 1, "body": "xin chào"}, follow_redirects=False)
    assert r.status_code == 403


def _story_with_chapters(n=3):
    u = create_user("tacgia", "matkhau123", "Tác Giả")
    s = create_story(u["id"], "Kiếm Đạo Độc Tôn", description="Mô tả truyện", origin="original", author_name="Mặc Vân")
    for i in range(1, n + 1):
        upsert_chapter(s["id"], i, f"Tên chương {i}", f"Đoạn một của chương {i}.\n\nĐoạn <b>hai</b> & ba.")
    return u, q1("SELECT * FROM stories WHERE id=?", (s["id"],))


def test_reader_page_renders_and_counts_view(client):
    _, s = _story_with_chapters()
    r = client.get(f"/truyen/{s['slug']}/chuong-2", headers={"user-agent": "Mozilla/5.0 test"})
    assert r.status_code == 200
    html = r.text
    assert "<title>Chương 2: Tên chương 2 – Kiếm Đạo Độc Tôn" in html
    assert '<link rel="canonical" href="http://testserver/truyen/kiem-dao-doc-ton/chuong-2">' in html
    assert 'property="og:title"' in html
    assert "<p>Đoạn một của chương 2.</p>" in html
    assert "Đoạn &lt;b&gt;hai&lt;/b&gt; &amp; ba." in html  # nội dung được escape
    assert 'rel="prev"' in html and 'rel="next"' in html
    # lượt xem: một người một chương một ngày chỉ tính 1
    client.get(f"/truyen/{s['slug']}/chuong-2", headers={"user-agent": "Mozilla/5.0 test"})
    assert val("SELECT views FROM chapters WHERE story_id=? AND n=2", (s["id"],)) == 1
    assert val("SELECT views FROM stories WHERE id=?", (s["id"],)) == 1
    # bot không tính
    client.get(f"/truyen/{s['slug']}/chuong-3", headers={"user-agent": "Googlebot/2.1"})
    assert val("SELECT views FROM chapters WHERE story_id=? AND n=3", (s["id"],)) == 0
    assert client.get(f"/truyen/{s['slug']}/chuong-99").status_code == 404


def test_public_pages(client):
    _, s = _story_with_chapters()
    for url in ["/", "/danh-sach", "/danh-sach?q=kiem dao", "/xep-hang", "/hoan-thanh", f"/truyen/{s['slug']}",
                "/dang-nhap", "/dang-ky", "/ung-ho", "/sitemap.xml", f"/sitemap-chuong-{s['id']}.xml", "/robots.txt",
                f"/truyen/{s['slug']}/rss.xml", "/tu-truyen"]:
        r = client.get(url)
        assert r.status_code == 200, url
    assert "Kiếm Đạo Độc Tôn" in client.get("/danh-sach?q=kiem dao").text  # tìm không dấu
    assert "Kiếm Đạo Độc Tôn" in client.get("/danh-sach?q=Mặc Vân").text  # tìm theo tác giả
    assert f"/truyen/{s['slug']}/chuong-3" in client.get(f"/sitemap-chuong-{s['id']}.xml").text


def test_scheduled_and_draft_chapters_hidden(client):
    u, s = _story_with_chapters(2)
    upsert_chapter(s["id"], 3, "Nháp", "Nội dung nháp dài dài", publish=False)
    import time
    upsert_chapter(s["id"], 4, "Hẹn giờ", "Nội dung hẹn giờ", publish=True, publish_at=int(time.time()) + 3600)
    assert val("SELECT chapter_count FROM stories WHERE id=?", (s["id"],)) == 2
    assert client.get(f"/truyen/{s['slug']}/chuong-3").status_code == 404
    assert client.get(f"/truyen/{s['slug']}/chuong-4").status_code == 404


def test_comment_follow_rate(client):
    _, s = _story_with_chapters()
    register(client, "docgia")
    tok = csrf_of(client)
    r = client.post("/binh-luan", data={"csrf": tok, "story_id": s["id"], "body": "<script>alert(1)</script> hay quá",
                                        "next": f"/truyen/{s['slug']}"}, follow_redirects=False)
    assert r.status_code == 303
    html = client.get(f"/truyen/{s['slug']}").text
    assert "&lt;script&gt;alert(1)&lt;/script&gt; hay quá" in html
    # chống spam: gửi tiếp ngay bị chặn
    client.post("/binh-luan", data={"csrf": tok, "story_id": s["id"], "body": "lần hai"})
    assert val("SELECT COUNT(*) FROM comments") == 1
    r = client.post(f"/truyen/{s['id']}/theo-doi", data={"csrf": tok}, headers={"accept": "application/json"})
    assert r.json() == {"following": True, "count": 1}
    r = client.post(f"/truyen/{s['id']}/danh-gia", data={"csrf": tok, "stars": "4"}, headers={"accept": "application/json"})
    assert r.json()["avg"] == 4.0
    client.post(f"/truyen/{s['id']}/danh-gia", data={"csrf": tok, "stars": "2"})
    assert val("SELECT rating_count FROM stories WHERE id=?", (s["id"],)) == 1  # mỗi người một lượt


def test_donation_claim_and_confirm(client):
    u, s = _story_with_chapters(1)
    tok = csrf_of(client)
    r = client.post("/ung-ho/bao-cao", data={"csrf": tok, "story_id": s["id"], "target": "author", "amount": "50000",
                                             "donor_name": "Khách quý", "message": "Cố lên"}, follow_redirects=False)
    assert r.status_code == 303
    d = q1("SELECT * FROM donations")
    assert d["status"] == "pending" and d["to_user_id"] == u["id"]
    client.post("/dang-nhap", data={"csrf": tok, "username": "tacgia", "password": "matkhau123"})
    client.post(f"/dang-truyen/ung-ho/{d['id']}", data={"csrf": tok, "action": "confirm"})
    assert q1("SELECT status FROM donations")["status"] == "confirmed"
    assert "Khách quý" in client.get(f"/truyen/{s['slug']}").text


def test_donation_webhook_requires_secret(client, monkeypatch):
    from app.config import settings
    _, s = _story_with_chapters(1)
    monkeypatch.setattr(settings, "donate_webhook_secret", "bimat")
    body = {"id": 999, "gateway": "VCB", "content": f"UH {s['id']} khach", "transferType": "in", "transferAmount": 20000}
    assert client.post("/api/donations/webhook", json=body).status_code == 401
    r = client.post("/api/donations/webhook", json=body, headers={"Authorization": "Apikey bimat"})
    assert r.json() == {"ok": True, "count": 1}
    # gửi lại cùng giao dịch không tạo trùng
    client.post("/api/donations/webhook", json=body, headers={"Authorization": "Apikey bimat"})
    assert val("SELECT COUNT(*) FROM donations WHERE status='confirmed'") == 1


def test_author_bulk_upload_txt(client):
    register(client, "nguoiviet")
    tok = csrf_of(client)
    r = client.post("/dang-truyen/moi", data={"csrf": tok, "title": "Truyện Thử", "status": "ongoing", "origin": "original"},
                    follow_redirects=False)
    sid = int(r.headers["location"].rsplit("/", 1)[1])
    text = "Chương 1: Mở đầu\n\nNội dung một.\n\nChương 2 - Tiếp theo\n\nNội dung hai.\nVẫn đoạn hai."
    r = client.post(f"/dang-truyen/{sid}/tai-len", data={"csrf": tok, "numbering": "auto", "mode": "publish", "start": "1"},
                    files={"file": ("truyen.txt", text.encode("utf-8"), "text/plain")}, follow_redirects=False)
    assert r.status_code == 303
    from app.db import q
    rows = [dict(x) for x in q("SELECT n, title, content FROM chapters WHERE story_id=? ORDER BY n", (sid,))]
    assert [(x["n"], x["title"]) for x in rows] == [(1, "Mở đầu"), (2, "Tiếp theo")]
    # chương không có dòng trống: mỗi dòng là một đoạn
    assert rows[1]["content"] == "Nội dung hai.\n\nVẫn đoạn hai."
    assert client.get(f"/dang-truyen/{sid}").status_code == 200


def test_logged_in_pages_render(client):
    register(client, "quantri")  # người đầu tiên = admin
    tok = csrf_of(client)
    r = client.post("/dang-truyen/moi", data={"csrf": tok, "title": "Truyện Quản Trị", "origin": "translated",
                                              "genres": ["1", "2"]}, follow_redirects=False)
    sid = int(r.headers["location"].rsplit("/", 1)[1])
    r = client.post(f"/dang-truyen/{sid}/chuong", data={"csrf": tok, "n": "1", "title": "Một", "mode": "publish",
                                                         "content": "Nội dung chương một đủ dài."}, follow_redirects=False)
    assert r.status_code == 303
    r = client.post(f"/dang-truyen/{sid}/chuong", data={"csrf": tok, "n": "2", "title": "Hai", "mode": "schedule",
                                                         "publish_at": "2099-01-01T08:00", "content": "Nội dung hẹn giờ."})
    assert r.status_code == 200
    from app.db import val
    assert val("SELECT chapter_count FROM stories WHERE id=?", (sid,)) == 1
    # nhập truyện Trung từ file .txt (TRANSLATOR=mock trong test)
    txt = "第一章 开始\n内容一。\n第二章 继续\n内容二。".encode("gbk")
    r = client.post("/nhap-truyen", data={"csrf": tok, "rights": "1", "provider": "mock", "first_n": "1"},
                    files={"file": ("剑来.txt", txt, "text/plain")}, follow_redirects=False)
    assert r.status_code == 303, r.text
    job = int(r.headers["location"].rsplit("/", 1)[1])
    assert val("SELECT COUNT(*) FROM source_chapters") == 2
    for action in ("pause", "resume", "more", "retry"):
        assert client.post(f"/nhap-truyen/{job}/hanh-dong", data={"csrf": tok, "action": action, "count": "5"},
                           follow_redirects=False).status_code == 303
    r = client.post(f"/nhap-truyen/{job}/tu-dien", data={"csrf": tok, "new_zh": "陈平安", "new_vi": "Trần Bình An"},
                    follow_redirects=False)
    assert r.status_code == 303 and val("SELECT locked FROM glossary WHERE zh='陈平安'") == 1
    pages = ["/dang-truyen", "/dang-truyen/moi", f"/dang-truyen/{sid}", f"/dang-truyen/{sid}/chuong/moi",
             f"/dang-truyen/{sid}/chuong/1", f"/dang-truyen/{sid}/chuong/2", f"/dang-truyen/{sid}/tai-len", "/dang-truyen/ung-ho",
             "/tai-khoan", "/tai-khoan?tab=ung-ho", "/tai-khoan?tab=api", "/tai-khoan?tab=bao-mat", "/tu-truyen",
             "/tu-truyen?tab=lich-su", "/nhap-truyen", f"/nhap-truyen/{job}", f"/nhap-truyen/{job}/tu-dien",
             f"/nhap-truyen/{job}/trang-thai.json", "/quan-tri", "/quan-tri/thanh-vien", "/quan-tri/truyen",
             "/quan-tri/dich", "/quan-tri/ung-ho", "/quan-tri/cai-dat", "/thanh-vien/quantri",
             "/truyen/truyen-quan-tri/chuong-2"]  # chủ truyện xem được chương hẹn giờ
    for url in pages:
        assert client.get(url).status_code == 200, url
    r = client.post("/tai-khoan", data={"csrf": tok, "action": "donate", "bank_bin": "970436", "bank_account": "0123 456",
                                        "bank_holder": "nguyen van a", "momo": "0900000000"}, follow_redirects=False)
    assert r.status_code == 303
    assert "img.vietqr.io/image/970436-0123456-compact2.png" in client.get("/truyen/truyen-quan-tri").text
    old_key = val("SELECT api_key FROM users WHERE username='quantri'")
    client.post("/tai-khoan", data={"csrf": tok, "action": "apikey"})
    assert val("SELECT api_key FROM users WHERE username='quantri'") != old_key
    # người thường không vào được trang quản trị / nhập truyện
    client.post("/dang-xuat", data={"csrf": tok})
    register(client, "thuong")
    assert client.get("/quan-tri").status_code == 403
    assert client.get("/nhap-truyen").status_code == 403
