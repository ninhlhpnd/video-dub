"""Bước 5: trộn giọng Việt + nền gốc + nhạc nền (tự hạ nhạc khi có lời), chèn phụ đề, xuất mp4."""
from pathlib import Path

from .common import Project, log, progress, run_ffmpeg_progress

STEP = "render"


def _ts(t, sep):
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    if sep == ".":  # ASS: h:mm:ss.cc
        return f"{int(h)}:{int(m):02d}:{s:05.2f}"
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int(round((s % 1) * 1000)) % 1000:03d}"


def _chunks(text, limit):
    """Chia câu dài thành các đoạn ≤ limit ký tự, ưu tiên ngắt ở dấu câu rồi mới tới khoảng trắng."""
    out, cur = [], []
    for w in text.split():
        if cur and len(" ".join(cur + [w])) > limit:
            out.append(" ".join(cur))
            cur = []
        cur.append(w)
        if w[-1] in ",.;:!?…" and len(" ".join(cur)) >= limit * 0.6:  # đủ dài thì ngắt ở dấu câu
            out.append(" ".join(cur))
            cur = []
    if cur:
        out.append(" ".join(cur))
    return out


def _cues(segs, duration, limit):
    items = [s for s in segs if s.get("vi", "").strip()]
    cues = []
    for i, s in enumerate(items):
        start = s.get("v_start", s["start"])
        voice_end = s.get("v_end", s["end"])
        nxt = items[i + 1].get("v_start", items[i + 1]["start"]) if i + 1 < len(items) else duration
        end = max(start + 0.3, min(max(voice_end, s["end"]), nxt))
        parts = _chunks(s["vi"].strip(), limit)
        # chia thời gian theo độ dài chữ, khớp với lúc giọng đọc tới đoạn đó
        speak = max(0.3, min(voice_end, end) - start)
        total = sum(len(p) for p in parts)
        t = start
        for k, part in enumerate(parts):
            b = end if k == len(parts) - 1 else t + speak * len(part) / total
            cues.append((t, b, part))
            t = b
    return cues


def _ass_color(rgb):
    rgb = (rgb or "FFFFFF").lstrip("#").upper().rjust(6, "0")
    return f"&H00{rgb[4:6]}{rgb[2:4]}{rgb[0:2]}"


def cover_box(proj: Project):
    """Dải che phụ đề gốc tính bằng pixel (y, cao), hoặc None nếu không bật."""
    o, h = proj.opt, proj.meta["height"]
    top, bottom = float(o.get("cover_top") or 0), float(o.get("cover_bottom") or 0)
    if not o.get("cover_subs") or bottom - top < 0.01:
        return None
    y = int(top * h) // 2 * 2
    return y, max(2, (int(bottom * h) - y) // 2 * 2)


def write_subs(proj: Project, segs):
    w, h = proj.meta["width"], proj.meta["height"]
    size = int(proj.opt.get("sub_font_size") or round(min(w, h) * 0.055))
    box = cover_box(proj)
    if box:
        # chữ đậm màu đặt giữa dải che, cỡ chữ vừa đủ 2 dòng trong dải
        size = max(12, min(size, int(box[1] / 2.5)))
        color, outline, shadow, pos = _ass_color(proj.opt.get("cover_text_color")), 0, 0, \
            f"{{\\an5\\pos({w // 2},{box[0] + box[1] // 2})}}"
    else:
        color, outline, shadow, pos = "&H00FFFFFF", max(2, size // 18), 1, ""
    per_line = int(w * 0.88 / (size * 0.52))
    cues = _cues(segs, proj.meta["duration"], per_line * 2)
    ass = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {w}", f"PlayResY: {h}", "WrapStyle: 0", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,Arial,{size},{color},{color},&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,"
        f"{outline},{shadow},2,{int(w * 0.06)},{int(w * 0.06)},{int(h * 0.06)},1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    srt = []
    for n, (a, b, text) in enumerate(cues, 1):
        clean = text.replace("{", "(").replace("}", ")").replace("\n", " ")
        ass.append(f"Dialogue: 0,{_ts(a, '.')},{_ts(b, '.')},Default,,0,0,0,,{pos}{clean}")
        srt += [str(n), f"{_ts(a, ',')} --> {_ts(b, ',')}", clean, ""]
    proj.path("subs.ass").write_text("\n".join(ass) + "\n", encoding="utf-8-sig")
    proj.path("subs_vi.srt").write_text("\n".join(srt), encoding="utf-8-sig")


def run(proj: Project, settings):
    if not proj.path("dub_voice.wav").exists():
        raise RuntimeError("Chưa có giọng lồng tiếng, hãy chạy bước lồng tiếng trước")
    o = proj.opt
    segs = proj.segments()
    write_subs(proj, segs)

    inputs = ["-i", proj.meta["video"], "-i", "dub_voice.wav"]
    fmt = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
    music = o.get("music", "").strip()
    use_music = bool(music) and Path(music).exists()
    use_bg = o.get("keep_background") and proj.path("background.wav").exists()
    if o.get("keep_background") and not use_bg:
        log("Chưa tách nền gốc nên video sẽ không có tiếng động gốc")
    if music and not use_music:
        log(f"Không thấy file nhạc: {music}")

    graph = [f"[1:a]{fmt},volume={o['voice_volume']}" + ("[voice];[voice]asplit=2[v][vsc]" if use_music else "[v]")]
    mix = ["[v]"]
    n = 2
    if use_bg:
        inputs += ["-i", "background.wav"]
        graph.append(f"[{n}:a]{fmt},volume={o['bg_volume']}[bg]")
        mix.append("[bg]")
        n += 1
    if use_music:
        inputs += ["-stream_loop", "-1", "-i", music]
        graph.append(f"[{n}:a]{fmt},volume={o['music_volume']}[mus]")
        graph.append("[mus][vsc]sidechaincompress=threshold=0.03:ratio=6:attack=20:release=400[duck]")
        mix.append("[duck]")
    graph.append("".join(mix) + f"amix=inputs={len(mix)}:duration=first:normalize=0,alimiter=limit=0.95[aout]")

    out = proj.path("output.mp4")
    base = [*inputs, "-filter_complex", ";".join(graph), "-map", "0:v:0", "-map", "[aout]",
            "-c:a", "aac", "-b:a", "192k", "-t", f"{proj.meta['duration']:.3f}", "-movflags", "+faststart"]
    vf = []
    box = cover_box(proj)
    if box:
        color = (o.get("cover_color") or "FFD400").lstrip("#")
        vf.append(f"drawbox=x=0:y={box[0]}:w=iw:h={box[1]}:color=0x{color}@1:t=fill")
    if o.get("burn_subs"):
        vf.append("subtitles=subs.ass")
    if vf:
        vf = ["-vf", ",".join(vf)]
        attempts = [
            [*vf, "-c:v", "h264_nvenc", "-preset", "p5", "-cq", "21", "-pix_fmt", "yuv420p"],
            [*vf, "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p"],
        ]
    else:
        attempts = [["-c:v", "copy"], ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p"]]

    progress(STEP, 0, "Đang xuất video...")
    for k, venc in enumerate(attempts):
        try:
            run_ffmpeg_progress([*base, *venc, out.name], proj.meta["duration"], STEP, cwd=proj.dir)
            break
        except RuntimeError as e:
            if k == len(attempts) - 1:
                raise
            log(f"Cách xuất nhanh không được ({str(e)[:150]}), thử cách khác...")
    proj.mark(STEP)
    progress(STEP, 100, f"Xong: {out}")
