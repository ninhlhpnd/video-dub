"""Bước 5: trộn giọng Việt + nền gốc + nhạc nền (tự hạ nhạc khi có lời), chèn phụ đề, xuất mp4."""
from pathlib import Path

from .common import Project, log, probe, progress, run_ffmpeg_progress, sdr_filter, wants_separate

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
    cues = _cues(segs, proj.duration, per_line * 2)
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


CORNERS = {"top-left": 7, "top-right": 9, "bottom-left": 1, "bottom-right": 3}


def overlay_filters(proj: Project):
    """Chữ Trung trên video: trả về (danh sách bộ lọc delogo xóa chữ, có ghi overlays.ass hay không).
    overlays.ass chứa chữ Việt / tên kênh đặt đúng chỗ chữ cũ, và tên kênh gắn ở góc (nếu bật)."""
    from . import overlays
    o = proj.opt
    W, H = proj.meta["width"], proj.meta["height"]
    channel = (o.get("channel_name") or "").strip()
    items = overlays.load(proj) if o.get("fix_overlays") else []
    delogo, events = [], []
    for it in items:
        act = it.get("action", "translate")
        if act == "keep":
            continue
        text = it.get("vi", "").strip() if act == "translate" else channel if act == "channel" else ""
        bw, bh = it["w"] * W, it["h"] * H
        pad_y, pad_x = max(4, bh * 0.3), max(4, bh * 0.3)
        x0, y0 = max(1, int(it["x"] * W - pad_x)), max(1, int(it["y"] * H - pad_y))
        x1, y1 = min(W - 2, int(it["x"] * W + bw + pad_x)), min(H - 2, int(it["y"] * H + bh + pad_y))
        if x1 - x0 < 4 or y1 - y0 < 4:
            continue
        a, b = max(0.0, it["start"] - 0.1), it["end"] + 0.15
        delogo.append(f"delogo=x={x0}:y={y0}:w={x1 - x0}:h={y1 - y0}:enable='between(t,{a:.2f},{b:.2f})'")
        if text:
            # cỡ chữ theo chiều cao chữ gốc thật (trung vị qua các khung hình, không phải khung gộp);
            # chữ Việt dài hơn thì thu nhỏ cho vừa khoảng 92% bề ngang video
            th = it.get("th", it["h"]) * H
            fs = max(th * 0.6, min(th * 0.9, W * 0.92 / max(1, len(text) * 0.55)))
            est = len(text) * fs * 0.55
            cx0 = it.get("cx", it["x"] + it["w"] / 2) * W
            # chữ gốc canh trái (nằm sát mép trái) thì chữ Việt cũng canh trái theo mép đó
            left = it["x"] * W
            cx = left + est / 2 if left < W * 0.15 and est > it.get("tw", it["w"]) * W else cx0
            cx = min(max(cx, est / 2 + W * 0.02), W - est / 2 - W * 0.02)
            cy = it.get("cy", it["y"] + it["h"] / 2) * H
            clean = text.replace("{", "(").replace("}", ")")
            events.append(f"Dialogue: 1,{_ts(a, '.')},{_ts(b, '.')},Ov,,0,0,0,,"
                          f"{{\\an5\\pos({cx:.0f},{cy:.0f})\\fs{fs:.0f}\\bord{max(2, fs / 14):.1f}}}{clean}")
    if o.get("channel_watermark") and channel:
        an = CORNERS.get(o.get("channel_pos"), 9)
        mx, my = W * 0.04, H * 0.035
        x = mx if an in (7, 1) else W - mx
        y = my if an in (7, 9) else H - my
        events.append(f"Dialogue: 2,{_ts(0, '.')},{_ts(proj.duration + 1, '.')},Wm,,0,0,0,,"
                      f"{{\\an{an}\\pos({x:.0f},{y:.0f})}}{channel.replace('{', '(').replace('}', ')')}")
    if not events:
        return delogo, False
    wm = round(min(W, H) * 0.04)
    ass = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 0", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Ov,Arial,40,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,3,1,5,10,10,10,1",
        f"Style: Wm,Arial,{wm},&H40FFFFFF,&H40FFFFFF,&H60000000,&H00000000,1,0,0,0,100,100,0,0,1,"
        f"{max(1, wm // 14)},0,9,10,10,10,1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        *events,
    ]
    proj.path("overlays.ass").write_text("\n".join(ass) + "\n", encoding="utf-8-sig")
    return delogo, True


def run(proj: Project, settings):
    if not proj.path("dub_voice.wav").exists():
        raise RuntimeError("Chưa có giọng lồng tiếng, hãy chạy bước lồng tiếng trước")
    o = proj.opt
    segs = proj.segments()
    write_subs(proj, segs)

    inputs = ["-i", proj.video, "-i", "dub_voice.wav"]
    fmt = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
    music = o.get("music", "").strip()
    use_music = bool(music) and Path(music).exists()
    use_bg = wants_separate(o) and proj.path("background.wav").exists()
    if wants_separate(o) and not use_bg:
        log("Chưa tách nền gốc nên video sẽ không có tiếng động gốc")
    if music and not use_music:
        log(f"Không thấy file nhạc: {music}")

    review = o.get("mode") == "review"
    # review không tách nền thì giữ nguyên tiếng phim gốc, nhỏ đi khi có lời kể
    use_orig = review and not use_bg and proj.meta.get("has_audio")

    graph, mix, ducked = [], [], []  # ducked: (nhãn, mức nén) — tự hạ âm lượng khi có giọng đọc
    n = 2
    if use_bg:
        inputs += ["-i", "background.wav"]
        graph.append(f"[{n}:a]{fmt},volume={o['bg_volume']}[bg]")
        (ducked.append(("bg", 8)) if review else mix.append("[bg]"))
        n += 1
    elif use_orig:
        graph.append(f"[0:a]{fmt},volume={o.get('orig_volume', 0.3)}[bg]")
        ducked.append(("bg", 8))
    if use_music:
        inputs += ["-stream_loop", "-1", "-i", music]
        graph.append(f"[{n}:a]{fmt},volume={o['music_volume']}[mus]")
        ducked.append(("mus", 6))
    voice = f"[1:a]{fmt},volume={o['voice_volume']}"
    if ducked:
        voice += f",asplit={len(ducked) + 1}[v]" + "".join(f"[sc{k}]" for k in range(len(ducked)))
    else:
        voice += "[v]"
    graph.insert(0, voice)
    mix.insert(0, "[v]")
    for k, (label, ratio) in enumerate(ducked):
        graph.append(f"[{label}][sc{k}]sidechaincompress=threshold=0.03:ratio={ratio}:attack=20:release=400[{label}d]")
        mix.append(f"[{label}d]")
    graph.append("".join(mix) + f"amix=inputs={len(mix)}:duration=first:normalize=0,alimiter=limit=0.95[aout]")

    out = proj.path("output.mp4")
    base = [*inputs, "-filter_complex", ";".join(graph), "-map", "0:v:0", "-map", "[aout]",
            "-c:a", "aac", "-b:a", "192k", "-t", f"{proj.duration:.3f}", "-movflags", "+faststart"]
    vf = []
    src = probe(proj.video)
    if src["hdr"]:  # video quay HDR (iPhone...): đổi về màu thường, không thì màu bị bạc/sáng trưng
        vf.append(sdr_filter(src["color_transfer"]))
    # video tốc độ khung hình bất thường (vd 180-240 hình/giây, không đều): đưa về 30 hình/giây đều cho mượt, nhẹ file
    if src.get("fps", 0) > 61:
        vf.append("fps=30")
    delogo, has_ov = overlay_filters(proj)
    vf += delogo
    box = cover_box(proj)
    if box:
        color = (o.get("cover_color") or "FFD400").lstrip("#")
        vf.append(f"drawbox=x=0:y={box[0]}:w=iw:h={box[1]}:color=0x{color}@1:t=fill")
    if o.get("burn_subs"):
        vf.append("subtitles=subs.ass")
    if has_ov:
        vf.append("subtitles=overlays.ass")
    if vf:
        vf = ["-vf", ",".join(vf)]
        attempts = [
            [*vf, "-c:v", "h264_nvenc", "-preset", "p5", "-cq", "21", "-pix_fmt", "yuv420p"],
            [*vf, "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p"],
        ]
    else:
        reenc = ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p"]
        # chỉ sao chép nguyên hình khi đã là H.264 (HEVC/AV1 không phát được trên trình xem mặc định của Windows)
        attempts = [["-c:v", "copy"], reenc] if src["vcodec"] == "h264" else [reenc]

    progress(STEP, 0, "Đang xuất video...")
    for k, venc in enumerate(attempts):
        try:
            run_ffmpeg_progress([*base, *venc, out.name], proj.duration, STEP, cwd=proj.dir)
            break
        except RuntimeError as e:
            if k == len(attempts) - 1:
                raise
            if "nvenc" in str(e).lower():
                log("Không dùng được card NVIDIA để xuất nhanh (driver card đồ họa cũ) → xuất bằng CPU, chậm hơn "
                    "nhưng video y hệt. Cập nhật driver NVIDIA mới nhất để xuất nhanh hơn.")
            else:
                log(f"Cách xuất nhanh không được ({str(e)[:150]}), thử cách khác...")
    proj.mark(STEP)
    progress(STEP, 100, f"Xong: {out}")
