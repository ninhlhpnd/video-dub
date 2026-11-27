"""Jinja2: môi trường, bộ lọc, hàm render trang và thông báo nhanh (flash)."""
import hashlib
import json
from pathlib import Path
from urllib.parse import quote, unquote

from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from . import utils
from .auth import CSRF_COOKIE, csrf_token, current_user
from .config import WEB_DIR, settings
from .donate import BANKS, PRESET_AMOUNTS, vietqr_url
from .services import ORIGIN_LABEL, ROLE_LABEL, STATUS_LABEL, base_url, chapter_path, cover_url, story_path

TEMPLATES = WEB_DIR / "templates"
STATIC = WEB_DIR / "static"

env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=select_autoescape(["html", "xml"]),
                  trim_blocks=True, lstrip_blocks=True)


def static_url(path: str) -> str:
    """Thêm ?v=<thời điểm sửa file> để trình duyệt tải lại khi file đổi (static được cache 7 ngày)."""
    try:
        v = int((STATIC / path).stat().st_mtime)
    except OSError:
        v = 0
    return f"/static/{path}?v={v}"


def tojson_attr(v):
    return Markup(json.dumps(v, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e")
                  .replace("&", "\\u0026").replace("'", "\\u0027"))


env.globals.update(
    static=static_url, settings=settings, STATUS_LABEL=STATUS_LABEL, ORIGIN_LABEL=ORIGIN_LABEL,
    ROLE_LABEL=ROLE_LABEL, BANKS=BANKS, PRESET_AMOUNTS=PRESET_AMOUNTS, story_path=story_path,
    chapter_path=chapter_path, cover_url=cover_url, paragraphs=utils.paragraphs,
)
env.filters.update(
    time_ago=utils.time_ago, fmt_time=utils.fmt_time, fmt_date=utils.fmt_date, num=utils.fmt_num,
    money=utils.fmt_money, excerpt=utils.excerpt, dt_local=utils.dt_local, tojson_attr=tojson_attr,
    urlq=lambda s: quote(str(s or ""), safe=""),
    hue=lambda s: int(hashlib.md5(str(s or "").encode()).hexdigest()[:4], 16) % 360,
)


def _nav_genres():
    from .db import q
    return q("SELECT name, slug FROM genres ORDER BY sort, name LIMIT 40")


def _site_setting(key, default=""):
    from .db import get_setting
    return get_setting(key, default)


env.globals.update(nav_genres=_nav_genres, site_setting=_site_setting, vietqr=vietqr_url)


def render(request, name, status=200, **ctx):
    user = current_user(request)
    flash = request.cookies.get("flash")
    ctx.setdefault("canonical", None)
    page = env.get_template(name).render(
        request=request, user=user, csrf=csrf_token(request), flash=unquote(flash) if flash else None,
        base=base_url(request), path=request.url.path, **ctx)
    resp = HTMLResponse(page, status_code=status)
    if flash:
        resp.delete_cookie("flash", path="/")
    return resp


def redirect(url: str, flash: str | None = None, status=303):
    resp = RedirectResponse(url, status_code=status)
    if flash:
        resp.set_cookie("flash", quote(flash), max_age=60, path="/", samesite="lax")
    return resp


def safe_next(url: str | None, default="/") -> str:
    """Chỉ cho chuyển hướng nội bộ."""
    if url and url.startswith("/") and not url.startswith("//") and "\\" not in url:
        return url
    return default


__all__ = ["render", "redirect", "safe_next", "env", "CSRF_COOKIE", "Path"]
