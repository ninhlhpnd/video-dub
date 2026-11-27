"""Tiến trình vẽ ảnh trên máy bằng SDXL-Lightning (4 bước), chạy bằng .venv_img (tách khỏi .venv chính).

Nhận từng dòng JSON qua stdin: {"prompt": "...", "width": 1344, "height": 768, "seed": 1, "out": "đường dẫn.png"}
Trả từng dòng JSON qua stdout: {"ok": true} hoặc {"ok": false, "error": "..."}; dòng đầu: {"ready": true}.
Giấy phép mô hình: SDXL base & SDXL-Lightning (OpenRAIL++), VAE fp16-fix (MIT) — dùng thương mại được.
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("HF_HOME", str(ROOT / "tools" / "models" / "hf"))
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

# stdout chỉ dành cho JSON trả lời; log của thư viện đẩy sang stderr
_out = sys.stdout
sys.stdout = sys.stderr

BASE = "stabilityai/stable-diffusion-xl-base-1.0"


def reply(**kw):
    _out.write(json.dumps(kw) + "\n")
    _out.flush()


def load():
    import torch
    from diffusers import AutoencoderKL, EulerDiscreteScheduler, StableDiffusionXLPipeline, UNet2DConditionModel
    from huggingface_hub import hf_hub_download
    from safetensors.torch import load_file

    if not torch.cuda.is_available():
        raise RuntimeError("cần card NVIDIA để vẽ ảnh trên máy")
    unet = UNet2DConditionModel.from_config(UNet2DConditionModel.load_config(BASE, subfolder="unet"))
    unet = unet.to(torch.float16)
    unet.load_state_dict(load_file(hf_hub_download("ByteDance/SDXL-Lightning", "sdxl_lightning_4step_unet.safetensors")))
    vae = AutoencoderKL.from_pretrained("madebyollin/sdxl-vae-fp16-fix", torch_dtype=torch.float16)
    pipe = StableDiffusionXLPipeline.from_pretrained(BASE, unet=unet, vae=vae, torch_dtype=torch.float16, variant="fp16")
    pipe.scheduler = EulerDiscreteScheduler.from_config(pipe.scheduler.config, timestep_spacing="trailing")
    pipe.enable_model_cpu_offload()  # card 6GB: chỉ đưa phần đang dùng lên GPU
    pipe.enable_vae_tiling()
    pipe.set_progress_bar_config(disable=True)
    return pipe, torch


def main():
    try:
        pipe, torch = load()
    except Exception as e:
        reply(ready=False, error=f"Không nạp được mô hình vẽ ảnh: {e}")
        return
    reply(ready=True)
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            job = json.loads(line)
            img = pipe(job["prompt"], width=job["width"], height=job["height"], num_inference_steps=4,
                       guidance_scale=0, generator=torch.Generator("cpu").manual_seed(job["seed"])).images[0]
            img.save(job["out"])
            reply(ok=True)
        except Exception as e:
            reply(ok=False, error=str(e)[:500])


if __name__ == "__main__":
    main()
