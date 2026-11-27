"""Theo dõi, đánh giá, bình luận, báo cáo đã ủng hộ."""
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from ..auth import check_csrf, current_user, require_user
from ..db import ex, now, q1, tx, val
from ..services import story_path
from ..templating import redirect, safe_next
from ..utils import to_int

router = APIRouter()

_claim_times = {}  # ip → [thời điểm] (chống spam báo cáo ủng hộ ẩn danh)


def _wants_json(request):
    return "application/json" in request.headers.get("accept", "")


def _story(story_id):
    s = q1("SELECT * FROM stories WHERE id=?", (story_id,))
    if not s:
        raise HTTPException(404, "Không tìm thấy truyện")
    return s


@router.post("/truyen/{story_id:int}/theo-doi")
async def follow(request: Request, story_id: int):
    user = require_user(request)
    form = await request.form()
    check_csrf(request, form)
    s = _story(story_id)
    with tx():
        if q1("SELECT 1 FROM follows WHERE user_id=? AND story_id=?", (user["id"], s["id"])):
            ex("DELETE FROM follows WHERE user_id=? AND story_id=?", (user["id"], s["id"]))
            following = False
        else:
            ex("INSERT INTO follows(user_id, story_id, last_seen_n, created_at) VALUES (?,?,?,?)",
               (user["id"], s["id"], s["last_chapter_n"], now()))
            following = True
        ex("UPDATE stories SET follows=(SELECT COUNT(*) FROM follows WHERE story_id=?) WHERE id=?", (s["id"], s["id"]))
    count = val("SELECT follows FROM stories WHERE id=?", (s["id"],))
    if _wants_json(request):
        return JSONResponse({"following": following, "count": count})
    return redirect(story_path(s), "Đã thêm vào tủ truyện" if following else "Đã bỏ theo dõi")


@router.post("/truyen/{story_id:int}/danh-gia")
async def rate(request: Request, story_id: int):
    user = require_user(request)
    form = await request.form()
    check_csrf(request, form)
    s = _story(story_id)
    stars = to_int(form.get("stars"), 0)
    if not 1 <= stars <= 5:
        raise HTTPException(400, "Số sao phải từ 1 đến 5")
    with tx():
        ex("INSERT INTO ratings(user_id, story_id, stars, created_at) VALUES (?,?,?,?) "
           "ON CONFLICT(user_id, story_id) DO UPDATE SET stars=excluded.stars", (user["id"], s["id"], stars, now()))
        ex("UPDATE stories SET rating_sum=(SELECT COALESCE(SUM(stars),0) FROM ratings WHERE story_id=?), "
           "rating_count=(SELECT COUNT(*) FROM ratings WHERE story_id=?) WHERE id=?", (s["id"], s["id"], s["id"]))
    r = q1("SELECT rating_sum, rating_count FROM stories WHERE id=?", (s["id"],))
    avg = round(r["rating_sum"] / r["rating_count"], 1) if r["rating_count"] else 0
    if _wants_json(request):
        return JSONResponse({"stars": stars, "avg": avg, "count": r["rating_count"]})
    return redirect(story_path(s), f"Bạn đã chấm {stars} sao")


@router.post("/binh-luan")
async def comment(request: Request):
    user = require_user(request)
    form = await request.form()
    check_csrf(request, form)
    s = _story(to_int(form.get("story_id"), 0))
    chapter_id = to_int(form.get("chapter_id"), 0) or None
    nxt = safe_next(form.get("next"), story_path(s)) + "#binh-luan"
    body = str(form.get("body", "")).strip()
    if chapter_id and not q1("SELECT 1 FROM chapters WHERE id=? AND story_id=?", (chapter_id, s["id"])):
        raise HTTPException(400, "Chương không hợp lệ")
    if len(body) < 2:
        return redirect(nxt, "Bình luận quá ngắn")
    if len(body) > 2000:
        return redirect(nxt, "Bình luận tối đa 2000 ký tự")
    t = now()
    recent = val("SELECT COUNT(*) FROM comments WHERE user_id=? AND created_at>?", (user["id"], t - 600), default=0)
    last = val("SELECT MAX(created_at) FROM comments WHERE user_id=?", (user["id"],), default=0)
    if recent >= 10 or t - last < 15:
        return redirect(nxt, "Bạn bình luận nhanh quá, hãy đợi một chút")
    ex("INSERT INTO comments(story_id, chapter_id, user_id, body, created_at) VALUES (?,?,?,?,?)",
       (s["id"], chapter_id, user["id"], body, t))
    return redirect(nxt, "Đã gửi bình luận")


@router.post("/binh-luan/{comment_id:int}/xoa")
async def delete_comment(request: Request, comment_id: int):
    user = require_user(request)
    form = await request.form()
    check_csrf(request, form)
    c = q1("SELECT c.*, s.owner_id FROM comments c JOIN stories s ON s.id=c.story_id WHERE c.id=?", (comment_id,))
    if not c:
        raise HTTPException(404, "Không tìm thấy bình luận")
    if not (user["role"] == "admin" or user["id"] in (c["user_id"], c["owner_id"])):
        raise HTTPException(403, "Bạn không thể xoá bình luận này")
    ex("UPDATE comments SET is_deleted=1 WHERE id=?", (comment_id,))
    return redirect(safe_next(form.get("next"), "/") + "#binh-luan", "Đã xoá bình luận")


@router.post("/ung-ho/bao-cao")
async def donation_claim(request: Request):
    """Bạn đọc bấm "Tôi đã ủng hộ": lưu chờ tác giả/admin xác nhận."""
    user = current_user(request)
    form = await request.form()
    check_csrf(request, form)
    story_id = to_int(form.get("story_id"), 0)
    s = q1("SELECT * FROM stories WHERE id=?", (story_id,)) if story_id else None
    target = form.get("target", "author")
    nxt = safe_next(form.get("next"), story_path(s) if s else "/ung-ho")
    amount = to_int(str(form.get("amount", "")).replace(".", "").replace(",", ""), 0)
    if amount < 1000 or amount > 1_000_000_000:
        return redirect(nxt, "Số tiền không hợp lệ")
    name = (str(form.get("donor_name", "")).strip() or (user["display_name"] if user else "") or "Ẩn danh")[:40]
    message = str(form.get("message", "")).strip()[:300]
    ip = request.client.host if request.client else ""
    t = time.time()
    times = [x for x in _claim_times.get(ip, []) if t - x < 3600]
    if len(times) >= 5:
        return redirect(nxt, "Bạn gửi quá nhiều báo cáo, hãy thử lại sau")
    _claim_times[ip] = times + [t]
    to_user = s["owner_id"] if (s and target == "author") else None
    ex("INSERT INTO donations(story_id, to_user_id, from_user_id, donor_name, amount, message, ip, created_at) "
       "VALUES (?,?,?,?,?,?,?,?)",
       (s["id"] if s and to_user else None, to_user, user["id"] if user else None, name, amount, message, ip, now()))
    return redirect(nxt, "Cảm ơn bạn đã ủng hộ! Tên bạn sẽ hiện trong bảng vinh danh sau khi được xác nhận.")
