"""Ứng dụng web đọc truyện: FastAPI + Jinja2 + SQLite."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.gzip import GZipMiddleware
from starlette.staticfiles import StaticFiles

from .auth import CSRF_COOKIE
from .config import settings
from .db import migrate
from .routes import account, admin, api, author, importer, public, social
from .templating import STATIC, redirect, render

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app):
    migrate()
    if settings.worker_enabled:
        from .worker import start_background, stop_background
        start_background()
        yield
        stop_background()
    else:
        yield


class CachedStatic(StaticFiles):
    def __init__(self, *a, max_age=604800, **kw):
        super().__init__(*a, **kw)
        self.max_age = max_age

    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = f"public, max-age={self.max_age}"
        return resp


def create_app() -> FastAPI:
    settings.ensure()
    app = FastAPI(title=settings.site_name, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(GZipMiddleware, minimum_size=800)

    @app.middleware("http")
    async def headers_mw(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        tok = getattr(request.state, "new_csrf", None)
        if tok and not request.cookies.get(CSRF_COOKIE):
            resp.set_cookie(CSRF_COOKIE, tok, max_age=60 * 60 * 24 * 30, httponly=False, samesite="lax",
                            secure=settings.cookie_secure, path="/")
        return resp

    app.mount("/static", CachedStatic(directory=str(STATIC)), name="static")
    # chỉ mở thư mục ảnh, KHÔNG mở cả data/ (có site.db, secret_key.txt)
    app.mount("/media/covers", CachedStatic(directory=str(settings.data_dir / "covers"), max_age=86400 * 30),
              name="covers")
    app.mount("/media/avatars", CachedStatic(directory=str(settings.data_dir / "avatars"), max_age=86400 * 30),
              name="avatars")

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)
        if exc.status_code == 401:
            return redirect(f"/dang-nhap?next={request.url.path}", "Bạn cần đăng nhập để tiếp tục")
        titles = {404: "Không tìm thấy trang", 403: "Không có quyền truy cập"}
        return render(request, "error.html", status=exc.status_code, code=exc.status_code,
                      title=titles.get(exc.status_code, "Có lỗi xảy ra"), message=exc.detail)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        errs = exc.errors()
        parts = []
        for e in errs[:3]:
            loc = ".".join(str(x) for x in e.get("loc", []) if x not in ("body", "query", "path"))
            parts.append(f"{loc}: {e.get('msg', '')}" if loc else e.get("msg", ""))
        msg = "Dữ liệu không hợp lệ: " + "; ".join(parts)
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": msg}, status_code=422)
        return render(request, "error.html", status=422, code=422, title="Dữ liệu không hợp lệ", message=msg)

    for r in (api.router, public.router, account.router, author.router, social.router, importer.router, admin.router):
        app.include_router(r)
    return app


app = create_app()
