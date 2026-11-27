"""Bảng quản trị: thống kê, thành viên, truyện, ủng hộ trang, việc dịch, cài đặt."""
from fastapi import APIRouter, HTTPException, Request

from ..auth import check_csrf, require_role
from ..db import ex, get_setting, now, q, q1, set_setting, val
from ..donate import BANK_BY_BIN
from ..services import delete_story
from ..templating import redirect, render
from ..utils import Page, norm, to_int, today

router = APIRouter(prefix="/quan-tri")


def _admin(request):
    return require_role(request, "admin")


@router.get("")
def dashboard(request: Request):
    _admin(request)
    stats = {
        "stories": val("SELECT COUNT(*) FROM stories", default=0),
        "public": val("SELECT COUNT(*) FROM stories WHERE is_hidden=0 AND chapter_count>0", default=0),
        "chapters": val("SELECT COUNT(*) FROM chapters", default=0),
        "users": val("SELECT COUNT(*) FROM users", default=0),
        "views_today": val("SELECT SUM(views) FROM story_views_daily WHERE day=?", (today(),), default=0),
        "views_total": val("SELECT SUM(views) FROM stories", default=0),
        "comments": val("SELECT COUNT(*) FROM comments WHERE is_deleted=0", default=0),
        "jobs_active": val("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')", default=0),
        "jobs_error": val("SELECT COUNT(*) FROM jobs WHERE status='error'", default=0),
        "donations_pending": val("SELECT COUNT(*) FROM donations WHERE to_user_id IS NULL AND status='pending'", default=0),
        "cost": val("SELECT SUM(cost_usd) FROM jobs", default=0),
    }
    chart = q("SELECT day, SUM(views) AS v FROM story_views_daily GROUP BY day ORDER BY day DESC LIMIT 14")
    recent_users = q("SELECT * FROM users ORDER BY created_at DESC LIMIT 8")
    recent_comments = q("SELECT c.*, u.username, s.title, s.slug FROM comments c JOIN users u ON u.id=c.user_id "
                        "JOIN stories s ON s.id=c.story_id WHERE c.is_deleted=0 ORDER BY c.created_at DESC LIMIT 10")
    return render(request, "admin_dashboard.html", stats=stats, chart=list(reversed(chart)), recent_users=recent_users,
                  recent_comments=recent_comments, section="dashboard")


@router.get("/thanh-vien")
def users(request: Request):
    _admin(request)
    kw = request.query_params.get("q", "").strip()
    where, params = "", ()
    if kw:
        where, params = "WHERE username LIKE ? OR display_name LIKE ?", (f"%{kw}%", f"%{kw}%")
    total = val(f"SELECT COUNT(*) FROM users {where}", params, default=0)
    pg = Page(to_int(request.query_params.get("trang"), 1, 1), 50, total)
    rows = q(f"SELECT u.*, (SELECT COUNT(*) FROM stories s WHERE s.owner_id=u.id) AS stories FROM users u {where} "
             f"ORDER BY u.created_at DESC LIMIT ? OFFSET ?", (*params, pg.per_page, pg.offset))
    return render(request, "admin_users.html", rows=rows, page=pg, kw=kw, section="users")


@router.post("/thanh-vien/{user_id:int}")
async def user_update(request: Request, user_id: int):
    me = _admin(request)
    form = await request.form()
    check_csrf(request, form)
    u = q1("SELECT * FROM users WHERE id=?", (user_id,))
    if not u:
        raise HTTPException(404, "Không tìm thấy thành viên")
    action = form.get("action")
    if u["id"] == me["id"] and action in ("ban", "role"):
        return redirect("/quan-tri/thanh-vien", "Không thể tự khoá hoặc đổi quyền của chính mình")
    if action == "role" and form.get("role") in ("reader", "uploader", "admin"):
        ex("UPDATE users SET role=? WHERE id=?", (form.get("role"), user_id))
    elif action == "ban":
        ex("UPDATE users SET is_banned=1-is_banned WHERE id=?", (user_id,))
    elif action == "resetpw":
        import secrets
        from ..auth import hash_password
        pw = secrets.token_urlsafe(8)
        h, salt = hash_password(pw)
        ex("UPDATE users SET pw_hash=?, pw_salt=? WHERE id=?", (h, salt, user_id))
        return redirect("/quan-tri/thanh-vien", f"Mật khẩu mới của {u['username']}: {pw} (chỉ hiện một lần)")
    return redirect("/quan-tri/thanh-vien", f"Đã cập nhật {u['username']}")


@router.get("/truyen")
def stories(request: Request):
    _admin(request)
    kw = request.query_params.get("q", "").strip()
    where, params = "", ()
    if kw:
        where, params = "WHERE s.title_norm LIKE ?", (f"%{norm(kw)}%",)
    total = val(f"SELECT COUNT(*) FROM stories s {where}", params, default=0)
    pg = Page(to_int(request.query_params.get("trang"), 1, 1), 50, total)
    rows = q(f"SELECT s.*, u.username FROM stories s JOIN users u ON u.id=s.owner_id {where} "
             f"ORDER BY s.is_featured DESC, s.updated_at DESC LIMIT ? OFFSET ?", (*params, pg.per_page, pg.offset))
    return render(request, "admin_stories.html", rows=rows, page=pg, kw=kw, section="stories")


@router.post("/truyen/{story_id:int}")
async def story_update(request: Request, story_id: int):
    _admin(request)
    form = await request.form()
    check_csrf(request, form)
    s = q1("SELECT * FROM stories WHERE id=?", (story_id,))
    if not s:
        raise HTTPException(404, "Không tìm thấy truyện")
    action = form.get("action")
    if action == "feature":
        ex("UPDATE stories SET is_featured=1-is_featured WHERE id=?", (story_id,))
    elif action == "hide":
        ex("UPDATE stories SET is_hidden=1-is_hidden WHERE id=?", (story_id,))
    elif action == "delete":
        if form.get("confirm") != s["slug"]:
            return redirect("/quan-tri/truyen", "Nhập đúng mã truyện để xác nhận xoá")
        delete_story(story_id)
        return redirect("/quan-tri/truyen", f"Đã xoá “{s['title']}”")
    return redirect(request.headers.get("referer") or "/quan-tri/truyen", "Đã cập nhật")


@router.get("/ung-ho")
def site_donations(request: Request):
    _admin(request)
    rows = q("SELECT d.*, s.title AS story_title, s.slug FROM donations d LEFT JOIN stories s ON s.id=d.story_id "
             "WHERE d.to_user_id IS NULL ORDER BY d.status='pending' DESC, d.created_at DESC LIMIT 300")
    total = val("SELECT SUM(amount) FROM donations WHERE to_user_id IS NULL AND status='confirmed'", default=0)
    return render(request, "author_donations.html", rows=rows, total=total, scope="admin", section="donations")


@router.get("/dich")
def jobs(request: Request):
    _admin(request)
    rows = q("SELECT j.*, s.title, s.slug, u.username, "
             "(SELECT COUNT(*) FROM source_chapters sc WHERE sc.story_id=j.story_id) AS total, "
             "(SELECT COUNT(*) FROM source_chapters sc WHERE sc.story_id=j.story_id AND sc.status='done') AS done, "
             "(SELECT COUNT(*) FROM source_chapters sc WHERE sc.story_id=j.story_id AND sc.status='error') AS errors "
             "FROM jobs j JOIN stories s ON s.id=j.story_id JOIN users u ON u.id=j.owner_id ORDER BY j.updated_at DESC LIMIT 200")
    from .importer import STATUS_TEXT
    return render(request, "admin_jobs.html", rows=rows, STATUS_TEXT=STATUS_TEXT, section="jobs")


SETTING_KEYS = ["donate_bank", "donate_account", "donate_name", "donate_momo", "donate_link", "announcement"]


@router.get("/cai-dat")
def site_settings(request: Request):
    _admin(request)
    from ..config import settings as cfg
    defaults = {"donate_bank": cfg.donate_bank, "donate_account": cfg.donate_account, "donate_name": cfg.donate_name,
                "donate_momo": cfg.donate_momo, "donate_link": cfg.donate_link, "announcement": ""}
    values = {k: get_setting(k, defaults[k]) for k in SETTING_KEYS}
    return render(request, "admin_settings.html", values=values, section="settings")


@router.post("/cai-dat")
async def site_settings_save(request: Request):
    _admin(request)
    form = await request.form()
    check_csrf(request, form)
    for k in SETTING_KEYS:
        v = str(form.get(k, "")).strip()[:500]
        if k == "donate_bank" and v and v not in BANK_BY_BIN:
            v = ""
        if k == "donate_link" and v and not v.startswith("https://"):
            return redirect("/quan-tri/cai-dat", "Link ủng hộ phải bắt đầu bằng https://")
        set_setting(k, v)
    set_setting("settings_updated", str(now()))
    return redirect("/quan-tri/cai-dat", "Đã lưu cài đặt")
