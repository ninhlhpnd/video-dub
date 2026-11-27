"""Mật khẩu (scrypt), phiên đăng nhập bằng cookie ký, CSRF kiểu double-submit, API key."""
import hashlib
import hmac
import secrets

from fastapi import HTTPException, Request
from itsdangerous import BadSignature, URLSafeTimedSerializer

from .config import settings
from .db import ex, now, q1

SESSION_COOKIE = "sid"
CSRF_COOKIE = "csrf"
SESSION_MAX_AGE = 60 * 60 * 24 * 60  # 60 ngày

SCRYPT = dict(n=2 ** 14, r=8, p=1, dklen=32)


def hash_password(password: str, salt: str | None = None):
    salt = salt or secrets.token_hex(16)
    h = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt), **SCRYPT)
    return h.hex(), salt


def verify_password(password: str, pw_hash: str, salt: str) -> bool:
    h, _ = hash_password(password, salt)
    return hmac.compare_digest(h, pw_hash)


def _serializer():
    return URLSafeTimedSerializer(settings.secret_key, salt="session")


def make_session(user) -> str:
    # gắn một phần hash mật khẩu: đổi mật khẩu là các phiên cũ mất hiệu lực
    return _serializer().dumps({"u": user["id"], "h": user["pw_hash"][:10]})


def read_session(token: str):
    if not token:
        return None
    try:
        data = _serializer().loads(token, max_age=SESSION_MAX_AGE)
    except BadSignature:
        return None
    user = q1("SELECT * FROM users WHERE id=?", (data.get("u"),))
    if not user or user["is_banned"] or user["pw_hash"][:10] != data.get("h"):
        return None
    return user


def set_login(response, user):
    response.set_cookie(SESSION_COOKIE, make_session(user), max_age=SESSION_MAX_AGE, httponly=True,
                        samesite="lax", secure=settings.cookie_secure, path="/")


def clear_login(response):
    response.delete_cookie(SESSION_COOKIE, path="/")


def current_user(request: Request):
    if not hasattr(request.state, "user"):
        request.state.user = read_session(request.cookies.get(SESSION_COOKIE, ""))
    return request.state.user


def require_user(request: Request):
    user = current_user(request)
    if not user:
        raise HTTPException(401, "Bạn cần đăng nhập")
    return user


def require_role(request: Request, *roles):
    user = require_user(request)
    if user["role"] not in roles and user["role"] != "admin":
        raise HTTPException(403, "Bạn không có quyền vào trang này")
    return user


def is_admin(user) -> bool:
    return bool(user) and user["role"] == "admin"


def can_import(user) -> bool:
    return bool(user) and user["role"] in ("uploader", "admin")


# ---- CSRF: cookie ngẫu nhiên + trường ẩn trong form phải trùng nhau ----

def csrf_token(request: Request) -> str:
    tok = request.cookies.get(CSRF_COOKIE) or getattr(request.state, "new_csrf", None)
    if not tok:
        tok = secrets.token_urlsafe(24)
        request.state.new_csrf = tok
    return tok


def check_csrf(request: Request, form):
    cookie = request.cookies.get(CSRF_COOKIE, "")
    sent = form.get("csrf", "") if form is not None else ""
    if not sent:
        sent = request.headers.get("x-csrf-token", "")
    if not cookie or not sent or not hmac.compare_digest(str(cookie), str(sent)):
        raise HTTPException(403, "Phiên làm việc hết hạn, hãy tải lại trang rồi thử lại")


# ---- API key cho ứng dụng máy tính ----

def new_api_key() -> str:
    return "vd_" + secrets.token_urlsafe(30)


def regenerate_api_key(user_id: int) -> str:
    key = new_api_key()
    ex("UPDATE users SET api_key=? WHERE id=?", (key, user_id))
    return key


def api_user(request: Request):
    key = request.headers.get("x-api-key", "").strip()
    if not key:
        raise HTTPException(401, "Thiếu header X-Api-Key")
    user = q1("SELECT * FROM users WHERE api_key=?", (key,))
    if not user:
        raise HTTPException(401, "API key không đúng")
    if user["is_banned"]:
        raise HTTPException(403, "Tài khoản đã bị khoá")
    return user


def create_user(username: str, password: str, display_name: str = "", email: str = "", role: str | None = None):
    """Người đăng ký đầu tiên thành admin."""
    from .db import tx, val
    pw_hash, salt = hash_password(password)
    with tx() as conn:
        first = not val("SELECT COUNT(*) FROM users", conn=conn)
        role = role or ("admin" if first else "reader")
        cur = conn.execute(
            "INSERT INTO users(username, display_name, email, pw_hash, pw_salt, role, api_key, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (username, display_name or username, email, pw_hash, salt, role, new_api_key(), now()))
    return q1("SELECT * FROM users WHERE id=?", (cur.lastrowid,))
