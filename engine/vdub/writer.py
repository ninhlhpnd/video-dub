"""Người viết truyện: Gemini Flash (gói miễn phí) hoặc Claude (trả phí), cùng một cách gọi ask()."""
import json
import re
import time

from .common import log

# Xếp theo chất lượng văn; mỗi model có quota ngày riêng (~20 lượt). Bản Lite chỉ là phương án cuối.
GEMINI_MODELS = ["gemini-flash-latest", "gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash",
                 "gemini-flash-lite-latest", "gemini-3.5-flash-lite"]
TIMEOUT_MS = 240_000


def make_writer(settings):
    """ask(system, messages, schema=None, effort, on_text): content của message là chuỗi, hoặc danh sách chuỗi
    (các khối đầu ổn định, Claude lưu đệm prompt theo từng khối)."""
    w = settings.get("story_writer")
    if w == "claude":
        from .claude import Claude
        return Claude(settings)
    if w == "claude_cli":
        from .claude import ClaudeCLI
        return ClaudeCLI(settings)
    return GeminiWriter(settings)


def flat(content):
    return content if isinstance(content, str) else "\n\n".join(x for x in content if x)


class GeminiWriter:
    def __init__(self, settings):
        key = settings.get("gemini_api_key", "").strip()
        if not key:
            raise RuntimeError("Chưa nhập Gemini API key trong Cài đặt")
        from google import genai
        from google.genai import types

        from . import ai
        self.types, self.status = types, ai
        self.client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=TIMEOUT_MS))
        self.cost = 0.0

    def ask(self, system, messages, schema=None, effort="high", on_text=None):
        t = self.types
        contents = [t.Content(role="model" if m["role"] == "assistant" else "user",
                              parts=[t.Part(text=flat(m["content"]))]) for m in messages]
        cfg = t.GenerateContentConfig(system_instruction=system, temperature=0.4 if schema else 1.0,
                                      max_output_tokens=60000,
                                      **({"response_mime_type": "application/json", "response_json_schema": schema}
                                         if schema else {}))
        why = ""
        for turn in range(5):
            avoid = self.status._status()
            for m in [m for m in GEMINI_MODELS if m not in avoid]:
                try:
                    text, finish = "", ""
                    for ch in self.client.models.generate_content_stream(model=m, contents=contents, config=cfg):
                        if ch.text:
                            text += ch.text
                            if on_text:
                                on_text(len(text))
                        if ch.candidates and ch.candidates[0].finish_reason:
                            finish = ch.candidates[0].finish_reason.name
                    if finish != "STOP" or not text.strip():
                        why = f"dừng giữa chừng ({finish or 'mất kết nối'})"
                        log(f"{m}: {why} → thử model khác")
                        continue
                    out = json.loads(text) if schema else _clean(text)
                    log(f"Viết bằng {m}")
                    return out
                except json.JSONDecodeError:
                    why = "trả dữ liệu lỗi"
                except Exception as e:
                    err = str(e)
                    if "API key not valid" in err:
                        raise RuntimeError("Gemini API key không đúng")
                    if "429" in err or "404" in err:
                        day = "PerDay" in err or "per day" in err.lower() or "404" in err
                        self.status._avoid(m, self.status.QUOTA_COOLDOWN if day else 65)
                        why = "hết lượt"
                    elif any(x in err for x in ("503", "500", "504", "UNAVAILABLE")) or \
                            "timeout" in type(e).__name__.lower() or "timed out" in err.lower() or \
                            "disconnect" in err.lower():
                        self.status._avoid(m, 60)
                        why = "máy chủ Google quá tải"
                    else:
                        raise RuntimeError(f"Gemini lỗi: {err[:300]}")
                log(f"{m}: {why} → thử model khác")
            if turn < 4:
                log("Mọi model Gemini đang bận, đợi 1 phút rồi thử lại...")
                time.sleep(62)
        raise RuntimeError(f"Gemini chưa viết được ({why or 'hết lượt'}). Phần đã viết vẫn được giữ, lát nữa bấm chạy tiếp.")


def _clean(text):
    """Bỏ dấu markdown và dòng rào đón nếu model lỡ thêm vào."""
    lines = []
    for s in text.strip().splitlines():
        s = re.sub(r"^#+\s*", "", s.strip()).replace("*", "")
        if not s or re.fullmatch(r"[-–—_=]{3,}", s):
            continue
        if not lines and re.match(r"(?i)^(dưới đây là|đây là chương|chương \d+\b.{0,80}$)", s):
            continue
        lines.append(s)
    return "\n".join(lines)
