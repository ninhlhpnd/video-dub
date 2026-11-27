"""Gọi Claude để viết truyện: qua Claude API (trả theo token) hoặc qua Claude Code đã đăng nhập trên máy
(lệnh `claude -p`, dùng gói Claude Pro/Max). Cộng dồn chi phí thực tế theo usage trả về."""
import json
import os
import shutil
import subprocess
from pathlib import Path

from .common import log

# USD cho 1 triệu token (vào, ra). Ghi cache tính 1.25x giá vào.
PRICES = {
    "claude-opus-5-5": (4.0, 20.0, 0.20),
    "claude-sonnet-5-5": (2.0, 10.0, 0.10),
}


class Claude:
    def __init__(self, settings):
        key = settings.get("claude_api_key", "").strip()
        if not key:
            raise RuntimeError("Chưa nhập Claude API key trong Cài đặt (lấy tại platform.claude.com, cần có credit)")
        import anthropic
        self.sdk = anthropic
        self.client = anthropic.Anthropic(api_key=key, max_retries=4)
        self.model = settings.get("claude_model") or "claude-opus-5-5"
        self.cost = 0.0

    def ask(self, system, messages, schema=None, effort="high", on_text=None):
        """Trả về văn bản, hoặc dict nếu có [schema]."""
        cfg = {"effort": effort}
        if schema:
            cfg["format"] = {"type": "json_schema", "schema": schema}
        blocks = any(not isinstance(m["content"], str) for m in messages)
        if blocks:
            # các khối đầu (sổ tay, dàn ý quyển) ít đổi: đặt mốc lưu đệm sau từng khối (tối đa 3 mốc)
            msgs = []
            for m in messages:
                c = m["content"]
                if isinstance(c, str):
                    msgs.append(m)
                    continue
                parts = [x for x in c if x]
                content = [{"type": "text", "text": x} for x in parts]
                for b in content[:-1][:3]:
                    b["cache_control"] = {"type": "ephemeral"}
                msgs.append({"role": m["role"], "content": content})
            messages = msgs
        try:
            with self.client.beta.messages.stream(
                model=self.model, max_tokens=64000, system=system, messages=messages, output_config=cfg,
                **({} if blocks else {"cache_control": {"type": "ephemeral"}}),
                betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            ) as stream:
                n = 0
                for t in stream.text_stream:
                    n += len(t)
                    if on_text:
                        on_text(n)
                msg = stream.get_final_message()
        except self.sdk.AuthenticationError:
            raise RuntimeError("Claude API key không đúng")
        except self.sdk.PermissionDeniedError as e:
            raise RuntimeError(f"Claude API key không có quyền: {e.message}")
        except self.sdk.RateLimitError:
            raise RuntimeError("Claude: gọi quá giới hạn, đợi vài phút rồi chạy tiếp")
        except self.sdk.APIStatusError as e:
            if "credit balance" in str(e.message).lower():
                raise RuntimeError("Tài khoản Claude API hết tiền, hãy nạp thêm tại platform.claude.com → Billing")
            raise RuntimeError(f"Claude lỗi {e.status_code}: {str(e.message)[:300]}")
        except self.sdk.APIConnectionError:
            raise RuntimeError("Không kết nối được Claude, kiểm tra mạng")
        self._add_cost(msg)
        if msg.stop_reason == "refusal":
            raise RuntimeError("Claude từ chối viết nội dung này, hãy đổi chủ đề hoặc yêu cầu")
        if msg.stop_reason == "max_tokens":
            raise RuntimeError("Claude viết quá dài bị cắt, hãy giảm độ dài video")
        text = "".join(b.text for b in msg.content if b.type == "text").strip()
        if not text:
            raise RuntimeError("Claude trả về nội dung rỗng, hãy chạy lại")
        return json.loads(text) if schema else text

    def _add_cost(self, msg):
        u = msg.usage
        pin, pout, pread = PRICES.get(self.model, PRICES["claude-opus-5-5"])
        c = (u.input_tokens * pin + (u.cache_creation_input_tokens or 0) * pin * 1.25
             + (u.cache_read_input_tokens or 0) * pread + u.output_tokens * pout) / 1e6
        self.cost += c
        log(f"Claude: {u.output_tokens} token viết ra, khoảng ${c:.3f}")


def find_cli(settings):
    p = (settings.get("claude_cli_path") or "").strip()
    if p and Path(p).exists():
        return p
    found = shutil.which("claude")
    if found:
        return found
    home = Path(os.environ.get("USERPROFILE", "~")).expanduser()
    for c in (home / ".local" / "bin" / "claude.exe", Path(os.environ.get("APPDATA", "")) / "npm" / "claude.cmd"):
        if c.exists():
            return str(c)
    raise RuntimeError("Không tìm thấy Claude Code (claude.exe). Cài tại claude.com/claude-code, đăng nhập bằng "
                       "lệnh `claude` một lần, hoặc chỉ đường dẫn trong Cài đặt")


class ClaudeCLI:
    """Dùng Claude Code đang đăng nhập trên máy làm người viết (không cần API key, tính vào hạn mức gói Claude).
    Chạy chế độ không công cụ: chỉ sinh chữ, không đọc/ghi file."""

    def __init__(self, settings):
        self.exe = find_cli(settings)
        self.model = {"claude-opus-5-5": "opus", "claude-sonnet-5-5": "sonnet"}.get(
            settings.get("claude_model") or "", settings.get("claude_model") or "opus")
        self.cost = 0.0  # gói thuê bao: không cộng tiền, chỉ ghi giá tham khảo vào log

    def _sys_file(self, system):
        """Lời dặn hệ thống ghi ra file (dòng lệnh Windows giới hạn độ dài, nhất là khi gọi qua claude.cmd)."""
        import hashlib
        import tempfile
        f = Path(tempfile.gettempdir()) / f"vdub_sys_{hashlib.md5(system.encode()).hexdigest()[:10]}.txt"
        if not f.exists():
            f.write_text(system, encoding="utf-8")
        return str(f)

    def ask(self, system, messages, schema=None, effort="high", on_text=None):
        from .writer import flat
        # Claude Code nhận một lượt hỏi: ghép lịch sử hội thoại thành văn bản
        if len(messages) == 1:
            prompt = flat(messages[0]["content"])
        else:
            prompt = "\n\n".join(("NGƯỜI DÙNG:\n" if m["role"] == "user" else "BẠN ĐÃ VIẾT:\n") + flat(m["content"])
                                  for m in messages)
        cmd = [self.exe, "-p", "--output-format", "json", "--model", self.model, "--tools", "",
               "--disallowedTools", "mcp__*", "--max-turns", "3", "--no-session-persistence",
               "--system-prompt-file", self._sys_file(system)]
        if schema:
            cmd += ["--json-schema", json.dumps(schema, ensure_ascii=False)]
        if on_text:
            on_text(0)
        try:
            r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=1800, creationflags=0x08000000 if os.name == "nt" else 0)
        except subprocess.TimeoutExpired:
            raise RuntimeError("Claude Code quá 30 phút chưa trả lời, hãy chạy lại")
        try:
            out = json.loads(r.stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            err = (r.stderr or r.stdout).strip()[-400:]
            if "login" in err.lower() or "auth" in err.lower():
                raise RuntimeError("Claude Code chưa đăng nhập: mở cửa sổ lệnh, gõ `claude` và đăng nhập một lần")
            raise RuntimeError(f"Claude Code lỗi: {err or 'không có phản hồi'}")
        if out.get("is_error"):
            msg = str(out.get("result") or out.get("subtype") or "")
            if "limit" in msg.lower():
                raise RuntimeError("Claude Code đã hết hạn mức của gói trong lúc này, đợi vài giờ rồi chạy tiếp. "
                                   "Phần đã viết vẫn được giữ.")
            raise RuntimeError(f"Claude Code lỗi: {msg[:300]}")
        if "total_cost_usd" in out:
            log(f"Claude Code: xong một lượt (giá tham khảo nếu tính theo API ${out['total_cost_usd']:.3f})")
        if schema:
            data = out.get("structured_output")
            if data is None:
                text = str(out.get("result") or "")
                i, j = text.find("{"), text.rfind("}")
                try:
                    data = json.loads(text[i:j + 1])
                except (ValueError, json.JSONDecodeError):
                    raise RuntimeError("Claude Code không trả đúng dữ liệu, hãy chạy lại")
            return data
        text = str(out.get("result") or "").strip()
        if not text:
            raise RuntimeError("Claude Code trả về nội dung rỗng, hãy chạy lại")
        return text
