"""Tiến trình đọc tiếng Việt bằng VieNeu-TTS, chạy bằng .venv_tts (tách khỏi .venv chính vì khác phiên bản thư viện).

Nhận từng dòng JSON qua stdin: {"text": "...", "voice": "Trúc Ly", "out": "đường dẫn.wav"}
Trả từng dòng JSON qua stdout: {"ok": true} hoặc {"ok": false, "error": "..."}.
Dòng đầu tiên sau khi nạp xong mô hình: {"ready": true}.
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "tools" / "models" / "vieneu"
os.environ.setdefault("HF_HOME", str(ROOT / "tools" / "models" / "hf"))
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

# stdout chỉ dành cho JSON trả lời; log của thư viện đẩy sang stderr
_out = sys.stdout
sys.stdout = sys.stderr


def reply(**kw):
    _out.write(json.dumps(kw) + "\n")
    _out.flush()


def load():
    from huggingface_hub import hf_hub_download
    from vieneu import Vieneu
    from vieneu._v3_turbo_engine.onnx_runtime_lite import OnnxV3LiteEngine

    # Cache HF đặt file .onnx và .data ở 2 thư mục blob khác nhau → onnxruntime từ chối nạp.
    # Tải thẳng vào thư mục thật để 2 file nằm cạnh nhau.
    def fetch(repo, files, subfolder):
        local = MODELS / repo.replace("/", "__")
        for fn in files:
            try:
                hf_hub_download(repo, fn, subfolder=subfolder or None, local_dir=local)
            except Exception:
                if not fn.endswith(".json"):
                    raise
        return local / subfolder if subfolder else local

    OnnxV3LiteEngine._fetch = staticmethod(fetch)
    return Vieneu(backend="onnx")


def main():
    try:
        tts = load()
    except Exception as e:
        reply(ready=False, error=f"Không nạp được VieNeu-TTS: {e}")
        return
    reply(ready=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            job = json.loads(line)
            audio = tts.infer(job["text"], voice=job["voice"])
            tts.save(audio, job["out"])
            reply(ok=True)
        except Exception as e:
            reply(ok=False, error=str(e)[:500])


if __name__ == "__main__":
    main()
