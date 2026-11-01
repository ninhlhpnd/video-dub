"""Bước 2: dịch từng câu sang tiếng Việt bằng Gemini, giữ đúng id/mốc thời gian, độ dài vừa khung giờ."""
import json
import re
import time

from .common import Project, log, progress

STEP = "translate"
BATCH = 40
CHARS_PER_SEC = 15  # tốc độ đọc tiếng Việt bình thường của giọng TTS

PROMPT = """Bạn là biên dịch viên lồng tiếng video chuyên nghiệp.
Dịch từng câu thoại dưới đây sang tiếng Việt nói tự nhiên, dễ nghe, đúng ngữ cảnh.
Quy tắc:
- Giữ nguyên "id". Mỗi câu gốc trả về đúng một câu dịch, không gộp, không tách.
- Câu dịch sẽ được máy đọc thành tiếng trong đúng khung thời gian của câu gốc, nên KHÔNG được dài hơn "max_chars" ký tự. Nếu cần thì lược bớt từ đệm, nói gọn lại nhưng giữ ý chính.
- Không thêm chú thích, không phiên âm, không dùng ký hiệu đặc biệt. Số viết bằng chữ số.
- Xưng hô nhất quán theo ngữ cảnh cả đoạn.
{note}
{context}
Câu cần dịch (JSON):
{items}

Trả về JSON dạng: [{{"id": 1, "vi": "..."}}]"""

SHORTEN = """Rút gọn câu tiếng Việt sau để đọc thành tiếng, tối đa {max_chars} ký tự, giữ ý chính, nói tự nhiên.
Câu gốc: {src}
Câu cần rút gọn: {vi}
Chỉ trả về JSON: {{"vi": "..."}}"""


def slot_seconds(segs, i, duration):
    """Thời gian có thể dùng để đọc câu i: từ lúc bắt đầu tới câu kế tiếp (tối đa thêm 0.8s sau khi câu gốc dứt)."""
    s = segs[i]
    nxt = segs[i + 1]["start"] if i + 1 < len(segs) else duration
    return max(0.5, min(nxt, s["end"] + 0.8) - s["start"])


# Gói miễn phí: model Flash chính chỉ ~20 lượt/ngày, các bản Flash-Lite ~500 lượt/ngày (quota tính riêng từng model).
# Hết lượt ngày ở model nào thì chuyển sang model kế tiếp.
FALLBACK_MODELS = ["gemini-flash-lite-latest", "gemini-3.1-flash-lite"]


def _retry_delay(err):
    m = re.search(r"retry(?:Delay)?['\"]?\s*(?:in|:)\s*['\"]?([\d.]+)s", err, re.I)
    return min(90.0, float(m.group(1)) + 1) if m else 20.0


class Gemini:
    def __init__(self, settings):
        key = settings.get("gemini_api_key", "").strip()
        if not key:
            raise RuntimeError("Chưa nhập Gemini API key trong Cài đặt")
        from google import genai
        from google.genai import types
        self.client = genai.Client(api_key=key)
        self.models = list(dict.fromkeys([settings["gemini_model"], *FALLBACK_MODELS]))
        self.cfg = types.GenerateContentConfig(response_mime_type="application/json", temperature=0.3)

    def _next_model(self, why):
        dead = self.models.pop(0)
        if not self.models:
            raise RuntimeError(f"Đã hết lượt Gemini miễn phí hôm nay ở mọi model ({why}). "
                               "Lượt sẽ được làm mới lúc 14h-15h chiều giờ Việt Nam.")
        log(f"{dead}: {why} → chuyển sang {self.models[0]}")

    def ask(self, prompt):
        busy = 0
        for _ in range(12):
            model = self.models[0]
            try:
                r = self.client.models.generate_content(model=model, contents=prompt, config=self.cfg)
                return json.loads(r.text)
            except json.JSONDecodeError:
                log(f"{model} trả về dữ liệu lỗi, hỏi lại...")
            except Exception as e:
                err = str(e)
                if "429" in err or "RESOURCE_EXHAUSTED" in err:
                    if "PerDay" in err or "per day" in err.lower():
                        self._next_model("hết lượt trong ngày")
                    else:
                        wait = _retry_delay(err)
                        log(f"{model}: gọi nhanh quá giới hạn/phút, đợi {wait:.0f}s...")
                        time.sleep(wait)
                elif "404" in err or "NOT_FOUND" in err:
                    self._next_model("model không tồn tại")
                elif "503" in err or "UNAVAILABLE" in err or "500" in err:
                    busy += 1
                    if busy >= 3:
                        busy = 0
                        self._next_model("máy chủ Google đang quá tải")
                    else:
                        log(f"{model}: máy chủ Google quá tải, thử lại sau 10s...")
                        time.sleep(10)
                else:
                    raise RuntimeError(f"Gemini lỗi: {err[:300]}")
        raise RuntimeError("Gemini không phản hồi sau nhiều lần thử")


def run(proj: Project, settings, force=False):
    segs = proj.segments()
    if not segs:
        raise RuntimeError("Chưa có phụ đề gốc, hãy chạy bước nhận dạng trước")
    ai = Gemini(settings)
    note = proj.opt.get("translate_note", "").strip()
    note = f"- Ghi chú của người dùng: {note}" if note else ""
    todo = [i for i, s in enumerate(segs) if force or not s.get("vi")]
    for b in range(0, len(todo), BATCH):
        idx = todo[b:b + BATCH]
        items = [{"id": segs[i]["id"], "text": segs[i]["text"],
                  "max_chars": max(8, int(slot_seconds(segs, i, proj.meta["duration"]) * CHARS_PER_SEC))}
                 for i in idx]
        prev = [{"text": s["text"], "vi": s["vi"]} for s in segs[max(0, idx[0] - 5):idx[0]] if s.get("vi")]
        context = f"Các câu ngay trước (đã dịch, để giữ mạch):\n{json.dumps(prev, ensure_ascii=False)}" if prev else ""

        pending = items
        for _ in range(3):  # gọi lại cho những câu bị thiếu trong kết quả
            data = ai.ask(PROMPT.format(note=note, context=context, items=json.dumps(pending, ensure_ascii=False)))
            got = {int(d["id"]): str(d.get("vi", "")).strip() for d in data if isinstance(d, dict) and "id" in d}
            for i in idx:
                if segs[i]["id"] in got and got[segs[i]["id"]]:
                    segs[i]["vi"] = got[segs[i]["id"]]
            done = {segs[i]["id"] for i in idx if segs[i]["vi"]}
            pending = [it for it in items if it["id"] not in done]
            if not pending:
                break
        proj.save_segments(segs)
        progress(STEP, (b + len(idx)) / len(todo) * 100, segs[idx[-1]]["vi"][:80])

    missing = sum(1 for s in segs if not s.get("vi"))
    if missing:
        log(f"Còn {missing} câu chưa dịch được, có thể sửa tay")
    proj.mark(STEP)
    progress(STEP, 100, "Xong")


def shorten(ai: Gemini, src, vi, max_chars):
    data = ai.ask(SHORTEN.format(src=src, vi=vi, max_chars=max_chars))
    if isinstance(data, list):
        data = data[0] if data else {}
    return str(data.get("vi", "")).strip() or vi
