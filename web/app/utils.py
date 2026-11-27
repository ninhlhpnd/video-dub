"""Hàm tiện ích: bỏ dấu, slug, đoạn văn, thời gian, phân trang."""
import math
import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone

VN_TZ = timezone(timedelta(hours=7))


def unaccent(s: str) -> str:
    s = (s or "").replace("đ", "d").replace("Đ", "D")
    s = unicodedata.normalize("NFD", s)
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def norm(s: str) -> str:
    """Chuỗi dùng để tìm kiếm: thường, bỏ dấu, gộp khoảng trắng."""
    s = unaccent(s).lower()
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def slugify(s: str, max_len=80) -> str:
    s = norm(s)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    if len(s) > max_len:
        s = s[:max_len].rsplit("-", 1)[0]
    return s or "truyen"


def unique_slug(base: str, exists) -> str:
    slug, i = base, 2
    while exists(slug):
        slug = f"{base}-{i}"
        i += 1
    return slug


def normalize_content(text: str) -> str:
    """Chuẩn hoá nội dung chương: đoạn văn cách nhau bởi một dòng trống."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").replace("　", " ")
    text = text.replace("﻿", "")
    if re.search(r"\n\s*\n", text):
        blocks = re.split(r"\n\s*\n", text)
        paras = [re.sub(r"\s*\n\s*", " ", b).strip() for b in blocks]
    else:
        paras = [ln.strip() for ln in text.split("\n")]
    return "\n\n".join(p for p in paras if p)


def paragraphs(content: str):
    return [p for p in (content or "").split("\n\n") if p.strip()]


def word_count(text: str) -> int:
    return len(re.findall(r"\w+", text or ""))


def today(ts=None) -> str:
    return datetime.fromtimestamp(ts or time.time(), VN_TZ).strftime("%Y-%m-%d")


def day_offset(days: int) -> str:
    return (datetime.now(VN_TZ) - timedelta(days=days)).strftime("%Y-%m-%d")


def fmt_time(ts) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(ts, VN_TZ).strftime("%d/%m/%Y %H:%M")


def fmt_date(ts) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(ts, VN_TZ).strftime("%d/%m/%Y")


def iso(ts) -> str:
    return datetime.fromtimestamp(ts or 0, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def rfc822(ts) -> str:
    return datetime.fromtimestamp(ts or 0, timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")


def time_ago(ts) -> str:
    if not ts:
        return ""
    d = int(time.time()) - int(ts)
    if d < 60:
        return "vừa xong"
    if d < 3600:
        return f"{d // 60} phút trước"
    if d < 86400:
        return f"{d // 3600} giờ trước"
    if d < 86400 * 30:
        return f"{d // 86400} ngày trước"
    return fmt_date(ts)


def fmt_num(n) -> str:
    n = int(n or 0)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}".rstrip("0").rstrip(".").replace(".", ",") + "M"
    if n >= 10_000:
        return f"{n / 1000:.1f}".rstrip("0").rstrip(".").replace(".", ",") + "K"
    return f"{n:,}".replace(",", ".")


def fmt_money(n) -> str:
    return f"{int(n or 0):,}".replace(",", ".") + "đ"


def parse_dt_local(s: str):
    """'2026-12-01T20:00' (giờ VN) → timestamp; rỗng → None."""
    s = (s or "").strip()
    if not s:
        return None
    try:
        dt = datetime.strptime(s[:16], "%Y-%m-%dT%H:%M").replace(tzinfo=VN_TZ)
        return int(dt.timestamp())
    except ValueError:
        return None


def dt_local(ts) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(ts, VN_TZ).strftime("%Y-%m-%dT%H:%M")


def to_int(v, default=0, lo=None, hi=None):
    try:
        x = int(v)
    except (TypeError, ValueError):
        return default
    if lo is not None and x < lo:
        x = lo
    if hi is not None and x > hi:
        x = hi
    return x


class Page:
    def __init__(self, page, per_page, total):
        self.per_page = per_page
        self.total = total
        self.pages = max(1, math.ceil(total / per_page))
        self.page = min(max(1, page), self.pages)
        self.offset = (self.page - 1) * per_page

    def window(self, size=2):
        """Dãy số trang có dấu … (None) cho thanh phân trang."""
        out, last = [], 0
        for p in range(1, self.pages + 1):
            if p in (1, self.pages) or abs(p - self.page) <= size:
                if last and p - last > 1:
                    out.append(None)
                out.append(p)
                last = p
        return out


def excerpt(text: str, n=160) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + "…"
