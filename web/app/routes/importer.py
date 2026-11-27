"""Nhập truyện Trung (link mục lục hoặc file .txt) và theo dõi việc dịch tự động."""
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from ..auth import can_import, check_csrf, require_user
from ..config import settings
from ..db import ex, now, q, q1, tx, val
from ..importer.fetch import FetchError, check_public_url
from ..importer.text import decode_bytes, split_chapters
from ..services import create_story
from ..templating import redirect, render
from ..translate.providers import CLAUDE_PRICES, estimate_claude_cost
from ..utils import Page, to_int
from ..worker import add_text_chapters

router = APIRouter(prefix="/nhap-truyen")

PROVIDERS = {"gemini": "Gemini (Google, có gói miễn phí)", "claude": "Claude (Anthropic, trả phí, dịch hay hơn)"}
STATUS_TEXT = {"queued": "Đang chờ", "running": "Đang chạy", "paused": "Tạm dừng", "done": "Đã xong đợt", "error": "Lỗi"}


def _require_importer(request):
    user = require_user(request)
    if not can_import(user):
        raise HTTPException(403, "Chỉ quản trị viên hoặc thành viên được cấp quyền “người đăng” mới nhập truyện Trung được")
    return user


def _job_for(user, job_id):
    job = q1("SELECT j.*, s.title, s.slug, s.original_title, s.chapter_count FROM jobs j JOIN stories s ON s.id=j.story_id "
             "WHERE j.id=?", (job_id,))
    if not job:
        raise HTTPException(404, "Không tìm thấy việc dịch")
    if user["role"] != "admin" and job["owner_id"] != user["id"]:
        raise HTTPException(403, "Đây không phải việc dịch của bạn")
    return job


def _providers():
    out = dict(PROVIDERS)
    if settings.translator == "mock":
        out["mock"] = "Dịch thử (không gọi AI, chỉ để kiểm tra)"
    return out


@router.get("")
def import_page(request: Request):
    user = _require_importer(request)
    where = "" if user["role"] == "admin" else "WHERE j.owner_id=?"
    jobs = q(f"SELECT j.*, s.title, s.slug, (SELECT COUNT(*) FROM source_chapters sc WHERE sc.story_id=j.story_id) AS total, "
             f"(SELECT COUNT(*) FROM source_chapters sc WHERE sc.story_id=j.story_id AND sc.status='done') AS done "
             f"FROM jobs j JOIN stories s ON s.id=j.story_id {where} ORDER BY j.updated_at DESC LIMIT 100",
             () if user["role"] == "admin" else (user["id"],))
    return render(request, "import_new.html", jobs=jobs, providers=_providers(), default_provider=settings.translator,
                  STATUS_TEXT=STATUS_TEXT, has_gemini=bool(settings.gemini_api_key), has_claude=bool(settings.claude_api_key))


@router.post("")
async def import_create(request: Request):
    user = _require_importer(request)
    form = await request.form()
    check_csrf(request, form)

    def back(msg):
        return redirect("/nhap-truyen", msg)

    if form.get("rights") != "1":
        return back("Bạn cần xác nhận có quyền dịch và đăng lại truyện này")
    provider = form.get("provider") if form.get("provider") in _providers() else "gemini"
    first_n = to_int(form.get("first_n"), 10, 1, 2000)
    title_vi = str(form.get("title_vi", "")).strip()[:200]
    title_zh = str(form.get("title_zh", "")).strip()[:200]
    author_zh = str(form.get("author_zh", "")).strip()[:100]
    url = str(form.get("url", "")).strip()
    f = form.get("file")
    chapters = None
    if f is not None and getattr(f, "filename", ""):
        data = await f.read()
        if len(data) > 50 * 1024 * 1024:
            return back("File quá lớn (tối đa 50 MB)")
        text = decode_bytes(data)
        chapters = split_chapters(text, "auto", 1)
        if not chapters:
            return back("Không đọc được nội dung file")
        title_zh = title_zh or Path(f.filename).stem[:200]
        url = ""
    elif url:
        try:
            check_public_url(url)
        except FetchError as e:
            return back(str(e))
    else:
        return back("Hãy dán link mục lục hoặc chọn file .txt")
    s = create_story(user["id"], title_vi or title_zh or "Truyện đang nhập", origin="translated",
                     original_title=title_zh, original_author=author_zh, author_name="")
    with tx():
        if chapters:
            add_text_chapters(s["id"], chapters)
        cur = ex("INSERT INTO jobs(story_id, owner_id, status, source_url, target_n, provider, toc_done, created_at, updated_at) "
                 "VALUES (?,?,?,?,?,?,?,?,?)",
                 (s["id"], user["id"], "queued", url or None, first_n, provider, 1 if chapters else 0, now(), now()))
    return redirect(f"/nhap-truyen/{cur.lastrowid}",
                    f"Đã tạo việc dịch{f' ({len(chapters)} chương trong file)' if chapters else ''}. Máy chủ sẽ tự chạy.")


def _job_stats(job):
    sid = job["story_id"]
    counts = {r["status"]: r["c"] for r in q("SELECT status, COUNT(*) AS c FROM source_chapters WHERE story_id=? GROUP BY status", (sid,))}
    total = sum(counts.values())
    avg_chars = val("SELECT AVG(LENGTH(content_zh)) FROM source_chapters WHERE story_id=? AND content_zh IS NOT NULL",
                    (sid,), default=None) or 3000
    return {"total": total, "done": counts.get("done", 0), "error": counts.get("error", 0),
            "pending": counts.get("pending", 0) + counts.get("translating", 0), "avg_chars": int(avg_chars)}


@router.get("/{job_id:int}")
def job_page(request: Request, job_id: int):
    user = _require_importer(request)
    job = _job_for(user, job_id)
    stats = _job_stats(job)
    pg = Page(to_int(request.query_params.get("trang"), 1, 1), 100, stats["total"])
    chapters = q("SELECT sc.id, sc.n, sc.title_zh, sc.status, sc.error, sc.url, LENGTH(sc.content_zh) AS chars, c.title AS title_vi "
                 "FROM source_chapters sc LEFT JOIN chapters c ON c.id=sc.chapter_id WHERE sc.story_id=? ORDER BY sc.n "
                 "LIMIT ? OFFSET ?", (job["story_id"], pg.per_page, pg.offset))
    glossary_count = val("SELECT COUNT(*) FROM glossary WHERE story_id=?", (job["story_id"],), default=0)
    estimates = {n: estimate_claude_cost(stats["avg_chars"] * n, n) for n in (10, 50, 100)}
    return render(request, "import_job.html", job=job, stats=stats, chapters=chapters, page=pg, STATUS_TEXT=STATUS_TEXT,
                  glossary_count=glossary_count, estimates=estimates, providers=_providers(),
                  claude_model=settings.claude_model, claude_price=CLAUDE_PRICES.get(settings.claude_model))


@router.get("/{job_id:int}/trang-thai.json")
def job_status(request: Request, job_id: int):
    user = _require_importer(request)
    job = _job_for(user, job_id)
    stats = _job_stats(job)
    return JSONResponse({"status": job["status"], "status_text": STATUS_TEXT.get(job["status"], job["status"]),
                         "message": job["message"], "error": job["error"], "cost": round(job["cost_usd"], 4),
                         "target": job["target_n"], **stats})


@router.post("/{job_id:int}/hanh-dong")
async def job_action(request: Request, job_id: int):
    user = _require_importer(request)
    job = _job_for(user, job_id)
    form = await request.form()
    check_csrf(request, form)
    action = form.get("action")
    sid = job["story_id"]
    msg = "Đã cập nhật"
    if action == "pause":
        ex("UPDATE jobs SET status='paused', message='Đã tạm dừng (chương đang dịch sẽ dịch xong rồi dừng)', updated_at=? "
           "WHERE id=? AND status IN ('queued','running')", (now(), job_id))
        msg = "Đã tạm dừng"
    elif action == "resume":
        ex("UPDATE jobs SET status='queued', error='', updated_at=? WHERE id=? AND status IN ('paused','error','done')",
           (now(), job_id))
        msg = "Đã cho chạy tiếp"
    elif action == "retry":
        with tx():
            ex("UPDATE source_chapters SET status='pending', error='' WHERE story_id=? AND status IN ('error','translating')", (sid,))
            ex("UPDATE jobs SET status='queued', error='', updated_at=? WHERE id=? AND status<>'running'", (now(), job_id))
        msg = "Sẽ dịch lại các chương lỗi"
    elif action == "more":
        n = to_int(form.get("count"), 10, 1, 2000)
        done_upto = val("SELECT MAX(n) FROM source_chapters WHERE story_id=? AND status='done'", (sid,), default=0)
        target = max(job["target_n"], done_upto) + n
        ex("UPDATE jobs SET target_n=?, status=CASE WHEN status='running' THEN 'running' ELSE 'queued' END, error='', "
           "updated_at=? WHERE id=?", (target, now(), job_id))
        msg = f"Sẽ dịch tới chương {target}"
    elif action == "retranslate":
        n = to_int(form.get("n"), 0)
        ex("UPDATE source_chapters SET status='pending', error='' WHERE story_id=? AND n=?", (sid, n))
        ex("UPDATE jobs SET target_n=MAX(target_n, ?), status=CASE WHEN status='running' THEN 'running' ELSE 'queued' END, "
           "updated_at=? WHERE id=?", (n, now(), job_id))
        msg = f"Sẽ dịch lại chương {n}"
    elif action == "refresh_toc":
        if not job["source_url"]:
            return redirect(f"/nhap-truyen/{job_id}", "Truyện nhập từ file, không có mục lục để cập nhật")
        ex("UPDATE jobs SET toc_done=0, status=CASE WHEN status='running' THEN 'running' ELSE 'queued' END, updated_at=? "
           "WHERE id=?", (now(), job_id))
        msg = "Sẽ tải lại mục lục để lấy chương mới"
    elif action == "provider":
        p = form.get("provider")
        if p in _providers():
            ex("UPDATE jobs SET provider=? WHERE id=?", (p, job_id))
            msg = f"Đã chuyển sang {PROVIDERS.get(p, p)} (áp dụng từ lần chạy sau)"
    elif action == "regloss":
        ex("UPDATE jobs SET glossary_done=0, status=CASE WHEN status='running' THEN 'running' ELSE 'queued' END, updated_at=? "
           "WHERE id=?", (now(), job_id))
        msg = "Sẽ lập lại bảng thuật ngữ (giữ các mục đã có)"
    return redirect(f"/nhap-truyen/{job_id}", msg)


@router.get("/{job_id:int}/tu-dien")
def glossary_page(request: Request, job_id: int):
    user = _require_importer(request)
    job = _job_for(user, job_id)
    rows = q("SELECT * FROM glossary WHERE story_id=? ORDER BY kind, zh", (job["story_id"],))
    return render(request, "import_glossary.html", job=job, rows=rows)


@router.post("/{job_id:int}/tu-dien")
async def glossary_save(request: Request, job_id: int):
    user = _require_importer(request)
    job = _job_for(user, job_id)
    form = await request.form(max_fields=30000)
    check_csrf(request, form)
    sid = job["story_id"]
    with tx():
        for gid, vi, kind in zip(form.getlist("id"), form.getlist("vi"), form.getlist("kind")):
            gid = to_int(gid)
            vi = str(vi).strip()[:80]
            if not gid:
                continue
            if not vi:
                ex("DELETE FROM glossary WHERE id=? AND story_id=?", (gid, sid))
                continue
            ex("UPDATE glossary SET vi=?, kind=?, locked=CASE WHEN vi<>? THEN 1 ELSE locked END WHERE id=? AND story_id=?",
               (vi, str(kind).strip()[:20], vi, gid, sid))
        for gid in form.getlist("delete"):
            ex("DELETE FROM glossary WHERE id=? AND story_id=?", (to_int(gid), sid))
        zh, vi = str(form.get("new_zh", "")).strip()[:60], str(form.get("new_vi", "")).strip()[:80]
        if zh and vi:
            ex("INSERT INTO glossary(story_id, zh, vi, kind, locked, created_at) VALUES (?,?,?,?,1,?) "
               "ON CONFLICT(story_id, zh) DO UPDATE SET vi=excluded.vi, kind=excluded.kind, locked=1",
               (sid, zh, vi, str(form.get("new_kind", "")).strip()[:20], now()))
    return redirect(f"/nhap-truyen/{job_id}/tu-dien", "Đã lưu bảng thuật ngữ. Các chương dịch sau sẽ dùng bảng mới.")
