"""Nhà cung cấp AI: Gemini (google-genai), Claude (anthropic), Mock (chạy thử/kiểm thử, không cần mạng).

Mọi provider có cùng hàm generate(system_blocks, prompt, schema) → Result.
system_blocks[0] là phần cố định, system_blocks[1:] là bảng thuật ngữ: Claude đặt cache_control ở khối cuối
để lần gọi sau dùng lại tiền tố đã cache (rẻ hơn ~10 lần).
"""
import json
import re
import time
from dataclasses import dataclass

from ..config import settings

# USD / 1 triệu token: (vào, ra, đọc cache). Ghi cache tính 1.25x giá vào.
CLAUDE_PRICES = {
    "claude-sonnet-5-5": (2.0, 10.0, 0.10),
    "claude-opus-5-5": (4.0, 20.0, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
    "claude-sonnet-5": (2.0, 10.0, 0.20),
    "claude-opus-5": (5.0, 25.0, 0.50),
}
# các model có fallback phía máy chủ khi bị bộ lọc an toàn từ chối
CLAUDE_FALLBACK_MODELS = {"claude-sonnet-5-5", "claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-fable-5-2"}


@dataclass
class Result:
    data: dict
    tokens_in: int = 0
    tokens_out: int = 0
    cost: float = 0.0


class ProviderError(RuntimeError):
    """fatal=True: lỗi cấu hình (sai key, hết tiền) → dừng cả việc dịch. retry_after: đợi rồi thử lại."""

    def __init__(self, msg, fatal=False, retry_after=None):
        super().__init__(msg)
        self.fatal = fatal
        self.retry_after = retry_after


def _parse_json(text: str) -> dict:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    return json.loads(text)


class Provider:
    name = "base"
    model = ""

    def generate(self, system_blocks, prompt, schema) -> Result:  # pragma: no cover
        raise NotImplementedError


# ---------------------------------------------------------------- Claude

class ClaudeProvider(Provider):
    name = "claude"

    def __init__(self, api_key=None, model=None):
        key = (api_key or settings.claude_api_key).strip()
        if not key:
            raise ProviderError("Chưa đặt CLAUDE_API_KEY", fatal=True)
        import anthropic
        self.sdk = anthropic
        self.client = anthropic.Anthropic(api_key=key, max_retries=3)
        self.model = model or settings.claude_model

    def price(self):
        return CLAUDE_PRICES.get(self.model, CLAUDE_PRICES["claude-sonnet-5-5"])

    def generate(self, system_blocks, prompt, schema) -> Result:
        system = [{"type": "text", "text": b} for b in system_blocks if b]
        system[-1]["cache_control"] = {"type": "ephemeral"}
        kwargs = dict(
            model=self.model, max_tokens=32000, system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": schema}},
        )
        if self.model in CLAUDE_FALLBACK_MODELS:
            kwargs.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        sdk = self.sdk
        try:
            with self.client.beta.messages.stream(**kwargs) as stream:
                msg = stream.get_final_message()
        except sdk.AuthenticationError:
            raise ProviderError("Claude API key không đúng", fatal=True)
        except sdk.PermissionDeniedError as e:
            raise ProviderError(f"Claude API key không có quyền: {e.message}", fatal=True)
        except sdk.RateLimitError:
            raise ProviderError("Claude: vượt giới hạn gọi, đợi rồi thử lại", retry_after=60)
        except sdk.APIStatusError as e:
            if "credit balance" in str(e.message).lower():
                raise ProviderError("Tài khoản Claude API hết tiền, hãy nạp thêm", fatal=True)
            if e.status_code >= 500:
                raise ProviderError(f"Claude quá tải ({e.status_code})", retry_after=30)
            raise ProviderError(f"Claude lỗi {e.status_code}: {str(e.message)[:300]}")
        except sdk.APIConnectionError:
            raise ProviderError("Không kết nối được Claude", retry_after=30)
        u = msg.usage
        pin, pout, pread = self.price()
        cw = u.cache_creation_input_tokens or 0
        cr = u.cache_read_input_tokens or 0
        cost = (u.input_tokens * pin + cw * pin * 1.25 + cr * pread + u.output_tokens * pout) / 1e6
        if msg.stop_reason == "refusal":
            raise ProviderError("Claude từ chối dịch đoạn này")
        if msg.stop_reason == "max_tokens":
            raise ProviderError("Chương quá dài, bản dịch bị cắt")
        text = "".join(b.text for b in msg.content if b.type == "text")
        try:
            data = _parse_json(text)
        except json.JSONDecodeError:
            raise ProviderError("Claude trả về JSON lỗi", retry_after=5)
        return Result(data, u.input_tokens + cw + cr, u.output_tokens, cost)


# ---------------------------------------------------------------- Gemini

def _retry_delay(err: str) -> float:
    m = re.search(r"retry(?:Delay)?['\"]?\s*(?:in|:)\s*['\"]?([\d.]+)s", err, re.I)
    return min(120.0, float(m.group(1)) + 1) if m else 30.0


class GeminiProvider(Provider):
    name = "gemini"

    def __init__(self, api_key=None, models=None):
        key = (api_key or settings.gemini_api_key).strip()
        if not key:
            raise ProviderError("Chưa đặt GEMINI_API_KEY", fatal=True)
        from google import genai
        from google.genai import types
        self.types = types
        self.client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=180_000))
        self.models = list(models or settings.gemini_models)
        self.model = self.models[0]
        self._dead = {}  # model → thời điểm được dùng lại

    def _pick(self):
        t = time.time()
        alive = [m for m in self.models if self._dead.get(m, 0) <= t]
        return alive[0] if alive else None

    def generate(self, system_blocks, prompt, schema) -> Result:
        t = self.types
        cfg_args = dict(system_instruction="\n\n".join(b for b in system_blocks if b),
                        response_mime_type="application/json", temperature=0.4)
        if "response_json_schema" in getattr(t.GenerateContentConfig, "model_fields", {}):
            cfg_args["response_json_schema"] = schema
        cfg = t.GenerateContentConfig(**cfg_args)
        last = ""
        for _ in range(8):
            model = self._pick()
            if model is None:
                wait = max(5, min(self._dead.values()) - time.time())
                raise ProviderError(f"Mọi model Gemini đang hết lượt/quá tải ({last})", retry_after=min(wait, 900))
            self.model = model
            try:
                r = self.client.models.generate_content(model=model, contents=prompt, config=cfg)
                data = _parse_json(r.text)
                um = r.usage_metadata
                return Result(data, getattr(um, "prompt_token_count", 0) or 0,
                              getattr(um, "candidates_token_count", 0) or 0, 0.0)
            except json.JSONDecodeError:
                last = "JSON lỗi"
                continue
            except Exception as e:  # google-genai gói lỗi HTTP trong APIError; phân loại theo mã trong chuỗi
                err = str(e)
                last = err[:200]
                if "API_KEY_INVALID" in err or "API key not valid" in err or "PERMISSION_DENIED" in err:
                    raise ProviderError("Gemini API key không đúng hoặc không có quyền", fatal=True)
                if "429" in err or "RESOURCE_EXHAUSTED" in err:
                    if "PerDay" in err or "per day" in err.lower():
                        self._dead[model] = time.time() + 6 * 3600
                    else:
                        time.sleep(_retry_delay(err))
                elif "404" in err or "NOT_FOUND" in err:
                    self._dead[model] = time.time() + 24 * 3600
                elif "503" in err or "500" in err or "UNAVAILABLE" in err or "timed out" in err.lower() \
                        or "timeout" in type(e).__name__.lower():
                    self._dead[model] = time.time() + 300
                else:
                    raise ProviderError(f"Gemini lỗi: {err[:300]}")
        raise ProviderError(f"Gemini không phản hồi sau nhiều lần thử ({last})", retry_after=60)


# ---------------------------------------------------------------- Mock

class MockProvider(Provider):
    """Không gọi mạng. Dùng khi chạy thử giao diện (TRANSLATOR=mock) và trong kiểm thử.
    handler(system_blocks, prompt, schema) → dict cho phép test tự quyết kết quả."""
    name = "mock"
    model = "mock"

    def __init__(self, handler=None):
        self.handler = handler
        self.calls = []

    def generate(self, system_blocks, prompt, schema) -> Result:
        self.calls.append({"system": list(system_blocks), "prompt": prompt, "schema": schema})
        if self.handler:
            return Result(self.handler(system_blocks, prompt, schema), len(prompt), 100, 0.0)
        props = schema.get("properties", {})
        if "terms" in props:
            return Result({"genre": "huyền huyễn", "terms": []})
        if "genres" in props:
            m = re.search(r'"title": "([^"]*)"', prompt)
            return Result({"title": f"[Bản dịch thử] {m.group(1) if m else ''}", "author": "", "description": "",
                           "genres": []})
        m = re.search(r"NỘI DUNG GỐC:\n(.*)", prompt, re.S)
        body = (m.group(1) if m else "").strip()
        paras = [p for p in body.split("\n\n") if p.strip()]
        return Result({"title": "", "content": "\n\n".join(f"[dịch thử] {p}" for p in paras) or "[dịch thử]",
                       "new_terms": [], "summary": "Tóm tắt thử."})


def get_provider(name=None) -> Provider:
    name = (name or settings.translator or "gemini").lower()
    if name == "claude":
        return ClaudeProvider()
    if name == "mock":
        return MockProvider()
    return GeminiProvider()


def estimate_claude_cost(chars_zh: int, chapters: int, model=None, glossary_tokens: int = 2500) -> float:
    """Ước tính USD: ~1.1 token/chữ Hán đầu vào, ~1.6 token đầu ra; phần system + thuật ngữ phần lớn đọc từ cache."""
    pin, pout, pread = CLAUDE_PRICES.get(model or settings.claude_model, CLAUDE_PRICES["claude-sonnet-5-5"])
    tin = chars_zh * 1.1 + chapters * 600
    tout = chars_zh * 1.6 + chapters * 250
    cached = chapters * glossary_tokens
    return (tin * pin + tout * pout + cached * pread) / 1e6
