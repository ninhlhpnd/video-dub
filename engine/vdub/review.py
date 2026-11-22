"""Chế độ Review phim: Gemini xem video, viết lời kể tiếng Việt theo cảnh.

- review_minutes = 0: kể trên toàn bộ clip (clip ngắn).
- review_minutes > 0: chọn các cảnh chính, cắt ghép thành video ~N phút (tóm tắt phim dài) rồi kể trên đó.
- review_source = "user": người dùng tự nhập lời thuyết minh, app tách câu và đặt từng câu vào đúng cảnh.
Kết quả ghi vào segments.json (mỗi câu kể là một "câu" có start/end/vi) để các bước lồng tiếng/xuất dùng lại.
"""
import json
import math
import re

from .ai import Gemini
from .common import Project, log, progress, run_ffmpeg

STEP = "script"
CHUNK = 900          # mỗi lần gửi Gemini xem tối đa 15 phút (vừa giới hạn token/phút gói miễn phí)
CPS = 13             # ký tự/giây khi viết lời kể (để chừa chỗ cho giọng đọc)

NARRATE = """Bạn là người viết lời thuyết minh cho video trên TikTok/YouTube/Facebook Việt Nam.
Xem đoạn video này (dài {dur:.0f} giây{part}) và viết LỜI THUYẾT MINH tiếng Việt đọc đè lên video.
Phong cách: {style}
{note}{context}
Quy tắc:
- Chia lời thành nhiều câu, mỗi câu gắn với khoảng thời gian (giây, tính từ đầu đoạn video này) đúng lúc hình ảnh đó xuất hiện.
- Mỗi câu dài tối đa ({cps} × số giây của câu) ký tự để đọc kịp. Các câu không chồng thời gian lên nhau.
- Chừa khoảng lặng ở những cảnh đắt giá/thoại quan trọng để người xem nghe tiếng gốc.
- Nếu là phim/truyện: kể hấp dẫn, gây tò mò, dùng ngôi thứ ba ("anh chàng", "cô gái"...), không đọc lại nguyên văn thoại.
- Nếu là video sản phẩm/bán hàng/hướng dẫn: giới thiệu đúng những gì THẤY trong video (công dụng, cách dùng, chữ hiện trên hình);
  KHÔNG bịa giá, thông số, khuyến mãi không có trong video. Có thể kết bằng 1 câu kêu gọi ngắn.
- Không dùng ký hiệu đặc biệt, số viết bằng chữ số.

Trả về JSON: {{"lines": [{{"start": 0.0, "end": 4.5, "text": "..."}}], "summary": "tóm tắt nội dung đoạn này trong 2-3 câu"}}"""

MOMENTS = """Xem đoạn video này (phần {idx}/{total} của một bộ phim, dài {dur:.0f} giây).
{context}
Liệt kê các khoảnh khắc quan trọng theo trình tự thời gian (giây, tính từ đầu đoạn này):
mỗi khoảnh khắc dài 4-25 giây, mô tả ngắn chuyện gì xảy ra, và độ quan trọng với mạch truyện (1-5).
Trả về JSON: {{"moments": [{{"start": 0.0, "end": 8.0, "desc": "...", "importance": 4}}],
"summary": "tóm tắt nội dung phần này trong 3-4 câu"}}"""

RECAP = """Bạn là người làm video tóm tắt/review phim trên TikTok/YouTube Việt Nam.
Dưới đây là các khoảnh khắc của một bộ phim (thời gian tính bằng giây trên phim gốc) và tóm tắt cốt truyện.
Hãy chọn các cảnh để ghép thành video tóm tắt dài khoảng {target} giây, kể trọn mạch truyện, giữ thứ tự thời gian.
Phong cách lời kể: {style}
{note}
Cốt truyện: {summary}
Khoảnh khắc: {moments}

Quy tắc:
- Mỗi cảnh lấy nguyên [start, end] của khoảnh khắc (có thể cắt ngắn bớt), dài 4-25 giây.
- Mỗi cảnh có 1 câu kể đọc đè lên cảnh, dài tối đa ({cps} × số giây của cảnh) ký tự; cảnh cao trào có thể để trống ("") để nghe tiếng phim.
- Kể hấp dẫn, gây tò mò, dùng ngôi thứ ba, có mở đầu cuốn hút và kết thúc gợi mở.
Trả về JSON: {{"scenes": [{{"start": 120.0, "end": 131.5, "text": "..."}}]}}"""


def _proxy(proj, start, dur, out):
    """Bản thu nhỏ 360p để tải lên Gemini nhanh."""
    run_ffmpeg(["-ss", f"{start:.2f}", "-t", f"{dur:.2f}", "-i", proj.meta["video"],
                "-vf", "scale=-2:360,fps=10", "-c:v", "libx264", "-preset", "veryfast", "-crf", "32",
                "-c:a", "aac", "-ac", "1", "-b:a", "48k", out])


def _style(proj):
    o = proj.opt
    note = o.get("review_note", "").strip()
    return o.get("review_style") or "Kể chuyện hấp dẫn", (f"Ghi chú thêm: {note}\n" if note else "")


def _clean_lines(lines, offset, limit):
    out = []
    for x in sorted(lines, key=lambda x: float(x.get("start", 0))):
        try:
            a, b, text = float(x["start"]) + offset, float(x["end"]) + offset, str(x.get("text", "")).strip()
        except (KeyError, TypeError, ValueError):
            continue
        a, b = max(0.0, a), min(limit, b)
        if out and a < out[-1]["end"]:
            a = out[-1]["end"]
        if text and b - a >= 0.8:
            out.append({"start": round(a, 2), "end": round(b, 2), "text": text})
    return out


def _chunks(dur):
    n = max(1, math.ceil(dur / CHUNK))
    size = dur / n
    return [(i * size, size) for i in range(n)]


def narrate_full(proj, ai):
    """Kể trên toàn bộ clip, không cắt."""
    style, note = _style(proj)
    dur = float(proj.meta["duration"])
    parts = _chunks(dur)
    lines, summary = [], ""
    for i, (start, size) in enumerate(parts):
        progress(STEP, 5 + i / len(parts) * 85, f"Gemini đang xem video{f' (phần {i + 1}/{len(parts)})' if len(parts) > 1 else ''}...")
        proxy = proj.path(f"proxy_{i}.mp4")
        _proxy(proj, start, size, proxy)
        try:
            part = ai.upload(proxy)
            ctx = f"Nội dung các phần trước: {summary}\n" if summary else ""
            data = ai.ask(NARRATE.format(dur=size, part=f", phần {i + 1}/{len(parts)} của video" if len(parts) > 1 else "",
                                         style=style, note=note, context=ctx, cps=CPS), parts=[part])
        finally:
            ai.cleanup()
            proxy.unlink(missing_ok=True)
        lines += _clean_lines(data.get("lines", []), start, dur)
        summary = (summary + " " + str(data.get("summary", ""))).strip()
    return lines, summary


def _cut(proj, scenes):
    """Cắt từng cảnh từ phim gốc rồi nối lại (cùng thông số mã hóa nên nối không cần mã hóa lại)."""
    has_audio = proj.meta.get("has_audio")
    files = []
    total = sum(b - a for a, b in scenes)
    done = 0.0
    for k, (a, b) in enumerate(scenes):
        f = proj.path(f"scene_{k:03d}.mp4")
        audio = ["-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "160k"] if has_audio else ["-an"]
        run_ffmpeg(["-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", proj.meta["video"],
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p",
                    "-r", "30", *audio, f])
        files.append(f)
        done += b - a
        progress(STEP, 70 + done / total * 28, f"Đang cắt ghép cảnh {k + 1}/{len(scenes)}...")
    lst = proj.path("scenes.txt")
    lst.write_text("".join(f"file '{f.name}'\n" for f in files), encoding="utf-8")
    run_ffmpeg(["-f", "concat", "-safe", "0", "-i", lst.name, "-c", "copy", "-movflags", "+faststart",
                "edited.mp4"], cwd=proj.dir)
    for f in files:
        f.unlink(missing_ok=True)
    lst.unlink(missing_ok=True)


def recap(proj, ai, target):
    """Tóm tắt phim dài: xem từng phần → chọn cảnh → cắt ghép."""
    style, note = _style(proj)
    dur = float(proj.meta["duration"])
    parts = _chunks(dur)
    moments, summary = [], ""
    for i, (start, size) in enumerate(parts):
        progress(STEP, 3 + i / len(parts) * 55, f"Gemini đang xem phim phần {i + 1}/{len(parts)}...")
        proxy = proj.path(f"proxy_{i}.mp4")
        _proxy(proj, start, size, proxy)
        try:
            part = ai.upload(proxy)
            ctx = f"Tóm tắt các phần trước: {summary}" if summary else ""
            data = ai.ask(MOMENTS.format(idx=i + 1, total=len(parts), dur=size, context=ctx), parts=[part])
        finally:
            ai.cleanup()
            proxy.unlink(missing_ok=True)
        for m in data.get("moments", []):
            try:
                moments.append({"start": round(float(m["start"]) + start, 1), "end": round(float(m["end"]) + start, 1),
                                "desc": str(m.get("desc", "")), "importance": int(m.get("importance", 3))})
            except (KeyError, TypeError, ValueError):
                continue
        summary = (summary + " " + str(data.get("summary", ""))).strip()
    if not moments:
        raise RuntimeError("Gemini không tìm được cảnh nào trong phim")

    progress(STEP, 60, "Gemini đang chọn cảnh và viết lời kể...")
    data = ai.ask(RECAP.format(target=int(target), style=style, note=note, summary=summary, cps=CPS,
                               moments=json.dumps(moments, ensure_ascii=False)))
    scenes = []
    for s in sorted(data.get("scenes", []), key=lambda s: float(s.get("start", 0))):
        try:
            a, b = max(0.0, float(s["start"])), min(dur, float(s["end"]))
        except (KeyError, TypeError, ValueError):
            continue
        if scenes and a < scenes[-1]["end"]:
            a = scenes[-1]["end"]
        if b - a >= 2:
            scenes.append({"start": a, "end": b, "text": str(s.get("text", "")).strip()})
    if not scenes:
        raise RuntimeError("Gemini không chọn được cảnh nào")

    _cut(proj, [(s["start"], s["end"]) for s in scenes])
    # lời kể đặt vào timeline của video đã cắt ghép
    lines, t = [], 0.0
    for s in scenes:
        d = s["end"] - s["start"]
        if s["text"]:
            lines.append({"start": round(t, 2), "end": round(t + d, 2), "text": s["text"]})
        t += d
    (proj.path("scenes.json")).write_text(json.dumps(scenes, ensure_ascii=False, indent=1), encoding="utf-8")
    return lines, summary, t


PLACE = """Đây là một video dài {dur:.0f} giây và lời thuyết minh tiếng Việt người dùng đã viết sẵn, gồm {n} câu theo đúng thứ tự.
Hãy xem video và chọn thời điểm BẮT ĐẦU đọc (giây, tính từ đầu video) cho từng câu, sao cho câu nói khớp với hình đang chiếu.
- Giữ đúng thứ tự câu, không đổi nội dung câu.
- Mỗi câu cần khoảng "need" giây để đọc; câu sau chỉ bắt đầu khi câu trước đã đọc xong.
- Nếu tổng thời gian đọc ngắn hơn video, phân bổ khoảng nghỉ hợp lý theo cảnh; câu cuối phải đọc xong trước khi video hết.
Các câu (JSON): {items}
Trả về JSON: [{{"id": 1, "start": 0.0}}]"""

READ_CPS = 15  # tốc độ đọc tiếng Việt bình thường của giọng máy (ký tự/giây)


def split_script(text):
    """Tách lời thuyết minh thành từng câu: theo dòng, rồi theo dấu kết thúc câu."""
    out = []
    for para in re.split(r"\n+", text or ""):
        for s in re.split(r"(?<=[.!?…])\s+", para.strip()):
            if s.strip():
                out.append(s.strip())
    return out


def _spread(needs, dur):
    """Chia đều: các câu đọc nối nhau, phần thời gian dư chia thành khoảng nghỉ giữa các câu."""
    spare = max(0.0, dur - sum(needs) - 0.3)
    gap = min(4.0, spare / max(1, len(needs)))
    starts, t = [], 0.3
    for d in needs:
        starts.append(t)
        t += d + gap
    return starts


def _fix_order(starts, needs, dur):
    """Ép thứ tự và khoảng cách tối thiểu để câu sau không đè lên câu trước."""
    out, prev_end = [], 0.0
    for s, d in zip(starts, needs):
        s = max(float(s), prev_end)
        out.append(round(min(s, max(0.0, dur - 0.5)), 2))
        prev_end = out[-1] + d * 0.95
    return out


def place_user_script(proj, settings):
    sentences = split_script(proj.opt.get("review_user_script", ""))
    if not sentences:
        raise RuntimeError("Chưa nhập lời thuyết minh (Tùy chọn → Lời thuyết minh của bạn)")
    dur = float(proj.duration)
    needs = [max(0.8, len(s) / READ_CPS) for s in sentences]
    if sum(needs) > dur * 1.3:
        log(f"Lời thuyết minh cần khoảng {sum(needs):.0f}s để đọc nhưng video chỉ dài {dur:.0f}s: "
            "giọng đọc sẽ phải nhanh hơn, nên rút bớt lời")
    starts = None
    try:
        ai = Gemini(settings, temperature=0.2)
        progress(STEP, 10, "Đang chuẩn bị video cho AI xem...")
        proxy = proj.path("proxy_place.mp4")
        _proxy(proj, 0, dur, proxy)
        try:
            progress(STEP, 30, "Gemini đang xem video để đặt lời thuyết minh đúng cảnh...")
            part = ai.upload(proxy)
            data = ai.ask(PLACE.format(dur=dur, n=len(sentences), items=json.dumps(
                [{"id": i, "text": s, "need": round(d, 1)} for i, (s, d) in enumerate(zip(sentences, needs), 1)],
                ensure_ascii=False)), parts=[part])
        finally:
            ai.cleanup()
            proxy.unlink(missing_ok=True)
        got = {int(x["id"]): float(x["start"]) for x in data if isinstance(x, dict) and "id" in x and "start" in x}
        if len(got) >= len(sentences) * 0.8:
            fallback = _spread(needs, dur)
            starts = [got.get(i, fallback[i - 1]) for i in range(1, len(sentences) + 1)]
    except Exception as e:
        log(f"Không nhờ AI đặt vị trí được ({str(e)[:150]}) → chia đều lời thuyết minh theo độ dài câu")
    if starts is None:
        starts = _spread(needs, dur)
    starts = _fix_order(starts, needs, dur)
    lines = []
    for k, (s, d, text) in enumerate(zip(starts, needs, sentences)):
        nxt = starts[k + 1] if k + 1 < len(starts) else dur
        lines.append({"start": s, "end": round(max(s + 0.5, min(s + d, nxt)), 2), "text": text})
    return lines, ""


def run(proj: Project, settings):
    fresh = Project(proj.dir).meta
    if proj.opt.get("review_source") == "user":
        lines, summary = place_user_script(proj, settings)
        fresh["edited"] = False
        proj.path("edited.mp4").unlink(missing_ok=True)
        log(f"Đã đặt {len(lines)} câu thuyết minh vào video — có thể chỉnh giây bắt đầu của từng câu rồi bấm "
            "\"Lồng tiếng lại & xuất\"")
        _finish(proj, fresh, lines, summary)
        return
    ai = Gemini(settings, temperature=0.7)
    target = float(proj.opt.get("review_minutes") or 0) * 60
    if target > 0 and target < float(proj.meta["duration"]) * 0.9:
        lines, summary, new_dur = recap(proj, ai, target)
        fresh.update(edited=True, edited_duration=round(new_dur, 3))
        log(f"Đã cắt ghép {len(json.loads(proj.path('scenes.json').read_text(encoding='utf-8')))} cảnh, "
            f"video mới dài {new_dur / 60:.1f} phút")
    else:
        lines, summary = narrate_full(proj, ai)
        fresh["edited"] = False
        proj.path("edited.mp4").unlink(missing_ok=True)
    _finish(proj, fresh, lines, summary)


def _finish(proj, fresh, lines, summary):
    fresh["review_summary"] = summary
    proj.meta = fresh
    proj.save()
    # các file của lần chạy trước không còn khớp timeline mới
    for f in ("background.wav", "dub_voice.wav", "cover_preview.jpg"):
        proj.path(f).unlink(missing_ok=True)
    segs = [{"id": i, "start": l["start"], "end": l["end"], "text": "", "vi": l["text"]}
            for i, l in enumerate(lines, 1)]
    proj.save_segments(segs)
    proj.mark(STEP)
    progress(STEP, 100, f"Xong: {len(segs)} câu kể")
