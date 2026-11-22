"""Gọi Gemini: trả JSON, tự xử lý quota gói miễn phí, gửi kèm video/âm thanh để AI xem/nghe."""
import json
import re
import time

from .common import log

# Gói miễn phí: model Flash chính chỉ ~20 lượt/ngày, các bản Flash-Lite ~500 lượt/ngày (quota tính riêng từng model).
# Hết lượt ngày ở model nào thì chuyển sang model kế tiếp.
FALLBACK_MODELS = ["gemini-flash-lite-latest", "gemini-3.1-flash-lite"]


def _retry_delay(err):
    m = re.search(r"retry(?:Delay)?['\"]?\s*(?:in|:)\s*['\"]?([\d.]+)s", err, re.I)
    return min(90.0, float(m.group(1)) + 1) if m else 20.0


class Gemini:
    def __init__(self, settings, temperature=0.3):
        key = settings.get("gemini_api_key", "").strip()
        if not key:
            raise RuntimeError("Chưa nhập Gemini API key trong Cài đặt")
        from google import genai
        from google.genai import types
        self.types = types
        self.client = genai.Client(api_key=key)
        self.models = list(dict.fromkeys([settings["gemini_model"], *FALLBACK_MODELS]))
        self.cfg = types.GenerateContentConfig(response_mime_type="application/json", temperature=temperature)
        # video: độ phân giải thấp (~66 token/khung) để phim dài vẫn vừa giới hạn token/phút của gói miễn phí
        self.media_cfg = types.GenerateContentConfig(
            response_mime_type="application/json", temperature=temperature,
            media_resolution=types.MediaResolution.MEDIA_RESOLUTION_LOW)
        self.uploaded = []

    def _next_model(self, why):
        dead = self.models.pop(0)
        if not self.models:
            raise RuntimeError(f"Đã hết lượt Gemini miễn phí hôm nay ở mọi model ({why}). "
                               "Lượt sẽ được làm mới lúc 14h-15h chiều giờ Việt Nam.")
        log(f"{dead}: {why} → chuyển sang {self.models[0]}")

    def upload(self, path, fps=None):
        """Tải file (video/âm thanh) lên Gemini, đợi xử lý xong. Trả về Part dùng trong ask()."""
        f = self.client.files.upload(file=str(path))
        for _ in range(120):
            state = str(getattr(f.state, "name", f.state))
            if "ACTIVE" in state:
                break
            if "FAILED" in state:
                raise RuntimeError("Gemini không xử lý được file video/âm thanh")
            time.sleep(3)
            f = self.client.files.get(name=f.name)
        self.uploaded.append(f.name)
        t = self.types
        meta = t.VideoMetadata(fps=fps) if fps else None
        return t.Part(file_data=t.FileData(file_uri=f.uri, mime_type=f.mime_type), video_metadata=meta)

    def cleanup(self):
        for name in self.uploaded:
            try:
                self.client.files.delete(name=name)
            except Exception:
                pass
        self.uploaded.clear()

    def ask(self, prompt, parts=()):
        busy = 0
        contents = [*parts, prompt] if parts else prompt
        cfg = self.media_cfg if parts else self.cfg
        for _ in range(12):
            model = self.models[0]
            try:
                r = self.client.models.generate_content(model=model, contents=contents, config=cfg)
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
