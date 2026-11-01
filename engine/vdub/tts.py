"""Bước 4: đọc phụ đề tiếng Việt thành giọng nói và khớp vào đúng timeline.

Giọng (option "voice"):
  - Edge TTS (online, miễn phí):  "vi-VN-HoaiMyNeural" hoặc có chỉnh "vi-VN-HoaiMyNeural|+25Hz|+10%" (cao độ|tốc độ)
  - VieNeu-TTS (chạy trên máy):   "vieneu:Trúc Ly"
"""
import asyncio
import hashlib
import json
import subprocess
import threading

import numpy as np

from .common import FFMPEG, NO_WINDOW, ROOT, Project, log, progress, run_ffmpeg
from . import translate

STEP = "tts"
SR = 24000
GAP = 0.05          # khoảng nghỉ tối thiểu giữa hai câu lồng tiếng
SPILL = 1.5         # cho phép đọc lố quá điểm kết thúc câu gốc tối đa 1.5s (nếu câu sau chưa bắt đầu)
CONCURRENCY = 6
VIENEU_PY = ROOT / "engine" / ".venv_tts" / "Scripts" / "python.exe"
VIENEU_WORKER = ROOT / "engine" / "vieneu_worker.py"


# ---------- Edge TTS ----------

class EdgeVoice:
    concurrency = CONCURRENCY

    def __init__(self, spec):
        parts = spec.split("|")
        self.name = parts[0]
        self.pitch = parts[1] if len(parts) > 1 and parts[1] else "+0Hz"
        self.rate = int(parts[2].rstrip("%")) if len(parts) > 2 and parts[2] else 0

    async def synth(self, text, speed, out):
        import edge_tts
        rate = self.rate + round((speed - 1) * 100)
        for n in range(4):
            try:
                await edge_tts.Communicate(text, self.name, rate=f"{rate:+d}%", pitch=self.pitch).save(str(out))
                return
            except Exception as e:
                if n == 3:
                    raise RuntimeError(f"TTS lỗi ở câu '{text[:40]}': {e}")
                await asyncio.sleep(2 * (n + 1))

    def close(self):
        pass


# ---------- VieNeu-TTS (tiến trình riêng, nạp mô hình 1 lần) ----------

class VieneuVoice:
    concurrency = 1  # mô hình chạy CPU, mỗi lần một câu

    def __init__(self, name):
        if not VIENEU_PY.exists():
            raise RuntimeError("Chưa cài VieNeu-TTS (thiếu engine/.venv_tts)")
        self.name = name
        self.lock = threading.Lock()
        self.proc = None

    def _start(self):
        """Chỉ nạp mô hình khi thật sự có câu cần đọc (các câu không đổi thì dùng lại file cũ)."""
        progress(STEP, 0, "Đang nạp mô hình giọng VieNeu (khoảng 30 giây)...")
        self.proc = subprocess.Popen([str(VIENEU_PY), str(VIENEU_WORKER)], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                     encoding="utf-8", creationflags=NO_WINDOW)
        first = self._read()
        if not first.get("ready"):
            raise RuntimeError(first.get("error") or "VieNeu-TTS không khởi động được")

    def _read(self):
        for line in self.proc.stdout:
            line = line.strip()
            if line.startswith("{"):
                return json.loads(line)
        raise RuntimeError("VieNeu-TTS bị dừng đột ngột")

    def _synth_blocking(self, text, speed, out):
        raw = out.with_suffix(".raw.wav")
        with self.lock:
            if self.proc is None:
                self._start()
            self.proc.stdin.write(json.dumps({"text": text, "voice": self.name, "out": str(raw)}) + "\n")
            self.proc.stdin.flush()
            r = self._read()
        if not r.get("ok"):
            raise RuntimeError(f"VieNeu lỗi ở câu '{text[:40]}': {r.get('error')}")
        # VieNeu không có tham số tốc độ → tăng tốc bằng atempo (giữ nguyên cao độ)
        af = ["-af", f"atempo={speed:.3f}"] if speed > 1.001 else []
        run_ffmpeg(["-i", raw, *af, "-ac", "1", "-ar", str(SR), "-b:a", "96k", out])
        raw.unlink(missing_ok=True)

    async def synth(self, text, speed, out):
        await asyncio.to_thread(self._synth_blocking, text, speed, out)

    def close(self):
        if self.proc is None:
            return
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()


def make_voice(spec):
    return VieneuVoice(spec[len("vieneu:"):]) if spec.startswith("vieneu:") else EdgeVoice(spec)


def _decode(path):
    cmd = [str(FFMPEG), "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "1", "-ar", str(SR), "pipe:1"]
    r = subprocess.run(cmd, capture_output=True, creationflags=NO_WINDOW)
    if r.returncode:
        raise RuntimeError(f"Không giải mã được {path.name}")
    a = np.frombuffer(r.stdout, dtype=np.float32)
    nz = np.flatnonzero(np.abs(a) > 0.01)  # cắt khoảng lặng đầu/cuối của TTS
    if len(nz) == 0:
        return a[:0]
    pad = int(0.03 * SR)
    return a[max(0, nz[0] - pad): nz[-1] + pad]


def _avail(segs, i, duration):
    s = segs[i]
    nxt = segs[i + 1]["start"] if i + 1 < len(segs) else duration
    return max(0.4, min(nxt - GAP, s["end"] + SPILL) - s["start"])


async def _process(proj, segs, i, voice, max_speed, ai, sem, engine):
    s = segs[i]
    text = s.get("vi", "").strip()
    if not text:
        s.update(tts="", tts_dur=0, fit="empty")
        return
    avail = _avail(segs, i, proj.meta["duration"])
    key = hashlib.md5(f"{text}|{voice}|{max_speed}|{avail:.2f}".encode()).hexdigest()[:12]
    out = proj.path(f"tts/{s['id']:04d}.mp3")
    if s.get("tts_key") == key and out.exists():
        return  # câu này không đổi so với lần trước

    async with sem:
        await engine.synth(text, 1.0, out)
        dur = len(await asyncio.to_thread(_decode, out)) / SR
        ratio = dur / avail
        if ratio > max_speed and ai:
            # dài quá: nhờ AI nói gọn lại
            target = max(6, int(len(text) * max_speed / ratio * 0.95))
            short = await asyncio.to_thread(translate.shorten, ai, s["text"], text, target)
            if short and len(short) < len(text):
                text = s["vi"] = short
                await engine.synth(text, 1.0, out)
                dur = len(await asyncio.to_thread(_decode, out)) / SR
                ratio = dur / avail
        if ratio > 1.02:
            await engine.synth(text, min(ratio, max_speed) + 0.01, out)
            dur = len(await asyncio.to_thread(_decode, out)) / SR
            ratio = dur / avail

    s["tts_dur"] = round(dur, 3)
    s["fit"] = "ok" if ratio <= 1.05 else "over"
    # tính key theo text cuối cùng để lần sau không chạy lại
    s["tts_key"] = hashlib.md5(f"{text}|{voice}|{max_speed}|{avail:.2f}".encode()).hexdigest()[:12]


async def _run_all(proj, segs, voice, max_speed, ai, engine):
    sem = asyncio.Semaphore(engine.concurrency)
    tasks = [asyncio.create_task(_process(proj, segs, i, voice, max_speed, ai, sem, engine))
             for i in range(len(segs))]
    done = 0
    for t in asyncio.as_completed(tasks):
        await t
        done += 1
        progress(STEP, done / len(tasks) * 90, f"Đã đọc {done}/{len(tasks)} câu")


def assemble(proj: Project, segs):
    """Ghép các câu thành 1 track giọng đọc dài bằng video. Câu lố giờ sẽ đẩy câu sau lùi lại một chút."""
    import soundfile as sf
    track = np.zeros(int(proj.meta["duration"] * SR) + SR, dtype=np.float32)
    cursor = 0.0
    for s in segs:
        f = proj.path(f"tts/{s['id']:04d}.mp3")
        if not s.get("vi", "").strip() or not f.exists():
            s.pop("v_start", None)
            continue
        clip = _decode(f)
        start = max(s["start"], cursor)
        a = int(start * SR)
        clip = clip[: max(0, len(track) - a)]
        track[a:a + len(clip)] += clip
        s["v_start"] = round(start, 3)
        s["v_end"] = round(start + len(clip) / SR, 3)
        cursor = s["v_end"] + GAP
    track = track[: int(proj.meta["duration"] * SR)]
    sf.write(str(proj.path("dub_voice.wav")), np.clip(track, -1, 1), SR, subtype="PCM_16")


PREVIEW_TEXT = ("Trời ơi, món này vừa đẹp vừa xịn luôn nha! Dùng một lần là mê, "
                "đang giảm giá nữa, cứ chốt ngay nhé các bạn!")


def preview(spec):
    """Đọc thử một câu mẫu bằng giọng [spec]; có sẵn rồi thì dùng lại. Trả về đường dẫn file."""
    folder = ROOT / "tools" / "previews"
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / (hashlib.md5(f"{spec}|{PREVIEW_TEXT}".encode()).hexdigest()[:12] + ".mp3")
    if not out.exists():
        engine = make_voice(spec)
        try:
            asyncio.run(engine.synth(PREVIEW_TEXT, 1.0, out))
        finally:
            engine.close()
    return str(out)


def run(proj: Project, settings):
    segs = proj.segments()
    if not any(s.get("vi") for s in segs):
        raise RuntimeError("Chưa có phụ đề tiếng Việt, hãy chạy bước dịch trước")
    proj.path("tts").mkdir(exist_ok=True)
    voice = proj.opt["voice"]
    ai = None
    if settings.get("gemini_api_key"):
        try:
            ai = translate.Gemini(settings)
        except Exception as e:
            log(f"Không dùng được Gemini để rút gọn câu: {e}")
    progress(STEP, 0, "Đang đọc lời thoại...")
    engine = make_voice(voice)
    try:
        asyncio.run(_run_all(proj, segs, voice, float(proj.opt["max_speed"]), ai, engine))
    finally:
        engine.close()
    proj.save_segments(segs)

    progress(STEP, 92, "Đang ghép giọng đọc theo timeline...")
    assemble(proj, segs)
    proj.save_segments(segs)
    over = sum(1 for s in segs if s.get("fit") == "over")
    if over:
        log(f"Có {over} câu vẫn dài hơn khung giờ (đánh dấu đỏ) - nên sửa ngắn lại rồi lồng tiếng lại")
    proj.mark(STEP)
    progress(STEP, 100, "Xong")
