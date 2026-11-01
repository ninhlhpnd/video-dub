"""Đăng video lên YouTube / Trang Facebook, có hẹn giờ công bố (nền tảng tự đăng, không cần bật máy)."""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from .common import TOOLS, log, progress

YT_TOKEN = TOOLS / "youtube_token.json"
YT_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def _parse_time(s):
    """Nhận ISO 8601 (có hoặc không có múi giờ; không có thì hiểu là giờ máy). Trả về datetime UTC hoặc None."""
    if not s:
        return None
    t = datetime.fromisoformat(s)
    if t.tzinfo is None:
        t = t.astimezone()
    return t.astimezone(timezone.utc)


def _yt_credentials(client_secret):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if YT_TOKEN.exists():
        creds = Credentials.from_authorized_user_file(str(YT_TOKEN), YT_SCOPES)
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception:
            creds = None
    if not creds or not creds.valid:
        if not client_secret or not Path(client_secret).exists():
            raise RuntimeError("Chưa chọn file client_secret.json của YouTube trong Cài đặt")
        log("Trình duyệt sẽ mở để đăng nhập Google và cấp quyền tải video...")
        flow = InstalledAppFlow.from_client_secrets_file(client_secret, YT_SCOPES)
        creds = flow.run_local_server(port=0, open_browser=True)
    YT_TOKEN.write_text(creds.to_json(), encoding="utf-8")
    return creds


def youtube(video, title, description, tags, publish_at, privacy, settings):
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    step = "youtube"
    when = _parse_time(publish_at)
    if when and when < datetime.now(timezone.utc) + timedelta(minutes=2):
        raise RuntimeError("Giờ đăng YouTube phải ở tương lai")
    yt = build("youtube", "v3", credentials=_yt_credentials(settings["youtube_client_secret"]), cache_discovery=False)
    status = {"selfDeclaredMadeForKids": False}
    if when:  # YouTube chỉ hẹn giờ khi video đang ở chế độ riêng tư
        status.update(privacyStatus="private", publishAt=when.strftime("%Y-%m-%dT%H:%M:%SZ"))
    else:
        status["privacyStatus"] = privacy or "public"
    body = {
        "snippet": {"title": title[:100], "description": description[:5000], "tags": tags,
                    "categoryId": "22", "defaultAudioLanguage": "vi"},
        "status": status,
    }
    media = MediaFileUpload(str(video), chunksize=8 * 1024 * 1024, resumable=True, mimetype="video/mp4")
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    resp = None
    while resp is None:
        st, resp = req.next_chunk()
        if st:
            progress(step, st.progress() * 100, "Đang tải lên YouTube...")
    url = f"https://youtu.be/{resp['id']}"
    progress(step, 100, f"Đã tải lên: {url}" + (f" - sẽ công bố lúc {publish_at}" if when else ""))
    return url


def facebook_pages(app_id, app_secret, user_token, version):
    """Đổi User Token ngắn hạn (1-2 giờ) sang dài hạn, rồi lấy các Trang bạn quản lý kèm Page Token.
    Page Token lấy từ User Token dài hạn thì không hết hạn (trừ khi đổi mật khẩu / gỡ quyền)."""
    api = f"https://graph.facebook.com/{version}"

    def get(path, **params):
        js = requests.get(f"{api}/{path}", params=params, timeout=30).json()
        if "error" in js:
            raise RuntimeError(f"Facebook lỗi: {js['error'].get('message')}")
        return js

    long_user = get("oauth/access_token", grant_type="fb_exchange_token", client_id=app_id.strip(),
                    client_secret=app_secret.strip(), fb_exchange_token=user_token.strip())["access_token"]
    perms = {p["permission"] for p in get("me/permissions", access_token=long_user)["data"]
             if p.get("status") == "granted"}
    missing = [p for p in ("pages_show_list", "pages_manage_posts", "pages_read_engagement") if p not in perms]
    if missing:
        raise RuntimeError("Token thiếu quyền: " + ", ".join(missing) + ". Hãy tick các quyền này rồi tạo token lại.")
    pages = get("me/accounts", access_token=long_user, fields="id,name,access_token,tasks", limit=100)["data"]
    if not pages:
        raise RuntimeError("Không thấy Trang nào. Khi tạo token, nhớ chọn Trang ở bước Facebook hỏi quyền.")
    return [{"id": p["id"], "name": p["name"], "token": p["access_token"],
             "can_post": "CREATE_CONTENT" in p.get("tasks", [])} for p in pages]


def facebook(video, title, description, publish_at, settings):
    step = "facebook"
    page, token = settings["facebook_page_id"].strip(), settings["facebook_page_token"].strip()
    if not page or not token:
        raise RuntimeError("Chưa nhập Page ID và Page Access Token của Facebook trong Cài đặt")
    when = _parse_time(publish_at)
    if when and not (timedelta(minutes=10) < when - datetime.now(timezone.utc) < timedelta(days=29)):
        raise RuntimeError("Facebook chỉ cho hẹn giờ từ 10 phút đến 29 ngày tới")
    api = f"https://graph-video.facebook.com/{settings['graph_version']}/{page}/videos"
    size = os.path.getsize(video)

    def call(data, files=None):
        r = requests.post(api, data={"access_token": token, **data}, files=files, timeout=600)
        js = r.json()
        if "error" in js:
            raise RuntimeError(f"Facebook lỗi: {js['error'].get('message')}")
        return js

    st = call({"upload_phase": "start", "file_size": size})
    session, vid = st["upload_session_id"], st["video_id"]
    start, end = int(st["start_offset"]), int(st["end_offset"])
    with open(video, "rb") as f:
        while start < end:
            f.seek(start)
            chunk = f.read(end - start)
            r = call({"upload_phase": "transfer", "upload_session_id": session, "start_offset": start},
                     files={"video_file_chunk": ("chunk", chunk, "application/octet-stream")})
            start, end = int(r["start_offset"]), int(r["end_offset"])
            progress(step, start / size * 100, "Đang tải lên Facebook...")
    fin = {"upload_phase": "finish", "upload_session_id": session, "title": title, "description": description}
    if when:
        fin.update(published="false", scheduled_publish_time=str(int(when.timestamp())))
    call(fin)
    url = f"https://www.facebook.com/{page}/videos/{vid}"
    progress(step, 100, f"Đã tải lên: {url}" + (f" - sẽ công bố lúc {publish_at}" if when else ""))
    return url
