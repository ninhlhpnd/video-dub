"""Bước 3: tách giọng nói gốc ra khỏi nhạc/tiếng động nền (Demucs), giữ lại phần nền."""
import numpy as np

from .common import Project, log, progress, run_ffmpeg

STEP = "separate"
CHUNK_SEC = 300  # xử lý từng đoạn 5 phút để không tràn VRAM với video dài


def run(proj: Project, settings):
    import soundfile as sf
    import torch
    from demucs.apply import apply_model
    from demucs.pretrained import get_model

    src = proj.path("audio44.wav")
    progress(STEP, 0, "Đang tách âm thanh...")
    run_ffmpeg(["-i", proj.meta["video"], "-vn", "-ac", "2", "-ar", "44100", src])

    progress(STEP, 2, "Đang nạp mô hình tách giọng (lần đầu tải ~80MB)...")
    model = get_model("htdemucs")
    model.eval()
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model.to(device)
    voc = model.sources.index("vocals")

    wav, sr = sf.read(str(src), dtype="float32", always_2d=True)
    total = len(wav)
    bg = np.zeros_like(wav)
    chunk = CHUNK_SEC * sr
    for i in range(0, total, chunk):
        part = torch.from_numpy(wav[i:i + chunk].T.copy())
        ref = part.mean(0)
        m, s = ref.mean(), ref.std() + 1e-8
        x = ((part - m) / s)[None]
        try:
            with torch.no_grad():
                out = apply_model(model, x.to(device), device=device, split=True, overlap=0.25, progress=False)[0]
        except torch.cuda.OutOfMemoryError:
            log("GPU hết bộ nhớ, chuyển sang CPU (chậm hơn)")
            device = "cpu"
            model.to(device)
            torch.cuda.empty_cache()
            with torch.no_grad():
                out = apply_model(model, x, device=device, split=True, overlap=0.25, progress=False)[0]
        out = out * s + m
        rest = out.sum(0) - out[voc]
        bg[i:i + chunk] = rest.cpu().numpy().T
        progress(STEP, 5 + min(i + chunk, total) / total * 94)

    sf.write(str(proj.path("background.wav")), bg, sr, subtype="PCM_16")
    src.unlink(missing_ok=True)
    proj.mark(STEP)
    progress(STEP, 100, "Xong")
