"""Tiện ích dùng chung: đường dẫn công cụ, gửi tiến độ cho app, gọi ffmpeg, quản lý project."""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent  # thư mục video_dub
TOOLS = ROOT / "tools"
FFMPEG = TOOLS / "ffmpeg" / "ffmpeg.exe"
FFPROBE = TOOLS / "ffmpeg" / "ffprobe.exe"
MODELS = TOOLS / "models"

os.environ.setdefault("HF_HOME", str(MODELS / "hf"))
os.environ.setdefault("TORCH_HOME", str(MODELS / "torch"))
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


# ---------- giao tiếp với app: mỗi dòng stdout là một JSON ----------

def emit(kind, **kw):
    sys.stdout.write(json.dumps({"type": kind, **kw}) + "\n")
    sys.stdout.flush()


def progress(step, pct, msg=""):
    emit("progress", step=step, pct=round(max(0.0, min(100.0, pct)), 1), msg=msg)


def log(msg):
    emit("log", msg=msg)


# ---------- ffmpeg ----------

def run_ffmpeg(args, cwd=None):
    cmd = [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y", *map(str, args)]
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", creationflags=NO_WINDOW)
    if r.returncode:
        raise RuntimeError("ffmpeg lỗi: " + r.stderr.strip()[-1500:])


def run_ffmpeg_progress(args, duration, step, cwd=None):
    """Chạy ffmpeg và báo % dựa trên out_time của -progress."""
    cmd = [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y", "-nostats",
           "-progress", "pipe:1", *map(str, args)]
    p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    last = 0.0
    for line in p.stdout:
        if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
            try:
                t = int(line.split("=")[1]) / 1e6
            except ValueError:
                continue
            if duration and time.time() - last > 0.5:
                last = time.time()
                progress(step, t / duration * 100)
    err = p.stderr.read()
    if p.wait():
        raise RuntimeError("ffmpeg lỗi: " + err.strip()[-1500:])


def probe(path):
    cmd = [str(FFPROBE), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       creationflags=NO_WINDOW)
    if r.returncode:
        raise RuntimeError(f"Không đọc được video: {r.stderr.strip()}")
    info = json.loads(r.stdout)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    if v is None:
        raise RuntimeError("File không có hình (không phải video)")
    return {
        "duration": float(info["format"].get("duration", 0)),
        "width": int(v["width"]),
        "height": int(v["height"]),
        "has_audio": a is not None,
        "vcodec": v.get("codec_name", ""),
        "color_transfer": v.get("color_transfer", ""),
        "hdr": v.get("color_transfer", "") in HDR_TRANSFERS,
    }


HDR_TRANSFERS = ("arib-std-b67", "smpte2084")  # HLG (iPhone...) và HDR10/PQ


def sdr_filter(color_transfer):
    """Bộ lọc đổi HDR (BT.2020) về SDR thường (BT.709), nếu không đổi thì màu bị bạc, sáng trưng khi xem ở Windows/web."""
    tin = color_transfer if color_transfer in HDR_TRANSFERS else "arib-std-b67"
    return (f"zscale=tin={tin}:t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
            "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p")


def ensure_h264(path, step="download"):
    """Video nén HEVC/AV1/VP9 không phát được trên trình xem mặc định của Windows (và dễ lỗi khi đăng); video HDR
    thì màu bị lệch: đổi sang H.264 + SDR. Trả về đường dẫn file dùng được."""
    path = Path(path)
    info = probe(path)
    codec = info["vcodec"]
    if codec == "h264" and not info["hdr"]:
        return path
    log(f"Video {'HDR ' if info['hdr'] else ''}nén {codec.upper()} → đang đổi sang H.264"
        f"{' + màu thường (SDR)' if info['hdr'] else ''}...")
    base = re.sub(r"(_h264|_sdr)+$", "", path.stem)
    out = path.with_name(base + "_h264.mp4")
    if out == path:  # đang xử lý lại chính file đã đổi trước đó
        out = path.with_name(base + "_sdr.mp4")
    dur = info["duration"]
    vf = ["-vf", sdr_filter(info["color_transfer"])] if info["hdr"] else []
    run_ffmpeg_progress(["-i", path, *vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
                         *(["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"] if info["hdr"] else []),
                         "-c:a", "copy", "-movflags", "+faststart", out], dur, step)
    new = probe(out)
    if new["vcodec"] != "h264" or new["hdr"] or abs(new["duration"] - dur) > 1:
        out.unlink(missing_ok=True)
        raise RuntimeError("Đổi sang H.264 không thành công, giữ nguyên file gốc")
    path.unlink(missing_ok=True)
    return out


def setup_cuda_dlls():
    """Cho ctranslate2 (faster-whisper) dùng chung cuDNN/cuBLAS đi kèm torch."""
    try:
        import torch
        lib = Path(torch.__file__).parent / "lib"
        if lib.exists():
            os.add_dll_directory(str(lib))
            os.environ["PATH"] = str(lib) + os.pathsep + os.environ.get("PATH", "")
        return torch.cuda.is_available()
    except Exception:
        return False


# ---------- settings & project ----------

DEFAULT_SETTINGS = {
    "gemini_api_key": "",
    "gemini_model": "gemini-flash-latest",
    "whisper_model": "large-v3-turbo",
    "youtube_client_secret": "",
    "facebook_page_id": "",
    "facebook_page_token": "",
    "graph_version": "v23.0",
    "cookies_file": "",  # cookies.txt xuất từ trình duyệt, cho trang đòi cookie (Douyin...)
}

DEFAULT_OPTIONS = {
    "source_lang": "auto",
    "voice": "vieneu:Trúc Ly",
    "max_speed": 1.35,
    "keep_background": True,
    "bg_volume": 1.0,
    "voice_volume": 1.0,
    "music": "",
    "music_volume": 0.15,
    "burn_subs": True,
    "sub_font_size": 0,  # 0 = tự tính theo chiều cao video
    "cover_subs": False,  # phủ dải màu lên phụ đề in cứng của video gốc
    "cover_top": 0.0,     # vị trí dải che, tính theo tỉ lệ chiều cao (0..1)
    "cover_bottom": 0.0,
    "cover_color": "FFD400",
    "cover_text_color": "000000",
    "translate_note": "",
    # "dub" = lồng tiếng lời thoại; "review" = AI xem video và viết lời kể
    "mode": "dub",
    "multi_voice": False,      # lồng tiếng phim: mỗi nhân vật một giọng
    "review_style": "Kể chuyện hấp dẫn, giọng văn review phim trên TikTok",
    "review_minutes": 0,       # 0 = kể trên toàn bộ clip; >0 = cắt ghép cảnh chính thành ~N phút
    "review_note": "",
    "orig_volume": 0.3,        # review: âm lượng tiếng phim gốc (tự nhỏ lại khi có lời kể)
    "review_remove_voice": False,  # review: xóa thoại gốc, chỉ giữ nhạc/tiếng động phim
    "review_source": "ai",     # "ai" = AI xem video tự viết lời; "user" = người dùng tự nhập lời thuyết minh
    "review_user_script": "",  # lời thuyết minh người dùng nhập (review_source = "user")
    "fix_overlays": False,     # dò chữ Trung in trên video → dịch / xóa / thay bằng tên kênh (xem overlays.py)
    "channel_name": "",        # tên kênh: dùng cho chữ được chọn "thay bằng tên kênh" và cho chữ gắn ở góc
    "channel_watermark": False,  # gắn tên kênh ở góc video suốt cả video
    "channel_pos": "top-right",  # top-left | top-right | bottom-left | bottom-right
}


def wants_separate(opt):
    """Có cần tách giọng gốc khỏi nền (Demucs) không."""
    if opt.get("mode") == "review":
        return bool(opt.get("review_remove_voice"))
    return bool(opt.get("keep_background"))


# Các bước của từng chế độ (bước nào không cần theo option thì bỏ qua, xem steps_for)
PIPELINES = {
    "dub": ["transcribe", "speakers", "translate", "overlays", "separate", "tts", "render"],
    "review": ["script", "overlays", "separate", "tts", "render"],
}


def steps_for(opt):
    mode = opt.get("mode", "dub")
    skip = set()
    if not wants_separate(opt):
        skip.add("separate")
    if mode == "dub" and not opt.get("multi_voice"):
        skip.add("speakers")
    if not opt.get("fix_overlays"):
        skip.add("overlays")
    return [s for s in PIPELINES.get(mode, PIPELINES["dub"]) if s not in skip]

FEMALE_VOICES = ["vieneu:Trúc Ly", "vieneu:Mai Anh", "vieneu:Đoan Trang", "vieneu:Ngọc Huyền",
                 "vieneu:Quỳnh Anh", "vieneu:Kim Thanh", "vieneu:Thùy Dung", "vieneu:Ngọc Linh"]
MALE_VOICES = ["vieneu:Hải Đăng", "vieneu:Quốc Tuấn", "vieneu:Thiện Minh", "vieneu:Minh Đức",
               "vieneu:Adam", "vieneu:Minh Triết", "vieneu:Thái Sơn", "vieneu:Adam bựa"]


def load_settings(path):
    s = dict(DEFAULT_SETTINGS)
    if path and Path(path).exists():
        s.update(json.loads(Path(path).read_text(encoding="utf-8")))
    return s


class Project:
    def __init__(self, folder):
        self.dir = Path(folder)
        self.meta_file = self.dir / "project.json"
        self.seg_file = self.dir / "segments.json"
        if not self.meta_file.exists():
            raise RuntimeError(f"Không tìm thấy project: {folder}")
        self.meta = json.loads(self.meta_file.read_text(encoding="utf-8"))
        self.meta["options"] = {**DEFAULT_OPTIONS, **self.meta.get("options", {})}

    @staticmethod
    def create(video, workspace):
        video = Path(video).resolve()
        info = probe(video)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        folder = Path(workspace) / f"{video.stem[:40]}_{stamp}"
        folder.mkdir(parents=True, exist_ok=True)
        meta = {
            "name": video.stem,
            "video": str(video),
            "created": stamp,
            **info,
            "steps": {},
            "options": dict(DEFAULT_OPTIONS),
        }
        (folder / "project.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            run_ffmpeg(["-ss", min(1.0, info["duration"] / 2), "-i", video, "-frames:v", "1",
                        "-vf", "scale=480:-2", folder / "thumb.jpg"])
        except RuntimeError:
            pass
        return Project(folder)

    @property
    def opt(self):
        return self.meta["options"]

    @property
    def edited(self):
        """Chế độ tóm tắt phim: đã cắt ghép các cảnh chính thành edited.mp4."""
        return bool(self.meta.get("edited")) and self.path("edited.mp4").exists()

    @property
    def video(self):
        """Video đang làm việc: bản cắt ghép (nếu có) hoặc video gốc."""
        return str(self.path("edited.mp4")) if self.edited else self.meta["video"]

    @property
    def duration(self):
        return float(self.meta["edited_duration"]) if self.edited else float(self.meta["duration"])

    def path(self, name):
        return self.dir / name

    def save(self):
        self.meta_file.write_text(json.dumps(self.meta, ensure_ascii=False, indent=2), encoding="utf-8")

    def mark(self, step, status="done"):
        # đọc lại để không ghi đè option app vừa sửa trong lúc engine chạy
        fresh = json.loads(self.meta_file.read_text(encoding="utf-8"))
        fresh.setdefault("steps", {})[step] = status
        self.meta["steps"] = fresh["steps"]
        self.meta_file.write_text(json.dumps(fresh, ensure_ascii=False, indent=2), encoding="utf-8")

    def segments(self):
        if not self.seg_file.exists():
            return []
        return json.loads(self.seg_file.read_text(encoding="utf-8"))

    def save_segments(self, segs):
        tmp = self.seg_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(segs, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.seg_file)
