"""Engine lồng tiếng Việt. App Flutter gọi file này; mỗi dòng stdout là một JSON (progress/log/done/error).

Ví dụ:
  python dub.py new --video a.mp4 --workspace D:/VideoDub
  python dub.py run --project D:/VideoDub/a_2026... --settings settings.json
  python dub.py tts --project ...          (chạy lại từ một bước sau khi sửa phụ đề)
  python dub.py youtube --video out.mp4 --title "..." --publish-at 2026-11-01T19:00
"""
import argparse
import subprocess
import sys
import traceback

sys.stdout.reconfigure(encoding="utf-8")

from vdub import common  # noqa: E402  (đặt biến môi trường HF_HOME/TORCH_HOME trước khi nạp mô hình)
from vdub.common import Project, emit, load_settings  # noqa: E402

PIPELINE = ["transcribe", "translate", "separate", "tts", "render"]


def step_module(name):
    from vdub import render, separate, transcribe, translate, tts
    return {"transcribe": transcribe, "translate": translate, "separate": separate,
            "tts": tts, "render": render}[name]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["new", "run", "info", "cover", "preview", "fb_pages", "youtube", "facebook",
                                    *PIPELINE])
    ap.add_argument("--voice", default="")
    ap.add_argument("--app-id", default="")
    ap.add_argument("--app-secret", default="")
    ap.add_argument("--user-token", default="")
    ap.add_argument("--project")
    ap.add_argument("--video")
    ap.add_argument("--workspace")
    ap.add_argument("--settings")
    ap.add_argument("--from", dest="start", default="transcribe", choices=PIPELINE)
    ap.add_argument("--force", action="store_true", help="dịch lại cả những câu đã dịch")
    ap.add_argument("--title", default="")
    ap.add_argument("--description", default="")
    ap.add_argument("--tags", default="")
    ap.add_argument("--publish-at", default="")
    ap.add_argument("--privacy", default="public")
    a = ap.parse_args()
    settings = load_settings(a.settings)

    if a.cmd == "info":
        cuda = common.setup_cuda_dlls()
        name = ""
        if cuda:
            import torch
            name = torch.cuda.get_device_name(0)
        emit("done", cuda=cuda, gpu=name, ffmpeg=common.FFMPEG.exists())
        return

    if a.cmd == "preview":
        from vdub import tts
        emit("done", file=tts.preview(a.voice))
        return

    if a.cmd == "new":
        p = Project.create(a.video, a.workspace)
        emit("done", project=str(p.dir), meta=p.meta)
        return

    if a.cmd == "fb_pages":
        from vdub import upload
        emit("done", pages=upload.facebook_pages(a.app_id, a.app_secret, a.user_token, settings["graph_version"]))
        return

    if a.cmd in ("youtube", "facebook"):
        from vdub import upload
        if a.cmd == "youtube":
            tags = [t.strip() for t in a.tags.split(",") if t.strip()]
            url = upload.youtube(a.video, a.title, a.description, tags, a.publish_at, a.privacy, settings)
        else:
            url = upload.facebook(a.video, a.title, a.description, a.publish_at, settings)
        emit("done", url=url)
        return

    proj = Project(a.project)
    if a.cmd == "cover":
        from vdub import cover
        emit("done", **cover.detect(proj))
        return

    if a.cmd == "run":
        # Mỗi bước chạy ở tiến trình riêng: faster-whisper (ctranslate2) và torch/demucs mang 2 bản cuDNN khác nhau,
        # nạp chung một tiến trình sẽ crash; tách ra cũng giải phóng hết VRAM giữa các bước.
        for s in PIPELINE[PIPELINE.index(a.start):]:
            if s == "separate" and not Project(a.project).opt.get("keep_background"):
                continue
            emit("step", step=s)
            cmd = [sys.executable, __file__, s, "--project", a.project]
            if a.settings:
                cmd += ["--settings", a.settings]
            if a.force:
                cmd.append("--force")
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                 encoding="utf-8", errors="replace", creationflags=common.NO_WINDOW)
            failed = False
            for line in p.stdout:
                if '"type": "done"' in line:
                    continue  # chỉ báo done một lần khi xong cả quy trình
                failed |= '"type": "error"' in line
                sys.stdout.write(line)
                sys.stdout.flush()
            code = p.wait()
            if failed:
                sys.exit(1)
            if code:
                raise RuntimeError(f"Bước {s} bị dừng đột ngột (mã {code})")
        emit("done", project=str(proj.dir))
        return

    if a.cmd == "translate":
        step_module(a.cmd).run(proj, settings, force=a.force)
    else:
        step_module(a.cmd).run(proj, settings)
    emit("done", project=str(proj.dir))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        emit("error", msg=str(e), trace=traceback.format_exc()[-3000:])
        sys.exit(1)
