"""Khu vực tác giả "Đăng truyện": tạo/sửa truyện, chương, tải file .txt, xác nhận ủng hộ."""
from fastapi import APIRouter, HTTPException, Request

from ..auth import check_csrf, require_user
from ..covers import remove_file, save_cover
from ..db import ex, now, q, q1, tx, val
from ..importer.text import decode_bytes, split_chapters
from ..services import (all_genres, can_edit, create_story, delete_chapter, delete_story, refresh_story_stats,
                        renumber_chapters, set_story_genres, story_genres, update_story, upsert_chapter)
from ..templating import redirect, render
from ..utils import Page, parse_dt_local, to_int

router = APIRouter(prefix="/dang-truyen")

MAX_TXT = 30 * 1024 * 1024


def _own_story(user, story_id):
    s = q1("SELECT * FROM stories WHERE id=?", (story_id,))
    if not s:
        raise HTTPException(404, "Không tìm thấy truyện")
    if not can_edit(user, s):
        raise HTTPException(403, "Bạn không phải chủ truyện này")
    return s


def _story_form_values(form):
    title = str(form.get("title", "")).strip()[:200]
    status = form.get("status") if form.get("status") in ("ongoing", "completed") else "ongoing"
    origin = form.get("origin") if form.get("origin") in ("original", "translated", "ai") else "original"
    gids = [to_int(g) for g in form.getlist("genres")][:8]
    valid = {g["id"] for g in all_genres()}
    return dict(
        title=title, description=str(form.get("description", "")).strip()[:10000], status=status, origin=origin,
        author_name=str(form.get("author_name", "")).strip()[:100],
        original_title=str(form.get("original_title", "")).strip()[:200],
        original_author=str(form.get("original_author", "")).strip()[:100],
    ), [g for g in gids if g in valid]


@router.get("")
def my_stories(request: Request):
    user = require_user(request)
    stories = q("SELECT s.*, (SELECT COUNT(*) FROM chapters c WHERE c.story_id=s.id) AS all_chapters, "
                "(SELECT COUNT(*) FROM donations d WHERE d.story_id=s.id AND d.status='pending') AS pending_donations "
                "FROM stories s WHERE s.owner_id=? ORDER BY s.updated_at DESC", (user["id"],))
    pending = val("SELECT COUNT(*) FROM donations WHERE to_user_id=? AND status='pending'", (user["id"],), default=0)
    return render(request, "author_list.html", stories=stories, pending=pending)


@router.get("/moi")
def new_story_form(request: Request):
    require_user(request)
    return render(request, "author_story_form.html", s=None, genres=all_genres(), selected=set())


@router.post("/moi")
async def new_story(request: Request):
    user = require_user(request)
    form = await request.form()
    check_csrf(request, form)
    vals, gids = _story_form_values(form)
    if not vals["title"]:
        return render(request, "author_story_form.html", status=400, s=None, genres=all_genres(), selected=set(gids),
                      error="Hãy nhập tên truyện", form=vals)
    vals["author_name"] = vals["author_name"] or user["display_name"]
    s = create_story(user["id"], vals.pop("title"), genre_ids=gids, **vals)
    cover = form.get("cover")
    if cover is not None and getattr(cover, "filename", ""):
        try:
            update_story(s["id"], cover=save_cover(await cover.read(), s["id"]))
        except ValueError as e:
            return redirect(f"/dang-truyen/{s['id']}", f"Đã tạo truyện nhưng ảnh bìa lỗi: {e}")
    return redirect(f"/dang-truyen/{s['id']}", "Đã tạo truyện. Hãy thêm chương đầu tiên!")


@router.get("/{story_id:int}")
def manage(request: Request, story_id: int):
    user = require_user(request)
    s = _own_story(user, story_id)
    total = val("SELECT COUNT(*) FROM chapters WHERE story_id=?", (s["id"],), default=0)
    pg = Page(to_int(request.query_params.get("trang"), 1, 1), 100, total)
    chapters = q("SELECT id, n, title, words, is_published, publish_at, views, updated_at FROM chapters WHERE story_id=? "
                 "ORDER BY n DESC LIMIT ? OFFSET ?", (s["id"], pg.per_page, pg.offset))
    next_n = val("SELECT MAX(n) FROM chapters WHERE story_id=?", (s["id"],), default=0) + 1
    job = q1("SELECT * FROM jobs WHERE story_id=? ORDER BY id DESC LIMIT 1", (s["id"],))
    return render(request, "author_story_manage.html", s=s, chapters=chapters, page=pg, total=total, next_n=next_n,
                  genres=all_genres(), selected={g["id"] for g in story_genres(s["id"])}, now=now(), job=job)


@router.post("/{story_id:int}")
async def save_story(request: Request, story_id: int):
    user = require_user(request)
    s = _own_story(user, story_id)
    form = await request.form()
    check_csrf(request, form)
    vals, gids = _story_form_values(form)
    if not vals["title"]:
        return redirect(f"/dang-truyen/{s['id']}", "Tên truyện không được để trống")
    with tx():
        update_story(s["id"], **vals)
        set_story_genres(s["id"], gids)
    cover = form.get("cover")
    if cover is not None and getattr(cover, "filename", ""):
        try:
            name = save_cover(await cover.read(), s["id"])
            update_story(s["id"], cover=name)
            remove_file("covers", s["cover"])
        except ValueError as e:
            return redirect(f"/dang-truyen/{s['id']}", f"Ảnh bìa lỗi: {e}")
    return redirect(f"/dang-truyen/{s['id']}", "Đã lưu thông tin truyện")


@router.post("/{story_id:int}/xoa")
async def remove_story(request: Request, story_id: int):
    user = require_user(request)
    s = _own_story(user, story_id)
    form = await request.form()
    check_csrf(request, form)
    if form.get("confirm") != s["slug"]:
        return redirect(f"/dang-truyen/{s['id']}", "Nhập đúng mã truyện để xác nhận xoá")
    delete_story(s["id"])
    return redirect("/dang-truyen", f"Đã xoá truyện “{s['title']}”")


@router.post("/{story_id:int}/danh-so-lai")
async def renumber(request: Request, story_id: int):
    user = require_user(request)
    s = _own_story(user, story_id)
    form = await request.form()
    check_csrf(request, form)
    renumber_chapters(s["id"])
    return redirect(f"/dang-truyen/{s['id']}", "Đã đánh lại số chương liên tục từ 1")


# ---------------------------------------------------------------- chương

@router.get("/{story_id:int}/chuong/moi")
def new_chapter_form(request: Request, story_id: int):
    user = require_user(request)
    s = _own_story(user, story_id)
    n = val("SELECT MAX(n) FROM chapters WHERE story_id=?", (s["id"],), default=0) + 1
    return render(request, "author_chapter_form.html", s=s, c=None, n=n)


@router.get("/{story_id:int}/chuong/{n:int}")
def edit_chapter_form(request: Request, story_id: int, n: int):
    user = require_user(request)
    s = _own_story(user, story_id)
    c = q1("SELECT * FROM chapters WHERE story_id=? AND n=?", (s["id"], n))
    if not c:
        raise HTTPException(404, "Không có chương này")
    return render(request, "author_chapter_form.html", s=s, c=c, n=n, now=now())


@router.post("/{story_id:int}/chuong")
async def save_chapter(request: Request, story_id: int):
    user = require_user(request)
    s = _own_story(user, story_id)
    form = await request.form(max_part_size=40 * 1024 * 1024)
    check_csrf(request, form)
    n = to_int(form.get("n"), 0)
    old_n = to_int(form.get("old_n"), 0)
    title = str(form.get("title", "")).strip()
    content = str(form.get("content", ""))
    mode = form.get("mode", "publish")
    if n < 1 or n > 100000:
        return redirect(request.headers.get("referer") or f"/dang-truyen/{s['id']}", "Số chương không hợp lệ")
    if len(content.strip()) < 10:
        return render(request, "author_chapter_form.html", status=400, s=s, c=None, n=n, error="Nội dung chương quá ngắn",
                      form={"title": title, "content": content, "n": n, "old_n": old_n})
    if old_n != n and q1("SELECT 1 FROM chapters WHERE story_id=? AND n=?", (s["id"], n)):
        return render(request, "author_chapter_form.html", status=400, s=s, c=None, n=n,
                      error=f"Đã có chương {n}. Hãy chọn số khác.",
                      form={"title": title, "content": content, "n": n, "old_n": old_n})
    publish_at = None
    if mode == "schedule":
        publish_at = parse_dt_local(form.get("publish_at"))
        if not publish_at:
            return render(request, "author_chapter_form.html", status=400, s=s, c=None, n=n,
                          error="Hãy chọn thời gian hẹn đăng", form={"title": title, "content": content, "n": n, "old_n": old_n})
    with tx():
        if old_n and old_n != n:
            ex("UPDATE chapters SET n=? WHERE story_id=? AND n=?", (n, s["id"], old_n))
        upsert_chapter(s["id"], n, title, content, publish=mode in ("publish", "schedule"), publish_at=publish_at)
    msg = {"publish": "Đã đăng chương", "draft": "Đã lưu bản nháp", "schedule": "Đã hẹn giờ đăng"}.get(mode, "Đã lưu")
    if form.get("next") == "new":
        return redirect(f"/dang-truyen/{s['id']}/chuong/moi", f"{msg} {n}. Viết tiếp chương sau nhé!")
    return redirect(f"/dang-truyen/{s['id']}", f"{msg} {n}")


@router.post("/{story_id:int}/chuong/{n:int}/xoa")
async def remove_chapter(request: Request, story_id: int, n: int):
    user = require_user(request)
    s = _own_story(user, story_id)
    form = await request.form()
    check_csrf(request, form)
    with tx():
        delete_chapter(s["id"], n)
    return redirect(f"/dang-truyen/{s['id']}", f"Đã xoá chương {n}")


@router.post("/{story_id:int}/chuong/hang-loat")
async def bulk_action(request: Request, story_id: int):
    """Đăng / chuyển nháp / xoá nhiều chương đã chọn."""
    user = require_user(request)
    s = _own_story(user, story_id)
    form = await request.form()
    check_csrf(request, form)
    ns = [to_int(x) for x in form.getlist("n") if to_int(x) > 0]
    action = form.get("action")
    if not ns:
        return redirect(f"/dang-truyen/{s['id']}", "Chưa chọn chương nào")
    marks = ",".join("?" * len(ns))
    with tx():
        if action == "publish":
            ex(f"UPDATE chapters SET is_published=1, publish_at=CASE WHEN publish_at>? OR publish_at=0 THEN ? ELSE publish_at END "
               f"WHERE story_id=? AND n IN ({marks}) AND is_published=0", (now(), now(), s["id"], *ns))
        elif action == "draft":
            ex(f"UPDATE chapters SET is_published=0 WHERE story_id=? AND n IN ({marks})", (s["id"], *ns))
        elif action == "delete":
            ex(f"DELETE FROM chapters WHERE story_id=? AND n IN ({marks})", (s["id"], *ns))
        refresh_story_stats(s["id"])
    return redirect(f"/dang-truyen/{s['id']}", f"Đã cập nhật {len(ns)} chương")


@router.get("/{story_id:int}/tai-len")
def upload_form(request: Request, story_id: int):
    user = require_user(request)
    s = _own_story(user, story_id)
    next_n = val("SELECT MAX(n) FROM chapters WHERE story_id=?", (s["id"],), default=0) + 1
    return render(request, "author_upload.html", s=s, next_n=next_n)


@router.post("/{story_id:int}/tai-len")
async def upload_txt(request: Request, story_id: int):
    user = require_user(request)
    s = _own_story(user, story_id)
    form = await request.form(max_part_size=40 * 1024 * 1024)
    check_csrf(request, form)
    f = form.get("file")
    text = str(form.get("text", ""))
    if f is not None and getattr(f, "filename", ""):
        data = await f.read()
        if len(data) > MAX_TXT:
            return redirect(f"/dang-truyen/{s['id']}/tai-len", "File quá lớn (tối đa 30 MB)")
        text = decode_bytes(data)
    if not text.strip():
        return redirect(f"/dang-truyen/{s['id']}/tai-len", "Hãy chọn file .txt hoặc dán nội dung")
    numbering = form.get("numbering") if form.get("numbering") in ("auto", "heading", "seq") else "auto"
    start = to_int(form.get("start"), 1, 1, 100000)
    chapters = split_chapters(text, numbering, start)
    if not chapters:
        return redirect(f"/dang-truyen/{s['id']}/tai-len", "Không tìm thấy nội dung")
    publish = form.get("mode") == "publish"
    overwrite = form.get("overwrite") == "1"
    existing = {r["n"] for r in q("SELECT n FROM chapters WHERE story_id=?", (s["id"],))}
    added, skipped = 0, 0
    with tx():
        for c in chapters[:5000]:
            if c["n"] in existing and not overwrite:
                skipped += 1
                continue
            upsert_chapter(s["id"], c["n"], c["title"], c["content"], publish=publish, refresh=False)
            added += 1
        refresh_story_stats(s["id"])
    msg = f"Đã thêm {added} chương" + (f", bỏ qua {skipped} chương trùng số" if skipped else "") + \
          ("" if publish else " (bản nháp)")
    return redirect(f"/dang-truyen/{s['id']}", msg)


# ---------------------------------------------------------------- xác nhận ủng hộ

@router.get("/ung-ho")
def my_donations(request: Request):
    user = require_user(request)
    rows = q("SELECT d.*, s.title AS story_title, s.slug FROM donations d LEFT JOIN stories s ON s.id=d.story_id "
             "WHERE d.to_user_id=? ORDER BY d.status='pending' DESC, d.created_at DESC LIMIT 300", (user["id"],))
    total = val("SELECT SUM(amount) FROM donations WHERE to_user_id=? AND status='confirmed'", (user["id"],), default=0)
    return render(request, "author_donations.html", rows=rows, total=total, scope="author")


@router.post("/ung-ho/{donation_id:int}")
async def decide_donation(request: Request, donation_id: int):
    user = require_user(request)
    form = await request.form()
    check_csrf(request, form)
    d = q1("SELECT * FROM donations WHERE id=?", (donation_id,))
    if not d:
        raise HTTPException(404, "Không tìm thấy")
    site_donation = d["to_user_id"] is None
    if not ((not site_donation and d["to_user_id"] == user["id"]) or user["role"] == "admin"):
        raise HTTPException(403, "Bạn không thể xác nhận khoản này")
    action = form.get("action")
    if action == "confirm":
        ex("UPDATE donations SET status='confirmed', confirmed_at=? WHERE id=?", (now(), donation_id))
    elif action == "reject":
        ex("UPDATE donations SET status='rejected' WHERE id=?", (donation_id,))
    elif action == "delete":
        ex("DELETE FROM donations WHERE id=?", (donation_id,))
    back = "/quan-tri/ung-ho" if (site_donation or form.get("back") == "admin") else "/dang-truyen/ung-ho"
    return redirect(back, "Đã cập nhật")
