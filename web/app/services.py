"""Nghiệp vụ dùng chung: truyện, chương, thể loại, lượt xem."""
import hashlib

from .config import settings
from .db import ex, get_db, now, q, q1, tx, val
from .utils import day_offset, norm, normalize_content, slugify, today, unique_slug, word_count

VISIBLE = "c.is_published=1 AND c.publish_at<=?"  # điều kiện chương đang hiển thị (tham số: now)

STATUS_LABEL = {"ongoing": "Đang ra", "completed": "Hoàn thành"}
ORIGIN_LABEL = {"original": "Sáng tác", "translated": "Dịch", "ai": "AI viết"}
ROLE_LABEL = {"reader": "Bạn đọc", "uploader": "Người đăng", "admin": "Quản trị"}

MAX_CHAPTER_CHARS = 200_000


def base_url(request=None) -> str:
    if settings.site_url:
        return settings.site_url
    if request is not None:
        return str(request.base_url).rstrip("/")
    return ""


def story_path(story) -> str:
    return f"/truyen/{story['slug']}"


def chapter_path(story, n) -> str:
    return f"/truyen/{story['slug']}/chuong-{n}"


def cover_url(story) -> str | None:
    return f"/media/covers/{story['cover']}" if story and story["cover"] else None


# ---------------------------------------------------------------- thể loại

def all_genres():
    return q("SELECT * FROM genres ORDER BY sort, name")


def genre_ids_from_names(names, create=True, conn=None):
    """Tên thể loại → id; tên lạ thì tạo mới (tối đa 40 ký tự)."""
    conn = conn or get_db()
    ids = []
    for name in names or []:
        name = str(name).strip()[:40]
        if not name:
            continue
        slug = slugify(name)
        row = conn.execute("SELECT id FROM genres WHERE slug=?", (slug,)).fetchone()
        if row:
            ids.append(row[0])
        elif create:
            cur = conn.execute("INSERT INTO genres(slug, name) VALUES (?,?)", (slug, name[:1].upper() + name[1:]))
            ids.append(cur.lastrowid)
    return list(dict.fromkeys(ids))


def set_story_genres(story_id, genre_ids, conn=None):
    conn = conn or get_db()
    conn.execute("DELETE FROM story_genres WHERE story_id=?", (story_id,))
    for gid in genre_ids:
        conn.execute("INSERT OR IGNORE INTO story_genres(story_id, genre_id) VALUES (?,?)", (story_id, gid))


def story_genres(story_id):
    return q("SELECT g.* FROM genres g JOIN story_genres sg ON sg.genre_id=g.id WHERE sg.story_id=? ORDER BY g.sort, g.name",
             (story_id,))


def genres_map(story_ids):
    """{story_id: [genre rows]} cho danh sách truyện."""
    if not story_ids:
        return {}
    marks = ",".join("?" * len(story_ids))
    out = {}
    for r in q(f"SELECT sg.story_id, g.name, g.slug FROM story_genres sg JOIN genres g ON g.id=sg.genre_id "
               f"WHERE sg.story_id IN ({marks}) ORDER BY g.sort", tuple(story_ids)):
        out.setdefault(r["story_id"], []).append(r)
    return out


# ---------------------------------------------------------------- truyện

def create_story(owner_id, title, *, description="", status="ongoing", origin="original", author_name="",
                 original_title="", original_author="", source_id=None, genre_ids=(), conn=None):
    conn = conn or get_db()
    t = now()
    with tx(conn):
        slug = unique_slug(slugify(title), lambda s: conn.execute("SELECT 1 FROM stories WHERE slug=?", (s,)).fetchone())
        cur = conn.execute(
            "INSERT INTO stories(owner_id, source_id, slug, title, title_norm, author_name, author_norm, original_title, "
            "original_author, description, status, origin, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (owner_id, source_id, slug, title, norm(title + " " + original_title), author_name, norm(author_name),
             original_title, original_author, description, status, origin, t, t))
        sid = cur.lastrowid
        set_story_genres(sid, genre_ids, conn)
    return q1("SELECT * FROM stories WHERE id=?", (sid,), conn)


def update_story(story_id, conn=None, **fields):
    conn = conn or get_db()
    allowed = {"title", "author_name", "original_title", "original_author", "description", "status", "origin",
               "cover", "is_hidden", "is_featured"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return
    row = conn.execute("SELECT title, original_title, author_name FROM stories WHERE id=?", (story_id,)).fetchone()
    title = sets.get("title", row["title"])
    sets["title_norm"] = norm(title + " " + sets.get("original_title", row["original_title"]))
    sets["author_norm"] = norm(sets.get("author_name", row["author_name"]))
    sets["updated_at"] = now()
    cols = ", ".join(f"{k}=?" for k in sets)
    conn.execute(f"UPDATE stories SET {cols} WHERE id=?", (*sets.values(), story_id))


def refresh_story_stats(story_id, conn=None):
    """Tính lại số chương đang hiện, chương mới nhất (gọi sau khi sửa chương / tới giờ hẹn đăng)."""
    conn = conn or get_db()
    t = now()
    row = conn.execute(
        f"SELECT COUNT(*) AS cnt, MAX(c.n) AS last_n FROM chapters c WHERE c.story_id=? AND {VISIBLE}",
        (story_id, t)).fetchone()
    last = None
    if row["last_n"]:
        last = conn.execute("SELECT title, publish_at FROM chapters WHERE story_id=? AND n=?",
                            (story_id, row["last_n"])).fetchone()
    conn.execute(
        "UPDATE stories SET chapter_count=?, last_chapter_n=?, last_chapter_title=?, "
        "last_chapter_at=MAX(last_chapter_at, ?) WHERE id=?",
        (row["cnt"], row["last_n"] or 0, last["title"] if last else "", last["publish_at"] if last else 0, story_id))


def upsert_chapter(story_id, n, title, content, publish=True, publish_at=None, conn=None, refresh=True):
    """Thêm/sửa chương số n. publish_at (timestamp) > hiện tại = hẹn giờ đăng."""
    conn = conn or get_db()
    content = normalize_content(content)[:MAX_CHAPTER_CHARS]
    title = (title or "").strip()[:200]
    t = now()
    pa = publish_at if publish_at else t
    old = conn.execute("SELECT id, is_published, publish_at FROM chapters WHERE story_id=? AND n=?", (story_id, n)).fetchone()
    if old:
        # đã đăng rồi thì giữ nguyên thời điểm đăng (không đẩy truyện lên "mới cập nhật" chỉ vì sửa lỗi chính tả)
        if old["is_published"] and publish and not publish_at and old["publish_at"] <= t:
            pa = old["publish_at"]
        conn.execute("UPDATE chapters SET title=?, content=?, words=?, is_published=?, publish_at=?, updated_at=? WHERE id=?",
                     (title, content, word_count(content), 1 if publish else 0, pa, t, old["id"]))
        cid = old["id"]
    else:
        cur = conn.execute(
            "INSERT INTO chapters(story_id, n, title, content, words, is_published, publish_at, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (story_id, n, title, content, word_count(content), 1 if publish else 0, pa, t, t))
        cid = cur.lastrowid
    conn.execute("UPDATE stories SET updated_at=? WHERE id=?", (t, story_id))
    if refresh:
        refresh_story_stats(story_id, conn)
    return cid


def delete_chapter(story_id, n, conn=None):
    conn = conn or get_db()
    conn.execute("DELETE FROM chapters WHERE story_id=? AND n=?", (story_id, n))
    refresh_story_stats(story_id, conn)


def renumber_chapters(story_id, conn=None):
    """Đánh lại số chương liên tục 1..N theo thứ tự hiện tại."""
    conn = conn or get_db()
    with tx(conn):
        rows = conn.execute("SELECT id FROM chapters WHERE story_id=? ORDER BY n", (story_id,)).fetchall()
        # tránh đụng UNIQUE(story_id, n): đổi sang số âm trước
        for i, r in enumerate(rows, 1):
            conn.execute("UPDATE chapters SET n=? WHERE id=?", (-i, r["id"]))
        conn.execute("UPDATE chapters SET n=-n WHERE story_id=?", (story_id,))
        refresh_story_stats(story_id, conn)


def delete_story(story_id):
    from .covers import remove_file
    s = q1("SELECT cover FROM stories WHERE id=?", (story_id,))
    with tx():
        ex("DELETE FROM jobs WHERE story_id=?", (story_id,))
        ex("DELETE FROM donations WHERE story_id=? AND status='pending'", (story_id,))
        ex("DELETE FROM stories WHERE id=?", (story_id,))
    if s:
        remove_file("covers", s["cover"])


def can_edit(user, story) -> bool:
    return bool(user) and (user["role"] == "admin" or user["id"] == story["owner_id"])


def publish_due_chapters():
    """Tới giờ hẹn: cập nhật lại thống kê các truyện có chương vừa tới giờ đăng."""
    t = now()
    last = int(val("SELECT value FROM site_settings WHERE key='schedule_tick'", default=0) or 0)
    rows = q("SELECT DISTINCT story_id FROM chapters WHERE is_published=1 AND publish_at>? AND publish_at<=?",
             (last, t))
    for r in rows:
        with tx():
            refresh_story_stats(r["story_id"])
    ex("INSERT INTO site_settings(key, value) VALUES ('schedule_tick', ?) "
       "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(t),))
    return len(rows)


# ---------------------------------------------------------------- lượt xem

def client_ip(request) -> str:
    """IP người dùng. Sau nginx/Cloudflare Tunnel: chạy uvicorn với --proxy-headers (mặc định tin 127.0.0.1)
    để request.client là IP thật; không đọc thẳng header X-Real-IP vì ai cũng giả được."""
    return request.client.host if request.client else ""


def visitor_key(request, user) -> str:
    if user:
        return f"u{user['id']}"
    ip = client_ip(request)
    ua = request.headers.get("user-agent", "")
    return "a" + hashlib.sha1(f"{ip}|{ua}".encode()).hexdigest()[:16]


BOT_HINTS = ("bot", "spider", "crawl", "slurp", "facebookexternalhit", "preview")


def record_view(request, user, story_id, chapter_id):
    """Đếm 1 lượt xem / người / chương / ngày; bỏ qua bot."""
    ua = request.headers.get("user-agent", "").lower()
    if not ua or any(b in ua for b in BOT_HINTS):
        return False
    day = today()
    key = f"{day}|{chapter_id}|{visitor_key(request, user)}"
    conn = get_db()
    with tx(conn):
        cur = conn.execute("INSERT OR IGNORE INTO view_log(key, day) VALUES (?,?)", (key, day))
        if cur.rowcount != 1:
            return False
        conn.execute("UPDATE chapters SET views=views+1 WHERE id=?", (chapter_id,))
        conn.execute("UPDATE stories SET views=views+1 WHERE id=?", (story_id,))
        conn.execute("INSERT INTO story_views_daily(story_id, day, views) VALUES (?,?,1) "
                     "ON CONFLICT(story_id, day) DO UPDATE SET views=views+1", (story_id, day))
    return True


def prune_view_log():
    ex("DELETE FROM view_log WHERE day<?", (day_offset(2),))
    ex("DELETE FROM story_views_daily WHERE day<?", (day_offset(60),))


def record_history(user, story_id, n):
    if not user:
        return
    t = now()
    with tx():
        ex("INSERT INTO reading_history(user_id, story_id, chapter_n, updated_at) VALUES (?,?,?,?) "
           "ON CONFLICT(user_id, story_id) DO UPDATE SET chapter_n=excluded.chapter_n, updated_at=excluded.updated_at",
           (user["id"], story_id, n, t))
        ex("UPDATE follows SET last_seen_n=MAX(last_seen_n, ?) WHERE user_id=? AND story_id=?", (n, user["id"], story_id))
