"""Tải trang web truyện Trung: UA thật, thời gian chờ, thử lại, giãn cách giữa các lần tải."""
import ipaddress
import random
import re
import socket
import threading
import time
from urllib.parse import urlparse

import httpx

from ..config import settings
from .text import decode_bytes

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/142.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
}

_META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_\-]+)""", re.I)


class FetchError(RuntimeError):
    pass


def check_public_url(url: str):
    """Chỉ cho tải http(s) tới máy chủ công khai (chặn localhost/mạng nội bộ)."""
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise FetchError("Link phải bắt đầu bằng http:// hoặc https://")
    try:
        infos = socket.getaddrinfo(p.hostname, None)
    except socket.gaierror:
        raise FetchError(f"Không tìm thấy tên miền {p.hostname}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise FetchError("Không được tải từ địa chỉ mạng nội bộ")


def decode_response(content: bytes, content_type: str = "") -> str:
    hint = None
    m = re.search(r"charset=([\w\-]+)", content_type or "", re.I)
    if m:
        hint = m.group(1)
    else:
        m = _META_CHARSET.search(content[:4096])
        if m:
            hint = m.group(1).decode("ascii", "ignore")
    return decode_bytes(content, hint)


class Fetcher:
    """Dùng chung một client; tự giãn cách `delay` giây (± ngẫu nhiên) giữa các lần tải."""

    def __init__(self, delay: float | None = None, retries: int = 3, timeout: float = 25.0, check_url=True):
        self.delay = settings.fetch_delay if delay is None else delay
        self.retries = retries
        self.check_url = check_url
        self.client = httpx.Client(headers=HEADERS, timeout=timeout, follow_redirects=True)
        self._last = 0.0
        self._lock = threading.Lock()

    def _wait(self):
        if self.delay <= 0:
            return
        gap = self.delay + random.uniform(0, self.delay * 0.6)
        d = self._last + gap - time.time()
        if d > 0:
            time.sleep(d)

    def get(self, url: str, referer: str | None = None) -> str:
        if self.check_url:
            check_public_url(url)
        last_err = None
        for attempt in range(self.retries):
            with self._lock:
                self._wait()
                try:
                    headers = {"Referer": referer} if referer else {}
                    r = self.client.get(url, headers=headers)
                    self._last = time.time()
                except httpx.HTTPError as e:
                    self._last = time.time()
                    last_err = f"lỗi mạng: {e.__class__.__name__}"
                    time.sleep(2 * (attempt + 1))
                    continue
            if r.status_code == 200:
                return decode_response(r.content, r.headers.get("content-type", ""))
            last_err = f"HTTP {r.status_code}"
            if r.status_code in (403, 404, 410):
                break
            time.sleep(3 * (attempt + 1))
        raise FetchError(f"Không tải được {url}: {last_err}")

    def close(self):
        self.client.close()
