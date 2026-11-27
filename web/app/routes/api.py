"""JSON API cho ứng dụng máy tính Video Dub (header X-Api-Key). Xem web/API.md."""
import hmac
import re
from typing import Literal, Optional

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from ..auth import api_user, current_user
from ..config import settings
from ..covers import remove_file, save_cover
from ..db import ex, now, q1, tx, val
from ..services import (base_url, chapter_path, create_story, genre_ids_from_names, refresh_story_stats,
                        set_story_genres, story_path, update_story, upsert_chapter)

router = APIRouter(prefix="/api")

MAX_BULK = 200


class StoryIn(BaseModel):
    source_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=20000)
    genres: list[str] = Field(default_factory=list, max_length=20)
    status: Literal["ongoing", "completed"] = "ongoing"
    origin: Literal["ai", "original", "translated"] = "ai"
    author_name: Optional[str] = Field(default=None, max_length=100)


class ChapterIn(BaseModel):
    title: str = Field(default="", max_length=200)
    content: str = Field(min_length=1, max_length=200_000)
    publish: bool = True


class BulkItem(BaseModel):
    n: int = Field(ge=1, le=100_000)
    title: str = Field(default="", max_length=200)
    content: str = Field(min_length=1, max_length=200_000)
    publish: bool = True


class BulkIn(BaseModel):
    chapters: list[BulkItem]


def _story_for_write(user, story_id):
    s = q1("SELECT * FROM stories WHERE id=?", (story_id,))
    if not s:
        raise HTTPException(404, "Không tìm thấy truyện")
    if user["role"] != "admin" and s["owner_id"] != user["id"]:
        raise HTTPException(403, "Bạn không phải chủ truyện này")
    return s


def _abs(request, path):
    return base_url(request) + path


@router.get("/me")
def me(request: Request):
    u = api_user(request)
    return {"id": u["id"], "username": u["username"], "role": u["role"]}


@router.post("/stories")
def upsert_story(request: Request, body: StoryIn):
    u = api_user(request)
    title = body.title.strip()
    if not title:
        raise HTTPException(422, "Thiếu tên truyện")
    author = (body.author_name or "").strip() or u["display_name"]
    s = q1("SELECT * FROM stories WHERE owner_id=? AND source_id=?", (u["id"], body.source_id))
    with tx():
        gids = genre_ids_from_names(body.genres)
        if s:
            update_story(s["id"], title=title, description=body.description.strip(), status=body.status,
                         origin=body.origin, author_name=author)
            set_story_genres(s["id"], gids)
            sid = s["id"]
        else:
            sid = create_story(u["id"], title, description=body.description.strip(), status=body.status,
                               origin=body.origin, author_name=author, source_id=body.source_id, genre_ids=gids)["id"]
    s = q1("SELECT * FROM stories WHERE id=?", (sid,))
    return {"id": s["id"], "slug": s["slug"], "url": _abs(request, story_path(s))}


@router.post("/stories/{story_id}/cover")
async def upload_cover(request: Request, story_id: int, file: UploadFile = File(...)):
    u = api_user(request)
    s = _story_for_write(u, story_id)
    data = await file.read()
    try:
        name = save_cover(data, s["id"])
    except ValueError as e:
        raise HTTPException(400, str(e))
    update_story(s["id"], cover=name)
    remove_file("covers", s["cover"])
    return {"ok": True, "url": _abs(request, f"/media/covers/{name}")}


@router.put("/stories/{story_id}/chapters/{n}")
def put_chapter(request: Request, story_id: int, n: int, body: ChapterIn):
    u = api_user(request)
    s = _story_for_write(u, story_id)
    if n < 1 or n > 100_000:
        raise HTTPException(422, "Số chương phải từ 1 đến 100000")
    with tx():
        upsert_chapter(s["id"], n, body.title, body.content, publish=body.publish)
    return {"ok": True, "url": _abs(request, chapter_path(s, n))}


@router.put("/stories/{story_id}/chapters")
def put_chapters(request: Request, story_id: int, body: BulkIn):
    u = api_user(request)
    s = _story_for_write(u, story_id)
    if len(body.chapters) > MAX_BULK:
        raise HTTPException(413, f"Tối đa {MAX_BULK} chương mỗi lần gửi")
    with tx():
        for c in body.chapters:
            upsert_chapter(s["id"], c.n, c.title, c.content, publish=c.publish, refresh=False)
        refresh_story_stats(s["id"])
    return {"ok": True, "count": len(body.chapters)}


@router.get("/stories/{story_id}")
def get_story(request: Request, story_id: int):
    u = api_user(request)
    s = _story_for_write(u, story_id)
    last = val("SELECT MAX(n) FROM chapters WHERE story_id=?", (s["id"],), default=0)
    return {"id": s["id"], "slug": s["slug"], "url": _abs(request, story_path(s)), "title": s["title"],
            "chapter_count": s["chapter_count"], "last_chapter": last, "status": s["status"]}


# ---------------------------------------------------------------- dùng nội bộ cho trang đọc

@router.get("/stories/{story_id}/toc")
def toc(story_id: int):
    """Danh sách chương đang hiện (cho ô chọn chương trong trang đọc)."""
    from ..db import q
    s = q1("SELECT id, slug, is_hidden FROM stories WHERE id=?", (story_id,))
    if not s or s["is_hidden"]:
        raise HTTPException(404, "Không tìm thấy truyện")
    rows = q("SELECT n, title FROM chapters c WHERE story_id=? AND is_published=1 AND publish_at<=? ORDER BY n",
             (story_id, now()))
    return {"slug": s["slug"], "chapters": [[r["n"], r["title"]] for r in rows]}


@router.post("/progress")
async def progress(request: Request):
    """Lưu chương đang đọc cho người đã đăng nhập (trang đọc gọi khi cuộn gần hết chương)."""
    from ..services import record_history
    user = current_user(request)
    if not user:
        return {"ok": False}
    data = await request.json()
    sid, n = int(data.get("story_id", 0)), int(data.get("n", 0))
    if sid and n and q1("SELECT 1 FROM stories WHERE id=?", (sid,)):
        record_history(user, sid, n)
    return {"ok": True}


# ---------------------------------------------------------------- webhook ngân hàng (SePay / Casso)

NOTE_RE = re.compile(r"UH\s*(\d+)\s*([A-Za-z0-9_.\-]*)", re.I)


def _webhook_items(data):
    """Chuẩn hoá payload SePay ({id, content, transferAmount, transferType}) và Casso ({data: [{id, description, amount}]})."""
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        for t in data["data"]:
            yield str(t.get("id") or t.get("tid") or ""), str(t.get("description", "")), int(t.get("amount") or 0)
    elif isinstance(data, dict):
        if data.get("transferType", "in") != "in":
            return
        yield str(data.get("id") or data.get("referenceCode") or ""), str(data.get("content") or data.get("description") or ""), \
            int(data.get("transferAmount") or data.get("amount") or 0)


@router.post("/donations/webhook")
async def donation_webhook(request: Request):
    """Móc nối cho webhook biến động số dư (SePay/Casso). Khoá bí mật DONATE_WEBHOOK_SECRET gửi qua header
    `Authorization: Apikey <secret>` (SePay) hoặc `Secure-Token: <secret>` (Casso)."""
    secret = settings.donate_webhook_secret
    if not secret:
        raise HTTPException(503, "Chưa cấu hình DONATE_WEBHOOK_SECRET")
    auth = request.headers.get("authorization", "")
    sent = auth.split(" ", 1)[1].strip() if " " in auth else request.headers.get("secure-token", "")
    if not sent or not hmac.compare_digest(sent, secret):
        raise HTTPException(401, "Sai khoá webhook")
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(400, "Body phải là JSON")
    created = 0
    for ref, content, amount in _webhook_items(data):
        if amount <= 0 or not ref:
            continue
        m = NOTE_RE.search(content)
        story_id = int(m.group(1)) if m else 0
        who = (m.group(2) if m else "") or "Ẩn danh"
        story = q1("SELECT id, owner_id FROM stories WHERE id=?", (story_id,)) if story_id else None
        from_user = q1("SELECT id, display_name FROM users WHERE username=?", (who,))
        with tx():
            # có báo cáo "tôi đã ủng hộ" khớp số tiền thì xác nhận luôn báo cáo đó
            pend = q1("SELECT id FROM donations WHERE status='pending' AND amount=? AND story_id IS ? AND "
                      "(from_user_id IS ? OR from_user_id IS NULL) ORDER BY created_at DESC LIMIT 1",
                      (amount, story["id"] if story else None, from_user["id"] if from_user else None))
            if val("SELECT 1 FROM donations WHERE ref=?", (f"bank:{ref}",)):
                continue
            if pend:
                ex("UPDATE donations SET status='confirmed', source='webhook', ref=?, confirmed_at=? WHERE id=?",
                   (f"bank:{ref}", now(), pend["id"]))
            else:
                ex("INSERT INTO donations(story_id, to_user_id, from_user_id, donor_name, amount, message, status, source, "
                   "ref, created_at, confirmed_at) VALUES (?,?,?,?,?,?,'confirmed','webhook',?,?,?)",
                   (story["id"] if story else None, story["owner_id"] if story else None,
                    from_user["id"] if from_user else None, from_user["display_name"] if from_user else who,
                    amount, "", f"bank:{ref}", now(), now()))
            created += 1
    return {"ok": True, "count": created}
