"""Dò vị trí phụ đề in cứng (hardsub) của video gốc để phủ dải màu lên và đặt phụ đề Việt vào đó."""
import subprocess

import numpy as np

from .common import FFMPEG, NO_WINDOW, Project, progress, run_ffmpeg

STEP = "cover"
W = 360          # thu nhỏ khung hình để phân tích cho nhanh
SAMPLES = 24


def _frame(video, t, h):
    cmd = [str(FFMPEG), "-v", "error", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1",
           "-vf", f"scale={W}:{h}", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"]
    r = subprocess.run(cmd, capture_output=True, creationflags=NO_WINDOW)
    if r.returncode or len(r.stdout) < W * h:
        return None
    return np.frombuffer(r.stdout[:W * h], dtype=np.uint8).reshape(h, W).astype(np.int16)


def _text_rows(g):
    """Điểm 'giống chữ' của từng hàng: pixel sáng nằm sát pixel tối (chữ trắng viền đen) hoặc ngược lại."""
    bright, dark = g > 185, g < 75
    near_dark = np.zeros_like(dark)
    near_bright = np.zeros_like(bright)
    for d in (1, 2, 3):
        near_dark[:, d:] |= dark[:, :-d]
        near_dark[:, :-d] |= dark[:, d:]
        near_dark[d:, :] |= dark[:-d, :]
        near_bright[:, d:] |= bright[:, :-d]
        near_bright[:, :-d] |= bright[:, d:]
    # chỉ lấy 90% giữa khung, bỏ logo ở mép
    edge = (bright & near_dark) | (dark & near_bright)
    x0, x1 = int(W * 0.05), int(W * 0.95)
    # chữ có nhiều cạnh ngang: đếm số lần đổi trạng thái theo chiều ngang
    flips = np.abs(np.diff(edge[:, x0:x1].astype(np.int8), axis=1)).sum(1)
    return flips.astype(np.float32)


def detect(proj: Project):
    vw, vh = proj.meta["width"], proj.meta["height"]
    h = int(round(W * vh / vw / 2) * 2)
    dur = proj.meta["duration"]
    segs = proj.segments()
    # lấy khung hình ở giữa các câu thoại (lúc đó hardsub đang hiện), không có thì rải đều
    times = [(s["start"] + s["end"]) / 2 for s in segs] or []
    if len(times) < SAMPLES:
        times += list(np.linspace(dur * 0.05, dur * 0.95, SAMPLES - len(times)))
    times = sorted(times)[:: max(1, len(times) // SAMPLES)][:SAMPLES]

    profiles, frames = [], []
    for k, t in enumerate(times):
        g = _frame(proj.meta["video"], t, h)
        if g is not None:
            profiles.append(_text_rows(g))
            frames.append(t)
        progress(STEP, (k + 1) / len(times) * 90, "Đang dò vị trí phụ đề gốc...")
    if not profiles:
        raise RuntimeError("Không đọc được khung hình nào từ video")

    # chữ hardsub thay đổi nội dung nhưng giữ nguyên vị trí → dùng trung vị qua các khung
    P = np.stack(profiles)
    prof = np.median(P, axis=0)
    prof = np.convolve(prof, np.ones(5) / 5, mode="same")
    lo = int(h * 0.25)
    search = prof.copy()
    search[:lo] = 0
    search[int(h * 0.98):] = 0
    peak = int(np.argmax(search))
    found = search[peak] > 6  # đủ mạnh mới coi là có chữ
    if found:
        thr = search[peak] * 0.3
        a = b = peak
        while a > lo and search[a - 1] > thr:
            a -= 1
        while b < h - 1 and search[b + 1] > thr:
            b += 1
        pad = max(3, int((b - a) * 0.35))
        top, bottom = (a - pad) / h, (b + pad) / h
    else:
        top, bottom = 0.74, 0.86  # mặc định: vùng phụ đề thường gặp

    # dải phải đủ cao cho 2 dòng phụ đề Việt
    need = 2.6 * min(vw, vh) * 0.055 / vh
    if bottom - top < need:
        mid = (top + bottom) / 2
        top, bottom = mid - need / 2, mid + need / 2
    top, bottom = max(0.0, top), min(1.0, bottom)

    # ảnh xem trước: khung hình có nhiều chữ nhất trong vùng vừa tìm
    a, b = int(top * h), int(bottom * h)
    best = frames[int(np.argmax(P[:, a:b + 1].sum(1)))]
    run_ffmpeg(["-ss", f"{best:.2f}", "-i", proj.meta["video"], "-frames:v", "1",
                "-vf", "scale=480:-2", proj.path("cover_preview.jpg")])

    o = proj.meta["options"]
    o["cover_top"], o["cover_bottom"] = round(top, 4), round(bottom, 4)
    proj.save()
    progress(STEP, 100, "Đã tìm thấy phụ đề gốc" if found else "Không chắc vị trí, hãy kéo chỉnh vùng che")
    return {"top": top, "bottom": bottom, "found": bool(found)}
