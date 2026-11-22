"""Tải video từ link (Douyin, Xiaohongshu/RedNote, TikTok, YouTube...) bằng yt-dlp rồi tạo project.

Dán nguyên đoạn chữ "chia sẻ" của app cũng được: tự lọc ra các link trong đó.
"""
import re
from pathlib import Path

from .common import FFMPEG, Project, ensure_h264, log, progress

STEP = "download"
URL_RE = re.compile(r"https?://[^\s　-〿＀-￯\"'<>]+")
BROWSERS = ("edge", "firefox", "chrome")


def extract_urls(text):
    return list(dict.fromkeys(u.rstrip(".,;)]}") for u in URL_RE.findall(text or "")))


XHS_NOTE_RE = re.compile(r"https?://(?:www\.)?(?:xiaohongshu|rednote)\.com/(?:explore|discovery/item|"
                         r"user/profile/[\da-f]+)/(?P<id>[\da-f]{24})(?P<query>\?[^#\s]*)?")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0 Safari/537.36")


def normalize_url(url):
    """Link RedNote rút gọn (xhslink.com), miền rednote.com hoặc link trang cá nhân → dạng yt-dlp hiểu
    (www.xiaohongshu.com/explore/<id>?xsec_token=...; thiếu xsec_token trang thường không trả video)."""
    if re.match(r"https?://(?:www\.)?xhslink\.com/", url):
        import requests
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=20, allow_redirects=True)
            url = r.url
        except Exception as e:
            log(f"Không mở được link rút gọn {url}: {e}")
            return url
    m = XHS_NOTE_RE.match(url)
    if m:
        return f"https://www.xiaohongshu.com/explore/{m['id']}{m['query'] or ''}"
    return url


class _QuietLogger:
    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        pass


def _fetch(url, folder, settings, idx, total, cookies_browser=None):
    import yt_dlp

    def hook(d):
        if d.get("status") == "downloading":
            size = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            pct = d.get("downloaded_bytes", 0) / size * 100 if size else 0
            progress(STEP, (idx + pct / 100) / total * 100, f"Đang tải video {idx + 1}/{total}: {pct:.0f}%")

    opts = {
        "outtmpl": str(folder / "%(extractor)s_%(id)s.%(ext)s"),
        # ưu tiên H.264 (phát được ở mọi nơi); không có thì lấy bản tốt nhất rồi đổi sang H.264 sau
        "format": "bv*[vcodec~='^(h264|avc)']+ba/bv*+ba/b",
        "merge_output_format": "mp4",
        "ffmpeg_location": str(FFMPEG.parent),
        "noplaylist": True,
        "progress_hooks": [hook],
        "logger": _QuietLogger(),
        "quiet": True,
        "noprogress": True,
        "retries": 3,
    }
    cookie_file = settings.get("cookies_file", "").strip()
    if cookie_file and Path(cookie_file).exists():
        opts["cookiefile"] = cookie_file
    if cookies_browser:
        opts["cookiesfrombrowser"] = (cookies_browser,)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if info.get("_type") == "playlist":
            info = (info.get("entries") or [None])[0] or {}
        path = Path(ydl.prepare_filename(info)).with_suffix(".mp4")
        if not path.exists():
            path = next(folder.glob(f"*{info.get('id', '')}*"), path)
        return path, info


def run(text, workspace, settings):
    """Tải mọi link trong [text]; trả về danh sách thư mục project vừa tạo."""
    from yt_dlp.utils import DownloadError

    urls = extract_urls(text)
    if not urls:
        raise RuntimeError("Không tìm thấy link nào trong nội dung đã dán")
    folder = Path(workspace) / "_downloads"
    folder.mkdir(parents=True, exist_ok=True)
    projects, errors = [], []
    for i, url in enumerate(urls):
        progress(STEP, i / len(urls) * 100, f"Đang lấy thông tin video {i + 1}/{len(urls)}...")
        path = info = None
        last = None
        url = normalize_url(url)
        # thử với cookies.txt (nếu có) trước; trang đòi cookie (Douyin) thì thử đọc cookie của trình duyệt
        need_cookie = False
        for browser in (None, *BROWSERS):
            if browser:
                log(f"Trang yêu cầu cookie, thử lấy cookie từ trình duyệt {browser}...")
            try:
                path, info = _fetch(url, folder, settings, i, len(urls), browser)
                break
            except DownloadError as e:
                last = str(e)
                need_cookie |= any(k in last.lower() for k in ("cookie", "login", "403", "verify", "captcha"))
                if not need_cookie:
                    break
            except Exception as e:  # lỗi đọc cookie trình duyệt (trình duyệt đang mở, chưa cài...)
                last = str(e)
        if path is None or not path.exists():
            msg = (last or "không tải được").replace("ERROR: ", "")
            if need_cookie:
                msg = ("Trang này cần cookie trình duyệt. Mở trang đó trên Chrome/Edge, xuất file cookies.txt "
                       "(tiện ích 'Get cookies.txt LOCALLY') rồi chọn file trong Cài đặt → Tải video từ link.")
            elif "No video formats" in msg:
                if "xiaohongshu" in url and not (settings.get("cookies_file") or "").strip():
                    # RedNote chuyển hướng sang trang đăng nhập khi không có cookie (hôm qua có thể vẫn tải được)
                    msg = ("RedNote đang yêu cầu đăng nhập nên không trả video. Đăng nhập xiaohongshu.com trên Chrome/Edge, "
                           "xuất file cookies.txt (tiện ích 'Get cookies.txt LOCALLY') rồi chọn file trong "
                           "Cài đặt → Tải video từ link.")
                elif "xiaohongshu" in url:
                    msg = ("RedNote không trả video: cookie đã hết hạn (xuất lại cookies.txt sau khi đăng nhập), "
                           "link đã hết hạn (copy link chia sẻ mới), hoặc bài này là bài ảnh/đã bị xóa.")
                else:
                    msg = "Bài viết này không có video (có thể là bài ảnh) hoặc đã bị xóa/ẩn."
            errors.append(f"{url}: {msg[:300]}")
            log(f"Không tải được {url}: {msg[:300]}")
            continue
        path = ensure_h264(path, STEP)
        p = Project.create(path, workspace)
        title = (info.get("title") or info.get("description") or "").strip().splitlines()[0] if info else ""
        if title:
            p.meta["name"] = title[:60]
        p.meta["source_url"] = url
        p.save()
        projects.append(str(p.dir))
        log(f"Đã tải: {p.meta['name']}")
    if not projects:
        raise RuntimeError("Không tải được video nào. " + " | ".join(errors))
    progress(STEP, 100, f"Đã tải {len(projects)}/{len(urls)} video")
    return projects, errors
