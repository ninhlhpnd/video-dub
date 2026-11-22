"""Bước phụ (lồng tiếng phim): Gemini nghe âm thanh để biết câu nào nhân vật nào nói, giới tính, tuổi → mỗi nhân vật một giọng."""
import json

from .ai import Gemini
from .common import FEMALE_VOICES, MALE_VOICES, Project, log, progress, run_ffmpeg

STEP = "speakers"

PROMPT = """Đây là âm thanh một đoạn phim/video và danh sách câu thoại kèm thời gian (giây).
Hãy nghe giọng để xác định MỖI CÂU do nhân vật nào nói.
- Đặt tên nhân vật là A, B, C... Cùng một người thì luôn cùng một tên trong cả đoạn.
- Với mỗi nhân vật cho biết: gender ("nam" hoặc "nữ"), age ("trẻ em", "thanh niên", "trung niên", "già"),
  desc (mô tả rất ngắn, ví dụ "cô gái trẻ, giọng trong", "ông lão trầm").
- Câu nào là người dẫn chuyện/thuyết minh thì vẫn coi là một nhân vật.

Câu thoại:
{lines}

Trả về JSON:
{{"speakers": {{"A": {{"gender": "nữ", "age": "thanh niên", "desc": "..."}}}},
  "lines": [{{"id": 1, "speaker": "A"}}]}}"""


def assign_voices(speakers, old):
    """Giữ giọng người dùng đã chọn trước đó; nhân vật mới thì lấy lần lượt giọng nam/nữ chưa dùng."""
    used = set()
    for k, sp in speakers.items():
        if old.get(k, {}).get("voice") and old[k].get("gender") == sp.get("gender"):
            sp["voice"] = old[k]["voice"]
            used.add(sp["voice"])
    for k, sp in speakers.items():
        if sp.get("voice"):
            continue
        pool = FEMALE_VOICES if sp.get("gender") == "nữ" else MALE_VOICES
        free = [v for v in pool if v not in used] or pool
        sp["voice"] = free[0]
        used.add(sp["voice"])
    return speakers


def run(proj: Project, settings):
    segs = proj.segments()
    if not segs:
        raise RuntimeError("Chưa có phụ đề gốc, hãy chạy bước nhận dạng trước")
    ai = Gemini(settings, temperature=0.1)
    progress(STEP, 5, "Đang chuẩn bị âm thanh cho AI nghe...")
    audio = proj.path("speakers.mp3")
    run_ffmpeg(["-i", proj.video, "-vn", "-ac", "1", "-ar", "16000", "-b:a", "32k", audio])
    try:
        progress(STEP, 20, "Đang tải âm thanh lên Gemini...")
        part = ai.upload(audio)
        lines = [{"id": s["id"], "start": round(s["start"], 1), "end": round(s["end"], 1), "text": s["text"]}
                 for s in segs]
        progress(STEP, 45, "Gemini đang nghe và phân biệt nhân vật...")
        data = ai.ask(PROMPT.format(lines=json.dumps(lines, ensure_ascii=False)), parts=[part])
    finally:
        ai.cleanup()
        audio.unlink(missing_ok=True)

    speakers = {str(k): v for k, v in (data.get("speakers") or {}).items() if isinstance(v, dict)}
    by_id = {int(x["id"]): str(x["speaker"]) for x in data.get("lines", []) if "id" in x and "speaker" in x}
    if not speakers or not by_id:
        raise RuntimeError("Gemini không phân biệt được nhân vật, hãy thử lại")
    for s in segs:
        s["speaker"] = by_id.get(s["id"], s.get("speaker") or next(iter(speakers)))
    for k in {s["speaker"] for s in segs} - speakers.keys():
        speakers[k] = {"gender": "nam", "age": "", "desc": ""}

    proj.meta = Project(proj.dir).meta  # đọc lại option mới nhất trước khi ghi
    proj.meta["speakers"] = assign_voices(speakers, proj.meta.get("speakers", {}))
    proj.save()
    proj.save_segments(segs)
    proj.mark(STEP)
    summary = ", ".join(f"{k}: {v.get('desc') or v.get('gender')}" for k, v in speakers.items())
    log(f"Tìm thấy {len(speakers)} nhân vật — {summary}")
    progress(STEP, 100, f"Xong: {len(speakers)} nhân vật")
