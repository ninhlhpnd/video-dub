"""Bước 1: tách âm thanh và nhận dạng lời nói (faster-whisper) thành các câu có mốc thời gian."""
from .common import Project, log, progress, run_ffmpeg, setup_cuda_dlls

STEP = "transcribe"
SENTENCE_END = tuple(".?!。？！…")
SOFT_BREAK = tuple(",，、;；:：")


def _resegment(words, max_dur=10.0, soft_dur=5.0, gap=0.7):
    """Gom từ thành câu: ngắt ở dấu chấm, khoảng lặng dài, hoặc khi câu quá dài."""
    out, cur = [], []

    def flush():
        if cur:
            text = "".join(w["w"] for w in cur).strip()
            if text:
                out.append({"start": round(cur[0]["s"], 3), "end": round(cur[-1]["e"], 3), "text": text})
            cur.clear()

    for w in words:
        if cur and (w["s"] - cur[-1]["e"] > gap or w["e"] - cur[0]["s"] > max_dur):
            flush()
        cur.append(w)
        tail = w["w"].strip()
        dur = w["e"] - cur[0]["s"]
        if (tail.endswith(SENTENCE_END) and dur > 0.8) or (tail.endswith(SOFT_BREAK) and dur > soft_dur):
            flush()
    flush()
    return out


def _load_model(name):
    from faster_whisper import WhisperModel
    if setup_cuda_dlls():
        try:
            return WhisperModel(name, device="cuda", compute_type="float16"), "GPU"
        except Exception as e:  # thiếu DLL CUDA / hết VRAM → chạy CPU
            log(f"Không dùng được GPU ({e}), chuyển sang CPU")
    return WhisperModel(name, device="cpu", compute_type="int8"), "CPU"


def run(proj: Project, settings):
    if not proj.meta.get("has_audio"):
        raise RuntimeError("Video không có âm thanh")
    audio = proj.path("audio16k.wav")
    progress(STEP, 0, "Đang tách âm thanh...")
    run_ffmpeg(["-i", proj.meta["video"], "-vn", "-ac", "1", "-ar", "16000", audio])

    name = settings["whisper_model"]
    progress(STEP, 1, f"Đang nạp mô hình {name} (lần đầu sẽ tải về ~1.6GB)...")
    model, dev = _load_model(name)
    lang = proj.opt["source_lang"]
    segs, info = model.transcribe(
        str(audio),
        language=None if lang in ("", "auto") else lang,
        vad_filter=True,
        word_timestamps=True,
        beam_size=5,
        condition_on_previous_text=False,
    )
    log(f"Ngôn ngữ gốc: {info.language} ({info.language_probability:.0%}), chạy bằng {dev}")
    words = []
    for s in segs:
        progress(STEP, 2 + s.end / max(info.duration, 1) * 97, s.text.strip()[:80])
        if s.words:
            words += [{"w": w.word, "s": w.start, "e": w.end} for w in s.words]
        else:
            words.append({"w": s.text, "s": s.start, "e": s.end})

    sentences = _resegment(words)
    for i, s in enumerate(sentences, 1):
        s["id"] = i
        s["vi"] = ""
    proj.save_segments(sentences)
    proj.meta["source_lang_detected"] = info.language
    proj.save()
    proj.mark(STEP)
    progress(STEP, 100, f"Xong: {len(sentences)} câu")
