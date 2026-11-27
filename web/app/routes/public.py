"""Trang công khai: trang chủ, danh sách/tìm kiếm, trang truyện, trang đọc, RSS, sitemap."""
from xml.sax.saxutils import escape

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response

from ..auth import current_user
from ..db import now, q, q1, val
from ..donate import payee_from_user, site_payee, transfer_note
from ..services import (VISIBLE, all_genres, base_url, chapter_path, genres_map, record_history, record_view,
                        story_genres, story_path)
from ..templating import render
from ..utils import Page, day_offset, excerpt, iso, norm, paragraphs, rfc822, to_int

router = APIRouter()

PUBLIC = "s.is_hidden=0 AND s.chapter_count>0"
STORY_COLS = ("s.id, s.slug, s.title, s.author_name, s.cover, s.status, s.origin, s.chapter_count, s.last_chapter_n, "
              "s.last_chapter_title, s.last_chapter_at, s.views, s.follows, s.rating_sum, s.rating_count, s.description, "
              "s.created_at, s.is_featured")


def _with_genres(rows):
    gm = genres_map([r["id"] for r in rows])
    out = []
    for r in rows:
        d = dict(r)
        d["genres"] = gm.get(r["id"], [])
        d["rating"] = round(r["rating_sum"] / r["rating_count"], 1) if r["rating_count"] else 0
        out.append(d)
    return out


@router.get("/")
def home(request: Request):
    featured = q(f"SELECT {STORY_COLS} FROM stories s WHERE {PUBLIC} AND s.is_featured=1 ORDER BY s.views DESC LIMIT 6")
    if len(featured) < 3:
        featured = q(f"SELECT {STORY_COLS} FROM stories s WHERE {PUBLIC} ORDER BY s.views DESC LIMIT 5")
    latest = q(f"SELECT {STORY_COLS} FROM stories s WHERE {PUBLIC} ORDER BY s.last_chapter_at DESC LIMIT 16")
    top_all = q(f"SELECT {STORY_COLS} FROM stories s WHERE {PUBLIC} ORDER BY s.views DESC LIMIT 10")
    top_week = q(f"SELECT {STORY_COLS}, SUM(d.views) AS week_views FROM story_views_daily d JOIN stories s ON s.id=d.story_id "
                 f"WHERE d.day>=? AND {PUBLIC} GROUP BY s.id ORDER BY week_views DESC LIMIT 10", (day_offset(6),))
    completed = q(f"SELECT {STORY_COLS} FROM stories s WHERE {PUBLIC} AND s.status='completed' ORDER BY s.views DESC LIMIT 12")
    newest = q(f"SELECT {STORY_COLS} FROM stories s WHERE {PUBLIC} ORDER BY s.created_at DESC LIMIT 12")
    genres = q("SELECT g.*, COUNT(sg.story_id) AS cnt FROM genres g LEFT JOIN story_genres sg ON sg.genre_id=g.id "
               "GROUP BY g.id ORDER BY g.sort, g.name")
    return render(request, "home.html", featured=_with_genres(featured), latest=_with_genres(latest),
                  top_all=_with_genres(top_all), top_week=_with_genres(top_week) or _with_genres(top_all),
                  completed=_with_genres(completed), newest=_with_genres(newest), genres=genres,
                  canonical=base_url(request) + "/")


SORTS = {
    "moi": ("Mới cập nhật", "s.last_chapter_at DESC"),
    "xem": ("Lượt đọc", "s.views DESC"),
    "chuong": ("Số chương", "s.chapter_count DESC"),
    "danh-gia": ("Đánh giá", "(CASE WHEN s.rating_count>0 THEN s.rating_sum*1.0/s.rating_count ELSE 0 END) DESC, s.rating_count DESC"),
    "theo-doi": ("Theo dõi", "s.follows DESC"),
    "moi-dang": ("Truyện mới", "s.created_at DESC"),
}
SOURCES = {"sang-tac": "original", "dich": "translated", "ai": "ai"}
STATUSES = {"dang-ra": "ongoing", "hoan-thanh": "completed"}


@router.get("/danh-sach")
def browse(request: Request):
    return _browse(request, "", "", "", "", "moi", 1)


def _browse(request, q_text, genre_slug, status, source, sort, page_n, title=None, canonical_path="/danh-sach"):
    p = request.query_params
    q_text = p.get("q", q_text).strip()[:100]
    genre_slug = p.get("the-loai", genre_slug)
    status = p.get("trang-thai", status)
    source = p.get("nguon", source)
    sort = p.get("sap-xep", sort) if p.get("sap-xep", sort) in SORTS else "moi"
    page_n = to_int(p.get("trang", page_n), 1, 1, 10000)
    where, params, join = [PUBLIC], [], ""
    genre = None
    if genre_slug:
        genre = q1("SELECT * FROM genres WHERE slug=?", (genre_slug,))
        if not genre:
            raise HTTPException(404, "Không có thể loại này")
        join = "JOIN story_genres sg ON sg.story_id=s.id AND sg.genre_id=?"
        params.append(genre["id"])
    if status in STATUSES:
        where.append("s.status=?")
        params.append(STATUSES[status])
    if source in SOURCES:
        where.append("s.origin=?")
        params.append(SOURCES[source])
    if q_text:
        for word in norm(q_text).split()[:6]:
            where.append("(s.title_norm LIKE ? OR s.author_norm LIKE ? OR s.id IN (SELECT sg2.story_id FROM story_genres sg2 "
                         "JOIN genres g2 ON g2.id=sg2.genre_id WHERE g2.slug LIKE ?))")
            params += [f"%{word}%", f"%{word}%", f"%{word}%"]
    w = " AND ".join(where)
    total = val(f"SELECT COUNT(*) FROM stories s {join} WHERE {w}", tuple(params), default=0)
    pg = Page(page_n, 24, total)
    rows = q(f"SELECT {STORY_COLS} FROM stories s {join} WHERE {w} ORDER BY {SORTS[sort][1]} LIMIT ? OFFSET ?",
             (*params, pg.per_page, pg.offset))
    if not title:
        title = f"Tìm “{q_text}”" if q_text else (f"Truyện {genre['name']}" if genre else "Danh sách truyện")
    qs = {k: v for k, v in {"q": q_text, "the-loai": genre_slug if not canonical_path.startswith("/the-loai") else "",
                            "trang-thai": status, "nguon": source, "sap-xep": sort}.items() if v}
    return render(request, "browse.html", stories=_with_genres(rows), page=pg, total=total, genres=all_genres(),
                  genre=genre, q=q_text, status_filter=status, source=source, sort=sort, sorts=SORTS, title=title, qs=qs,
                  list_path=canonical_path, canonical=base_url(request) + canonical_path +
                  (f"?trang={pg.page}" if pg.page > 1 else ""))


@router.get("/the-loai/{slug}")
def genre_page(request: Request, slug: str):
    g = q1("SELECT * FROM genres WHERE slug=?", (slug,))
    if not g:
        raise HTTPException(404, "Không có thể loại này")
    return _browse(request, "", slug, "", "", "moi", 1, title=f"Truyện {g['name']}", canonical_path=f"/the-loai/{slug}")


@router.get("/hoan-thanh")
def completed_page(request: Request):
    return _browse(request, "", "", "hoan-thanh", "", "xem", 1, title="Truyện đã hoàn thành", canonical_path="/hoan-thanh")


@router.get("/xep-hang")
def ranking(request: Request):
    period = request.query_params.get("ky", "tuan")
    if period == "tuan":
        rows = q(f"SELECT {STORY_COLS}, SUM(d.views) AS period_views FROM story_views_daily d JOIN stories s ON s.id=d.story_id "
                 f"WHERE d.day>=? AND {PUBLIC} GROUP BY s.id ORDER BY period_views DESC LIMIT 50", (day_offset(6),))
    elif period == "thang":
        rows = q(f"SELECT {STORY_COLS}, SUM(d.views) AS period_views FROM story_views_daily d JOIN stories s ON s.id=d.story_id "
                 f"WHERE d.day>=? AND {PUBLIC} GROUP BY s.id ORDER BY period_views DESC LIMIT 50", (day_offset(29),))
    else:
        period = "tat-ca"
        rows = q(f"SELECT {STORY_COLS}, s.views AS period_views FROM stories s WHERE {PUBLIC} ORDER BY s.views DESC LIMIT 50")
    return render(request, "ranking.html", stories=_with_genres(rows), period=period,
                  canonical=base_url(request) + "/xep-hang")


# ---------------------------------------------------------------- trang truyện

def get_story_or_404(slug, user=None):
    s = q1("SELECT s.*, u.username AS owner_username, u.display_name AS owner_name FROM stories s "
           "JOIN users u ON u.id=s.owner_id WHERE s.slug=?", (slug,))
    if not s:
        raise HTTPException(404, "Không tìm thấy truyện")
    if s["is_hidden"] and not (user and (user["role"] == "admin" or user["id"] == s["owner_id"])):
        raise HTTPException(404, "Không tìm thấy truyện")
    return s


def donate_context(story, user):
    owner = q1("SELECT * FROM users WHERE id=?", (story["owner_id"],)) if story else None
    payee = payee_from_user(owner)
    target = "author"
    if not payee:
        payee, target = site_payee(), "site"
    return {"payee": payee, "donate_target": target,
            "donate_note": transfer_note(story["id"] if story else 0, user["username"] if user else "")}


def comments_for(story_id, chapter_id, page_n=1):
    cond = "c.chapter_id=?" if chapter_id else "c.chapter_id IS NULL"
    params = (story_id, chapter_id) if chapter_id else (story_id,)
    total = val(f"SELECT COUNT(*) FROM comments c WHERE c.story_id=? AND {cond} AND c.is_deleted=0", params, default=0)
    pg = Page(page_n, 30, total)
    rows = q(f"SELECT c.*, u.username, u.display_name, u.avatar, u.role FROM comments c JOIN users u ON u.id=c.user_id "
             f"WHERE c.story_id=? AND {cond} AND c.is_deleted=0 ORDER BY c.created_at DESC LIMIT ? OFFSET ?",
             (*params, pg.per_page, pg.offset))
    return rows, pg


@router.get("/truyen/{slug}")
def story_page(request: Request, slug: str):
    user = current_user(request)
    s = get_story_or_404(slug, user)
    order = "desc" if request.query_params.get("thu-tu") == "moi" else "asc"
    t = now()
    pg = Page(to_int(request.query_params.get("trang"), 1, 1), 100, s["chapter_count"])
    chapters = q(f"SELECT c.n, c.title, c.publish_at FROM chapters c WHERE c.story_id=? AND {VISIBLE} "
                 f"ORDER BY c.n {'DESC' if order == 'desc' else 'ASC'} LIMIT ? OFFSET ?",
                 (s["id"], t, pg.per_page, pg.offset))
    first = val(f"SELECT MIN(c.n) FROM chapters c WHERE c.story_id=? AND {VISIBLE}", (s["id"], t))
    following, my_rating, history = False, 0, None
    if user:
        following = bool(q1("SELECT 1 FROM follows WHERE user_id=? AND story_id=?", (user["id"], s["id"])))
        my_rating = val("SELECT stars FROM ratings WHERE user_id=? AND story_id=?", (user["id"], s["id"]), default=0)
        history = val("SELECT chapter_n FROM reading_history WHERE user_id=? AND story_id=?", (user["id"], s["id"]))
    supporters = q("SELECT donor_name, SUM(amount) AS total, COUNT(*) AS times, MAX(message) AS message FROM donations "
                   "WHERE story_id=? AND status='confirmed' GROUP BY donor_name ORDER BY total DESC LIMIT 10", (s["id"],))
    comments, cpg = comments_for(s["id"], None, to_int(request.query_params.get("bl"), 1, 1))
    related = q(f"SELECT {STORY_COLS} FROM stories s JOIN story_genres sg ON sg.story_id=s.id "
                f"WHERE sg.genre_id IN (SELECT genre_id FROM story_genres WHERE story_id=?) AND s.id<>? AND {PUBLIC} "
                f"GROUP BY s.id ORDER BY s.views DESC LIMIT 6", (s["id"], s["id"]))
    rating = round(s["rating_sum"] / s["rating_count"], 1) if s["rating_count"] else 0
    words = val("SELECT SUM(words) FROM chapters WHERE story_id=? AND is_published=1", (s["id"],), default=0)
    return render(request, "story.html", s=s, genres=story_genres(s["id"]), chapters=chapters, page=pg, order=order,
                  first_n=first, following=following, my_rating=my_rating, history=history, rating=rating,
                  supporters=supporters, comments=comments, cpage=cpg, related=_with_genres(related), words=words,
                  can_edit=bool(user and (user["role"] == "admin" or user["id"] == s["owner_id"])),
                  canonical=base_url(request) + story_path(s), meta_description=excerpt(s["description"], 160),
                  **donate_context(s, user))


@router.get("/truyen/{slug}/chuong-{n:int}")
def chapter_page(request: Request, slug: str, n: int):
    user = current_user(request)
    s = get_story_or_404(slug, user)
    t = now()
    owner_view = bool(user and (user["role"] == "admin" or user["id"] == s["owner_id"]))
    c = q1("SELECT * FROM chapters WHERE story_id=? AND n=?", (s["id"], n))
    if not c or (not (c["is_published"] and c["publish_at"] <= t) and not owner_view):
        raise HTTPException(404, "Chương này chưa có hoặc chưa được đăng")
    prev_n = val(f"SELECT MAX(c.n) FROM chapters c WHERE c.story_id=? AND c.n<? AND {VISIBLE}", (s["id"], n, t))
    next_n = val(f"SELECT MIN(c.n) FROM chapters c WHERE c.story_id=? AND c.n>? AND {VISIBLE}", (s["id"], n, t))
    nearby = q(f"SELECT c.n, c.title FROM chapters c WHERE c.story_id=? AND {VISIBLE} AND c.n BETWEEN ? AND ? ORDER BY c.n",
               (s["id"], t, n - 60, n + 60))
    if c["is_published"] and c["publish_at"] <= t:
        record_view(request, user, s["id"], c["id"])
        record_history(user, s["id"], n)
    comments, cpg = comments_for(s["id"], c["id"], to_int(request.query_params.get("bl"), 1, 1))
    paras = paragraphs(c["content"])
    title = f"Chương {n}" + (f": {c['title']}" if c["title"] else "")
    return render(request, "reader.html", s=s, c=c, paras=paras, prev_n=prev_n, next_n=next_n, nearby=nearby,
                  chapter_title=title, comments=comments, cpage=cpg, draft=not (c["is_published"] and c["publish_at"] <= t),
                  canonical=base_url(request) + chapter_path(s, n),
                  meta_description=excerpt(" ".join(paras[:3]), 160), **donate_context(s, user))


# ---------------------------------------------------------------- ủng hộ trang

@router.get("/ung-ho")
def donate_site(request: Request):
    user = current_user(request)
    supporters = q("SELECT donor_name, SUM(amount) AS total, MAX(message) AS message, MAX(created_at) AS at FROM donations "
                   "WHERE story_id IS NULL AND to_user_id IS NULL AND status='confirmed' GROUP BY donor_name "
                   "ORDER BY total DESC LIMIT 30")
    return render(request, "donate_site.html", payee=site_payee(), supporters=supporters,
                  donate_note=transfer_note(0, user["username"] if user else ""), donate_target="site",
                  canonical=base_url(request) + "/ung-ho")


# ---------------------------------------------------------------- RSS, sitemap, robots

@router.get("/truyen/{slug}/rss.xml")
def story_rss(request: Request, slug: str):
    s = get_story_or_404(slug)
    b = base_url(request)
    rows = q(f"SELECT c.n, c.title, c.publish_at, substr(c.content, 1, 400) AS head FROM chapters c "
             f"WHERE c.story_id=? AND {VISIBLE} ORDER BY c.n DESC LIMIT 30", (s["id"], now()))
    items = []
    for r in rows:
        link = b + chapter_path(s, r["n"])
        t = f"Chương {r['n']}" + (f": {r['title']}" if r["title"] else "")
        items.append(f"<item><title>{escape(t)}</title><link>{escape(link)}</link><guid>{escape(link)}</guid>"
                     f"<pubDate>{rfc822(r['publish_at'])}</pubDate><description>{escape(excerpt(r['head'], 300))}</description></item>")
    xml = ('<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
           f"<title>{escape(s['title'])}</title><link>{escape(b + story_path(s))}</link>"
           f"<description>{escape(excerpt(s['description'], 300))}</description><language>vi</language>"
           + "".join(items) + "</channel></rss>")
    return Response(xml, media_type="application/rss+xml; charset=utf-8")


@router.get("/sitemap.xml")
def sitemap_index(request: Request):
    b = base_url(request)
    rows = q(f"SELECT s.id, s.last_chapter_at FROM stories s WHERE {PUBLIC} ORDER BY s.id")
    parts = [f"<sitemap><loc>{b}/sitemap-truyen.xml</loc></sitemap>"]
    for r in rows:
        parts.append(f"<sitemap><loc>{b}/sitemap-chuong-{r['id']}.xml</loc><lastmod>{iso(r['last_chapter_at'])}</lastmod></sitemap>")
    xml = ('<?xml version="1.0" encoding="UTF-8"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
           + "".join(parts) + "</sitemapindex>")
    return Response(xml, media_type="application/xml")


@router.get("/sitemap-truyen.xml")
def sitemap_stories(request: Request):
    b = base_url(request)
    rows = q(f"SELECT s.slug, s.updated_at, s.last_chapter_at FROM stories s WHERE {PUBLIC} ORDER BY s.id")
    urls = [f"<url><loc>{b}/</loc><changefreq>hourly</changefreq></url>"]
    for g in all_genres():
        urls.append(f"<url><loc>{b}/the-loai/{g['slug']}</loc></url>")
    for r in rows:
        urls.append(f"<url><loc>{b}/truyen/{escape(r['slug'])}</loc><lastmod>{iso(max(r['updated_at'], r['last_chapter_at']))}</lastmod></url>")
    xml = ('<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
           + "".join(urls) + "</urlset>")
    return Response(xml, media_type="application/xml")


@router.get("/sitemap-chuong-{story_id:int}.xml")
def sitemap_chapters(request: Request, story_id: int):
    b = base_url(request)
    s = q1(f"SELECT s.id, s.slug FROM stories s WHERE s.id=? AND {PUBLIC}", (story_id,))
    if not s:
        raise HTTPException(404, "Không tìm thấy")
    rows = q(f"SELECT c.n, c.updated_at FROM chapters c WHERE c.story_id=? AND {VISIBLE} ORDER BY c.n LIMIT 50000",
             (story_id, now()))
    urls = [f"<url><loc>{b}/truyen/{escape(s['slug'])}/chuong-{r['n']}</loc><lastmod>{iso(r['updated_at'])}</lastmod></url>"
            for r in rows]
    xml = ('<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
           + "".join(urls) + "</urlset>")
    return Response(xml, media_type="application/xml")


@router.get("/robots.txt")
def robots(request: Request):
    b = base_url(request)
    return PlainTextResponse(
        "User-agent: *\nDisallow: /api/\nDisallow: /quan-tri\nDisallow: /dang-truyen\nDisallow: /nhap-truyen\n"
        f"Disallow: /tai-khoan\nDisallow: /tu-truyen\n\nSitemap: {b}/sitemap.xml\n")


@router.get("/favicon.ico")
def favicon():
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/static/img/favicon.svg", status_code=301)
