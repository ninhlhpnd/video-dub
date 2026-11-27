"""Tài khoản: đăng ký, đăng nhập, cài đặt, tủ truyện, trang cá nhân."""
import re
import time

from fastapi import APIRouter, HTTPException, Request

from ..auth import (check_csrf, clear_login, create_user, current_user, hash_password, regenerate_api_key,
                    require_user, set_login, verify_password)
from ..covers import remove_file, save_avatar
from ..db import ex, q, q1
from ..donate import BANK_BY_BIN
from ..templating import redirect, render, safe_next

router = APIRouter()

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.]{3,30}$")
_login_fail = {}  # ip → [thời điểm sai]


def _ip(request):
    from ..services import client_ip
    return client_ip(request)


def _too_many_fails(ip):
    t = time.time()
    fails = [x for x in _login_fail.get(ip, []) if t - x < 600]
    _login_fail[ip] = fails
    return len(fails) >= 10


@router.get("/dang-nhap")
def login_form(request: Request):
    if current_user(request):
        return redirect("/")
    return render(request, "auth_login.html", next=safe_next(request.query_params.get("next"), "/"))


@router.post("/dang-nhap")
async def login(request: Request):
    form = await request.form()
    check_csrf(request, form)
    username = str(form.get("username", "")).strip()
    password = str(form.get("password", ""))
    nxt = safe_next(form.get("next"), "/")
    ip = _ip(request)
    if _too_many_fails(ip):
        return render(request, "auth_login.html", status=429, error="Bạn nhập sai quá nhiều lần, hãy thử lại sau 10 phút.",
                      username=username, next=nxt)
    user = q1("SELECT * FROM users WHERE username=?", (username,))
    if not user or not verify_password(password, user["pw_hash"], user["pw_salt"]):
        _login_fail.setdefault(ip, []).append(time.time())
        return render(request, "auth_login.html", status=400, error="Sai tên đăng nhập hoặc mật khẩu.",
                      username=username, next=nxt)
    if user["is_banned"]:
        return render(request, "auth_login.html", status=403, error="Tài khoản này đã bị khoá.", username=username, next=nxt)
    resp = redirect(nxt, f"Chào mừng {user['display_name']} quay lại!")
    set_login(resp, user)
    return resp


@router.get("/dang-ky")
def register_form(request: Request):
    if current_user(request):
        return redirect("/")
    return render(request, "auth_register.html", next=safe_next(request.query_params.get("next"), "/"))


@router.post("/dang-ky")
async def register(request: Request):
    form = await request.form()
    check_csrf(request, form)
    username = str(form.get("username", "")).strip()
    display = str(form.get("display_name", "")).strip()[:50] or username
    email = str(form.get("email", "")).strip()[:120]
    password = str(form.get("password", ""))
    password2 = str(form.get("password2", ""))
    nxt = safe_next(form.get("next"), "/")
    err = None
    if not USERNAME_RE.match(username):
        err = "Tên đăng nhập 3-30 ký tự, chỉ gồm chữ không dấu, số, dấu chấm hoặc gạch dưới."
    elif len(password) < 6:
        err = "Mật khẩu cần ít nhất 6 ký tự."
    elif password != password2:
        err = "Hai lần nhập mật khẩu không khớp."
    elif q1("SELECT 1 FROM users WHERE username=?", (username,)):
        err = "Tên đăng nhập này đã có người dùng."
    if err:
        return render(request, "auth_register.html", status=400, error=err, username=username, display_name=display,
                      email=email, next=nxt)
    user = create_user(username, password, display, email)
    msg = "Đăng ký thành công!" + (" Bạn là thành viên đầu tiên nên được đặt làm quản trị viên." if user["role"] == "admin" else "")
    resp = redirect(nxt, msg)
    set_login(resp, user)
    return resp


@router.post("/dang-xuat")
async def logout(request: Request):
    form = await request.form()
    check_csrf(request, form)
    resp = redirect("/", "Đã đăng xuất")
    clear_login(resp)
    return resp


# ---------------------------------------------------------------- cài đặt tài khoản

@router.get("/tai-khoan")
def account_page(request: Request):
    user = require_user(request)
    return render(request, "account.html", tab=request.query_params.get("tab", "ho-so"), me=user)


@router.post("/tai-khoan")
async def account_save(request: Request):
    user = require_user(request)
    form = await request.form()
    check_csrf(request, form)
    action = form.get("action", "profile")
    if action == "profile":
        display = str(form.get("display_name", "")).strip()[:50] or user["username"]
        bio = str(form.get("bio", "")).strip()[:500]
        email = str(form.get("email", "")).strip()[:120]
        avatar = form.get("avatar")
        new_avatar = user["avatar"]
        if avatar is not None and getattr(avatar, "filename", ""):
            try:
                new_avatar = save_avatar(await avatar.read(), user["id"])
                remove_file("avatars", user["avatar"])
            except ValueError as e:
                return redirect("/tai-khoan", str(e))
        ex("UPDATE users SET display_name=?, bio=?, email=?, avatar=? WHERE id=?",
           (display, bio, email, new_avatar, user["id"]))
        return redirect("/tai-khoan", "Đã lưu hồ sơ")
    if action == "donate":
        bank = str(form.get("bank_bin", "")).strip()
        if bank and bank not in BANK_BY_BIN:
            bank = ""
        account = re.sub(r"[^0-9A-Za-z]", "", str(form.get("bank_account", "")))[:30]
        holder = str(form.get("bank_holder", "")).strip().upper()[:60]
        momo = re.sub(r"[^0-9]", "", str(form.get("momo", "")))[:12]
        paypal = str(form.get("paypal", "")).strip()[:200]
        kofi = str(form.get("kofi", "")).strip()[:200]
        for link in (paypal, kofi):
            if link and not link.startswith("https://"):
                return redirect("/tai-khoan?tab=ung-ho", "Link PayPal/Ko-fi phải bắt đầu bằng https://")
        ex("UPDATE users SET bank_bin=?, bank_account=?, bank_holder=?, momo=?, paypal=?, kofi=? WHERE id=?",
           (bank, account, holder, momo, paypal, kofi, user["id"]))
        return redirect("/tai-khoan?tab=ung-ho", "Đã lưu thông tin nhận ủng hộ")
    if action == "password":
        old, new, new2 = str(form.get("old", "")), str(form.get("new", "")), str(form.get("new2", ""))
        if not verify_password(old, user["pw_hash"], user["pw_salt"]):
            return redirect("/tai-khoan?tab=bao-mat", "Mật khẩu hiện tại không đúng")
        if len(new) < 6 or new != new2:
            return redirect("/tai-khoan?tab=bao-mat", "Mật khẩu mới cần ít nhất 6 ký tự và hai lần nhập phải khớp")
        h, salt = hash_password(new)
        ex("UPDATE users SET pw_hash=?, pw_salt=? WHERE id=?", (h, salt, user["id"]))
        resp = redirect("/tai-khoan?tab=bao-mat", "Đã đổi mật khẩu")
        set_login(resp, q1("SELECT * FROM users WHERE id=?", (user["id"],)))
        return resp
    if action == "apikey":
        regenerate_api_key(user["id"])
        return redirect("/tai-khoan?tab=api", "Đã tạo API key mới. Key cũ không dùng được nữa.")
    raise HTTPException(400, "Thao tác không hợp lệ")


# ---------------------------------------------------------------- tủ truyện

@router.get("/tu-truyen")
def bookshelf(request: Request):
    user = current_user(request)
    follows, history = [], []
    if user:
        follows = q("SELECT s.*, f.last_seen_n, (s.last_chapter_n - f.last_seen_n) AS new_count, h.chapter_n AS read_n "
                    "FROM follows f JOIN stories s ON s.id=f.story_id "
                    "LEFT JOIN reading_history h ON h.user_id=f.user_id AND h.story_id=f.story_id "
                    "WHERE f.user_id=? AND s.is_hidden=0 ORDER BY (s.last_chapter_n > f.last_seen_n) DESC, s.last_chapter_at DESC",
                    (user["id"],))
        history = q("SELECT s.*, h.chapter_n, h.updated_at AS read_at FROM reading_history h JOIN stories s ON s.id=h.story_id "
                    "WHERE h.user_id=? AND s.is_hidden=0 ORDER BY h.updated_at DESC LIMIT 60", (user["id"],))
    return render(request, "bookshelf.html", follows=follows, history=history, tab=request.query_params.get("tab", "theo-doi"))


@router.get("/thanh-vien/{username}")
def member(request: Request, username: str):
    m = q1("SELECT * FROM users WHERE username=?", (username,))
    if not m:
        raise HTTPException(404, "Không tìm thấy thành viên")
    stories = q("SELECT * FROM stories WHERE owner_id=? AND is_hidden=0 AND chapter_count>0 ORDER BY last_chapter_at DESC",
                (m["id"],))
    return render(request, "member.html", member=m, stories=stories)
