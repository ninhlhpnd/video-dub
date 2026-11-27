"""Engine lồng tiếng Việt. App Flutter gọi file này; mỗi dòng stdout là một JSON (progress/log/done/error).

Ví dụ:
  python dub.py new --video a.mp4 --workspace D:/VideoDub
  python dub.py run --project D:/VideoDub/a_2026... --settings settings.json
  python dub.py tts --project ...          (chạy lại từ một bước sau khi sửa phụ đề)
  python dub.py youtube --video out.mp4 --title "..." --publish-at 2026-11-01T19:00
Truyện dài (--project là thư mục bộ truyện):
  python dub.py series_new --workspace D:/VideoDub --options '{"genre": "...", "chapters": 1000}'
  python dub.py series_plan --project <bộ truyện>         (sổ tay + các quyển)
  python dub.py series_write --project <bộ truyện> --count 20
  python dub.py series_episode --project <bộ truyện> --workspace D:/VideoDub --minutes 17
  python dub.py series_publish --project <bộ truyện>      (đăng chương lên web đọc truyện)
"""
import argparse
import json
import subprocess
import sys
import traceback

sys.stdout.reconfigure(encoding="utf-8")

from vdub import common  # noqa: E402  (đặt biến môi trường HF_HOME/TORCH_HOME trước khi nạp mô hình)
from vdub.common import PIPELINES, Project, emit, load_settings, steps_for  # noqa: E402

ALL_STEPS = list(dict.fromkeys(s for steps in PIPELINES.values() for s in steps))
SERIES_CMDS = ["series_new", "series_plan", "series_write", "series_rewind", "series_episode", "series_publish",
               "web_check"]


def step_runner(name):
    if name in PIPELINES["story"]:
        from vdub import story
        return getattr(story, name)
    return step_module(name).run


def step_module(name):
    from vdub import overlays, render, review, separate, speakers, transcribe, translate, tts
    return {"transcribe": transcribe, "speakers": speakers, "translate": translate, "script": review,
            "overlays": overlays, "separate": separate, "tts": tts, "render": render}[name]


def plan(proj, start):
    """Các bước sẽ chạy: theo chế độ của project, bắt đầu từ [start] (bỏ qua bước không cần)."""
    full = PIPELINES.get(proj.opt.get("mode", "dub"), PIPELINES["dub"])
    steps = steps_for(proj.opt)
    if not start or start not in full:
        return steps
    return [s for s in steps if full.index(s) >= full.index(start)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="", help="link hoặc đoạn chữ chia sẻ chứa link (lệnh download)")
    ap.add_argument("cmd", choices=["new", "new_story", "download", "music", "run", "plan", "info", "cover", "preview", "caption", "fb_pages",
                                    "youtube", "facebook", *SERIES_CMDS, *ALL_STEPS])
    ap.add_argument("--count", type=int, default=10, help="số chương viết thêm (series_write)")
    ap.add_argument("--minutes", type=float, default=0, help="độ dài tập video (series_episode)")
    ap.add_argument("--n", type=int, default=0, help="chương bắt đầu viết lại (series_rewind)")
    ap.add_argument("--options", default="{}", help="tùy chọn dạng JSON (lệnh new_story)")
    ap.add_argument("--until", default="", choices=["", *ALL_STEPS], help="chạy tới hết bước này rồi dừng")
    ap.add_argument("--voice", default="")
    ap.add_argument("--app-id", default="")
    ap.add_argument("--app-secret", default="")
    ap.add_argument("--user-token", default="")
    ap.add_argument("--project")
    ap.add_argument("--video")
    ap.add_argument("--workspace")
    ap.add_argument("--settings")
    ap.add_argument("--from", dest="start", default="", choices=["", *ALL_STEPS])
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

    if a.cmd == "music":
        from vdub import music
        emit("done", tracks=music.ensure())
        return

    if a.cmd == "download":
        from vdub import download
        projects, errors = download.run(a.url, a.workspace, settings)
        emit("done", projects=projects, errors=errors)
        return

    if a.cmd == "new":
        p = Project.create(a.video, a.workspace)
        emit("done", project=str(p.dir), meta=p.meta)
        return

    if a.cmd == "new_story":
        from vdub import story
        emit("done", project=str(story.create(a.workspace, json.loads(a.options)).dir))
        return

    if a.cmd in SERIES_CMDS:
        from vdub import series
        if a.cmd == "series_new":
            emit("done", project=str(series.Series.create(a.workspace, json.loads(a.options)).dir))
        elif a.cmd == "series_plan":
            series.plan(a.project, settings)
            emit("done", project=a.project)
        elif a.cmd == "series_write":
            series.write(a.project, settings, a.count)
            emit("done", project=a.project)
        elif a.cmd == "series_rewind":
            emit("done", written=series.rewind(a.project, a.n))
        elif a.cmd == "series_episode":
            proj, k, first, last = series.episode(a.project, a.workspace, settings, a.minutes)
            emit("done", project=str(proj.dir), episode=k, first=first, last=last)
        elif a.cmd == "series_publish":
            emit("done", url=series.publish(a.project, settings))
        elif a.cmd == "web_check":
            with series._api(settings) as c:
                emit("done", **series._check(c.get("/api/me")))
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

    if a.cmd == "caption":
        from vdub import caption
        emit("done", **caption.run(proj, settings))
        return

    if a.cmd == "plan":
        emit("done", steps=plan(proj, a.start))
        return

    if a.cmd == "run":
        # Mỗi bước chạy ở tiến trình riêng: faster-whisper (ctranslate2) và torch/demucs mang 2 bản cuDNN khác nhau,
        # nạp chung một tiến trình sẽ crash; tách ra cũng giải phóng hết VRAM giữa các bước.
        steps = plan(proj, a.start)
        if a.until in steps:
            steps = steps[:steps.index(a.until) + 1]
        emit("plan", steps=steps)
        mode = proj.opt.get("mode", "dub")
        i = 0
        while i < len(steps):
            s = steps[i]
            i += 1
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
            # bước vừa chạy có thể đổi chế độ (video không có lời nói → thuyết minh): lập lại các bước còn lại
            fresh = Project(a.project)
            if fresh.opt.get("mode", "dub") != mode:
                mode = fresh.opt.get("mode", "dub")
                steps = [*steps[:i], *plan(fresh, "")]
                emit("plan", steps=steps)
        emit("done", project=str(proj.dir))
        return

    if a.cmd == "translate":
        step_module(a.cmd).run(proj, settings, force=a.force)
    else:
        step_runner(a.cmd)(proj, settings)
    emit("done", project=str(proj.dir))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        emit("error", msg=str(e), trace=traceback.format_exc()[-3000:])
        sys.exit(1)
