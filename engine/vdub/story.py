"""Chế độ Truyện kể: AI viết truyện, vẽ ảnh minh họa, đọc thành lời, ghép video ngang 16:9 cho YouTube.

Mọi dữ liệu nằm ở story.json để app cho duyệt/sửa giữa các bước:
  outline {title, logline, setting, characters[], art_style, chapters[{title, summary}]}
  chapters [{title, text}]          scenes [{id, chapter, para, prompt, image, image_key, seed, start}]
  youtube {title, titles[], description, hashtags[], tags[]}      cost (USD đã dùng)
"""
import asyncio
import hashlib
import json
import math
import re
import subprocess
import time
from pathlib import Path

import numpy as np

from .common import DEFAULT_OPTIONS, FFMPEG, NO_WINDOW, ROOT, Project, log, progress
from .writer import make_writer

CPS = 14.5           # ký tự/giây khi đọc truyện (gồm cả khoảng nghỉ), dùng để tính độ dài cần viết
W, H, FPS = 1920, 1080, 25
SR = 24000
PAUSE = {"sentence": 0.25, "para": 0.6, "chapter": 1.6}
FADE = 1.2           # giây chuyển cảnh mờ dần


# ---------- dữ liệu ----------

def load(proj):
    f = proj.path("story.json")
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def save(proj, st):
    tmp = proj.path("story.tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(proj.path("story.json"))


def create(workspace, options):
    stamp = time.strftime("%Y%m%d_%H%M%S")
    folder = Path(workspace) / f"truyen_{stamp}"
    folder.mkdir(parents=True, exist_ok=True)
    opt = {**DEFAULT_OPTIONS, "voice": "vieneu:Thiện Minh", "music_volume": 0.10, **options, "mode": "story"}
    meta = {"name": (opt.get("story_topic") or "Truyện mới")[:60], "video": "", "created": stamp, "duration": 0,
            "width": W, "height": H, "has_audio": False, "steps": {}, "options": opt}
    (folder / "project.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return Project(folder)


def paragraphs(text):
    return [p.strip() for p in re.split(r"\n+", text or "") if p.strip()]


def _spend(st, ai):
    st["cost"] = round(st.get("cost", 0) + ai.cost, 4)
    ai.cost = 0.0


def _invalidate(proj, step):
    """Làm lại một bước thì các bước sau phải làm lại."""
    from .common import PIPELINES
    meta = json.loads(proj.meta_file.read_text(encoding="utf-8"))
    order = PIPELINES["story"]
    for s in order[order.index(step):]:
        meta.get("steps", {}).pop(s, None)
    proj.meta = meta
    proj.meta["options"] = {**DEFAULT_OPTIONS, **meta.get("options", {})}
    proj.save()


# ---------- viết ----------

SYSTEM = """Bạn là nhà văn Việt Nam viết truyện audio cho kênh YouTube kể chuyện.
Yêu cầu nghề nghiệp:
- Truyện hoàn toàn nguyên bản: không phóng tác, không mượn nhân vật hay cốt truyện của tác phẩm có sẵn, không dùng người thật.
- Có chủ đề rõ, nhân vật có mong muốn và mâu thuẫn riêng, hành động có nguyên nhân và hậu quả. Mở đầu kéo người nghe vào một tình huống cụ thể; kết thúc trả lời được những gì đã gài.
- Chi tiết cụ thể, đời sống Việt Nam chính xác (tên, nghề, món ăn, địa danh, cách xưng hô), lời thoại tự nhiên. Tránh sáo ngữ, tránh lặp ý, tránh giảng đạo lý, tránh câu kết kiểu tổng kết ở mỗi chương.
- DỰNG CẢNH, không tóm tắt: mỗi chương bám một đến hai cảnh liền mạch, hành động từng bước, có âm thanh, mùi vị, đối thoại qua lại nhiều lượt. Cho thấy cảm xúc qua cử chỉ và lời nói, không gọi tên cảm xúc ("nghẹn ngào", "vỡ òa"), không thuật lại kiểu "bà kể rằng...".
- Chỉ dùng tiếng Việt trong truyện, không chen từ tiếng Anh.
- Văn để NGHE: câu vừa phải, dễ theo dõi, đổi nhịp; không gạch đầu dòng, không ký hiệu, không viết tắt, số và đơn vị viết bằng chữ; lời thoại đặt trong ngoặc kép và rõ ai nói.
- Phù hợp quảng cáo YouTube: không tả bạo lực máu me, tình dục, tự hại, thù ghét, mê tín trục lợi, lời khuyên y tế hay tài chính nguy hiểm, thông tin sai sự thật."""


def _obj(props):
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def _arr(item):
    return {"type": "array", "items": item}


S, I = {"type": "string"}, {"type": "integer"}
OUTLINE_SCHEMA = _obj({
    "title": S, "logline": S, "theme": S, "setting": S, "art_style": S,
    "characters": _arr(_obj({"name": S, "role": S, "voice": S, "look_en": S})),
    "chapters": _arr(_obj({"title": S, "summary": S})),
})
REVIEW_SCHEMA = _obj({"issues": _arr(_obj({"chapter": I, "problem": S, "fix": S})), "verdict": S})
SCENES_SCHEMA = _obj({
    "scenes": _arr(_obj({"chapter": I, "para": I, "prompt": S})),
    "titles": _arr(S), "description": S, "hashtags": _arr(S), "tags": _arr(S),
})


def _others(proj):
    """Các truyện đã làm, để truyện mới không na ná (YouTube phạt nội dung lặp lại hàng loạt)."""
    out = []
    for f in sorted(proj.dir.parent.glob("*/story.json"), reverse=True)[:30]:
        if f.parent == proj.dir:
            continue
        try:
            o = json.loads(f.read_text(encoding="utf-8")).get("outline")
            if o:
                out.append(f"- {o['title']}: {o['logline']}")
        except Exception:
            continue
    return "\n".join(out)


def _minutes(proj):
    return max(3, min(40, int(proj.opt.get("story_minutes") or 15)))


def outline(proj, settings):
    _invalidate(proj, "outline")
    ai, o, mins = make_writer(settings), proj.opt, _minutes(proj)
    n = max(3, min(12, round(mins / 3)))
    others = _others(proj)
    prompt = (f"Lập dàn ý cho một truyện audio dài khoảng {mins} phút, đúng {n} chương.\n"
              f"Thể loại: {o.get('story_genre') or 'tự chọn'}\n"
              f"Chủ đề / ý tưởng của tác giả kênh: {o.get('story_topic') or 'tự chọn một ý tưởng thật mới'}\n"
              f"Giọng văn: {o.get('story_tone') or 'hợp với thể loại'}\n"
              f"Yêu cầu thêm: {o.get('story_note') or 'không có'}\n"
              + (f"\nKênh đã có các truyện sau, truyện mới phải khác hẳn về cốt truyện, bối cảnh và nhân vật:\n{others}\n"
                 if others else "")
              + f"\nMỗi chương chỉ đọc khoảng {mins / n:.0f} phút nên cốt truyện phải gọn, đủ để dựng thành cảnh: "
                "ít nhân vật, mỗi chương một biến cố chính. Tên chương không ghi chữ \"Chương\" và số thứ tự."
                "\nMỗi summary 4-6 câu: chuyện gì xảy ra, vì sao, thông tin nào được gài hoặc hé lộ, cảm xúc cuối chương."
                "\nlook_en: mô tả ngoại hình nhân vật bằng tiếng Anh, dưới 15 từ, để vẽ minh họa nhất quán."
                "\nart_style: phong cách tranh minh họa chung bằng tiếng Anh, dưới 12 từ (tranh vẽ, không phải ảnh chụp).")
    progress("outline", 5, "AI đang nghĩ cốt truyện và lập dàn ý...")
    data = ai.ask(SYSTEM, [{"role": "user", "content": prompt}], OUTLINE_SCHEMA,
                  on_text=lambda k: progress("outline", min(95, 10 + k / 60), "AI đang lập dàn ý..."))
    for c in data["chapters"]:
        c["title"] = re.sub(r"(?i)^chương\s*\d+\s*[:.\-–]?\s*", "", c["title"]).strip() or c["title"]
    st = {"cost": load(proj).get("cost", 0), "outline": data}
    _spend(st, ai)
    save(proj, st)
    proj.meta["name"] = data["title"][:80]
    proj.save()
    proj.mark("outline")
    progress("outline", 100, f"Xong: {data['title']} ({len(data['chapters'])} chương)")


def _history(st, upto):
    """Hội thoại gồm dàn ý + các chương đã có (kể cả phần người dùng đã sửa)."""
    o = st["outline"]
    msgs = [{"role": "user", "content": "DÀN Ý ĐÃ DUYỆT:\n" + json.dumps(o, ensure_ascii=False)
             + "\n\n" + _chapter_request(st, 0)}]
    for i in range(upto):
        msgs.append({"role": "assistant", "content": st["chapters"][i]["text"]})
        msgs.append({"role": "user", "content": _chapter_request(st, i + 1)})
    return msgs


def _chapter_request(st, i):
    ch = st["outline"]["chapters"]
    last = i == len(ch) - 1
    return (f"Viết trọn chương {i + 1}/{len(ch)}: \"{ch[i]['title']}\", khoảng {st['chars']} ký tự "
            f"(cho phép lệch 15%). Chia đoạn 2-5 câu, mỗi đoạn một dòng. Chỉ trả về phần truyện, không ghi lại tên chương. "
            + ("Đây là chương cuối: khép lại trọn vẹn câu chuyện." if last else "Dừng ở chỗ khiến người nghe muốn nghe tiếp."))


def write(proj, settings):
    st = load(proj)
    if not st.get("outline"):
        raise RuntimeError("Chưa có dàn ý")
    n = len(st["outline"]["chapters"])
    st["chars"] = int(_minutes(proj) * 60 * CPS / n)
    # làm lại từ đầu nếu bước này đã xong trước đó; còn dở (bị dừng/lỗi) thì viết tiếp chương còn thiếu
    if proj.meta.get("steps", {}).get("write") == "done":
        st["chapters"] = []
    _invalidate(proj, "write")
    st.setdefault("chapters", [])
    ai = make_writer(settings)
    for i in range(len(st["chapters"]), n):
        title = st["outline"]["chapters"][i]["title"]
        text = ai.ask(SYSTEM, _history(st, i), on_text=lambda k, i=i, t=title: progress(
            "write", (i + min(0.97, k / st["chars"])) / n * 100, f"AI đang viết chương {i + 1}/{n}: {t}"))
        st["chapters"].append({"title": title, "text": text})
        _spend(st, ai)
        save(proj, st)
    proj.mark("write")
    total = sum(len(c["text"]) for c in st["chapters"])
    progress("write", 100, f"Xong {n} chương, khoảng {total / CPS / 60:.0f} phút đọc")


def _draft(st, numbered=False):
    out = []
    for i, c in enumerate(st["chapters"], 1):
        body = "\n".join(f"[{k}] {p}" for k, p in enumerate(paragraphs(c["text"]), 1)) if numbered else c["text"]
        out.append(f"=== CHƯƠNG {i}: {c['title']} ===\n{body}")
    return "\n\n".join(out)


def polish(proj, settings):
    st = load(proj)
    if not st.get("chapters"):
        raise RuntimeError("Chưa có nội dung truyện")
    _invalidate(proj, "polish")
    ai = make_writer(settings)
    n = len(st["chapters"])
    progress("polish", 5, "AI đang đọc lại cả truyện như một biên tập viên...")
    base = {"role": "user", "content": "BẢN THẢO TRUYỆN:\n\n" + _draft(st) + "\n\nĐọc như biên tập viên khó tính. "
            "Chỉ nêu lỗi thật sự làm hỏng truyện: mâu thuẫn tình tiết, nhân vật hành động vô lý, thắt nút không được mở, "
            "đoạn dài dòng hoặc lặp, lời thoại giả, câu sáo, xưng hô lẫn lộn, chỗ không phù hợp quảng cáo YouTube. "
            "Mỗi lỗi ghi số chương, vấn đề và cách sửa cụ thể. Không có lỗi thì để issues rỗng. verdict: nhận xét chung 2-3 câu."}
    rv = ai.ask(SYSTEM, [base], REVIEW_SCHEMA)
    by = {}
    for x in rv["issues"]:
        if 1 <= x["chapter"] <= n:
            by.setdefault(x["chapter"], []).append(x)
    log(f"Biên tập: {rv['verdict']}")
    for k, (c, xs) in enumerate(sorted(by.items())):
        progress("polish", 25 + k / len(by) * 70, f"AI đang sửa chương {c} ({len(xs)} điểm)...")
        notes = "\n".join(f"- {x['problem']} → {x['fix']}" for x in xs)
        text = ai.ask(SYSTEM, [{"role": "user", "content": "BẢN THẢO TRUYỆN:\n\n" + _draft(st)
                      + f"\n\nViết lại trọn chương {c}, sửa các điểm sau, giữ độ dài và phần đang hay, khớp các chương khác:\n"
                      + notes + "\n\nChỉ trả về phần truyện của chương đó, mỗi đoạn một dòng, không ghi tên chương."}])
        st["chapters"][c - 1]["text"] = text
        _spend(st, ai)
        save(proj, st)
    st["review"] = {"verdict": rv["verdict"], "fixed": len(rv["issues"])}
    _spend(st, ai)
    save(proj, st)
    proj.mark("polish")
    progress("polish", 100, f"Xong: sửa {len(rv['issues'])} điểm ở {len(by)} chương")


def _episode_rule(proj):
    """Tập của bộ truyện dài: tiêu đề có tên bộ và số tập để người xem tìm tập trước/sau."""
    ep = proj.opt.get("story_episode")
    if not ep:
        return ""
    return (f"Đây là tập {ep['n']} (chương {ep['from']}-{ep['to']}) của bộ truyện dài kỳ \"{ep['title']}\". "
            f"Mỗi tiêu đề phải bắt đầu bằng \"{ep['title']} - Tập {ep['n']}: \" rồi tới phần gây tò mò của tập này. "
            "Mô tả nhắc ngắn đây là tập tiếp nối.\n")


def scenes(proj, settings):
    st = load(proj)
    if not st.get("chapters"):
        raise RuntimeError("Chưa có nội dung truyện")
    _invalidate(proj, "scenes")
    ai = make_writer(settings)
    total = max(len(st["chapters"]), min(60, int(proj.opt.get("story_images") or 15)))
    o = st["outline"]
    looks = "\n".join(f"- {c['name']}: {c['look_en']}" for c in o["characters"])
    progress("scenes", 10, "AI đang chọn cảnh minh họa và viết tiêu đề, hashtag...")
    data = ai.ask(SYSTEM, [{"role": "user", "content": "TRUYỆN (đoạn đã đánh số):\n\n" + _draft(st, True)
        + f"\n\nNgoại hình nhân vật:\n{looks}\nPhong cách tranh: {o['art_style']}\n\n"
        f"1) Chọn đúng {total} cảnh minh họa rải đều, theo thứ tự, mỗi chương ít nhất một cảnh và cảnh đầu mỗi chương ở para 1. "
        "para là số đoạn mà ảnh bắt đầu hiện. prompt: tiếng Anh, 25-40 từ, tả khung cảnh, nhân vật (nhắc lại ngoại hình, "
        "không dùng tên riêng), hành động, ánh sáng, góc máy; không chữ trong ảnh, không máu me; không cần nhắc phong cách tranh.\n"
        + _episode_rule(proj) +
        "2) titles: 3 tiêu đề YouTube dưới 90 ký tự, gợi tò mò, đúng nội dung, không viết hoa toàn bộ.\n"
        "3) description: 3-5 câu giới thiệu không tiết lộ kết. hashtags: 5-8 cái có dấu #. tags: 10-15 từ khóa tìm kiếm."}],
        SCENES_SCHEMA, effort="medium")
    out = []
    for x in sorted(data["scenes"], key=lambda x: (x["chapter"], x["para"])):
        if 1 <= x["chapter"] <= len(st["chapters"]):
            para = max(1, min(x["para"], len(paragraphs(st["chapters"][x["chapter"] - 1]["text"]))))
            if not out or (out[-1]["chapter"], out[-1]["para"]) != (x["chapter"], para):
                out.append({"id": len(out) + 1, "chapter": x["chapter"], "para": para, "prompt": x["prompt"],
                            "seed": 1000 + len(out)})
    if not out:
        raise RuntimeError("AI không chọn được cảnh nào, hãy chạy lại")
    out[0].update(chapter=1, para=1)
    st["scenes"] = out
    st["youtube"] = {"title": data["titles"][0], "titles": data["titles"], "description": data["description"],
                     "hashtags": [h if h.startswith("#") else "#" + h for h in data["hashtags"]], "tags": data["tags"]}
    _spend(st, ai)
    save(proj, st)
    proj.mark("scenes")
    progress("scenes", 100, f"Xong: {len(out)} cảnh")


# ---------- ảnh ----------

class LocalPainter:
    name = "SDXL trên máy"

    def __init__(self):
        py = ROOT / "engine" / ".venv_img" / "Scripts" / "python.exe"
        if not py.exists():
            raise RuntimeError("Chưa cài bộ vẽ ảnh trên máy (thiếu engine/.venv_img), hãy chạy setup.ps1")
        progress("images", 1, "Đang nạp mô hình vẽ ảnh (lần đầu tải khoảng 9GB)...")
        self.err = open(ROOT / "tools" / "sd_worker.log", "w", encoding="utf-8")
        self.p = subprocess.Popen([str(py), str(ROOT / "engine" / "sd_worker.py")], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=self.err, text=True, encoding="utf-8",
                                  creationflags=NO_WINDOW)
        r = self._read()
        if not r.get("ready"):
            raise RuntimeError(r.get("error") or "Bộ vẽ ảnh không khởi động được")

    def _read(self):
        for line in self.p.stdout:
            if line.startswith("{"):
                return json.loads(line)
        raise RuntimeError("Bộ vẽ ảnh bị dừng đột ngột (xem tools/sd_worker.log)")

    def paint(self, prompt, seed, out):
        self.p.stdin.write(json.dumps({"prompt": prompt, "width": 1344, "height": 768, "seed": seed, "out": str(out)}) + "\n")
        self.p.stdin.flush()
        r = self._read()
        if not r.get("ok"):
            raise RuntimeError(f"Vẽ ảnh lỗi: {r.get('error')}")

    def close(self):
        try:
            self.p.stdin.close()
            self.p.wait(timeout=20)
        except Exception:
            self.p.kill()
        self.err.close()


class GeminiPainter:
    name = "Gemini"
    MODELS = ["gemini-3.1-flash-lite-image", "gemini-2.5-flash-image"]

    def __init__(self, settings):
        key = settings.get("gemini_api_key", "").strip()
        if not key:
            raise RuntimeError("chưa có Gemini API key")
        from google import genai
        from google.genai import types
        self.types = types
        self.client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=90_000))

    def paint(self, prompt, seed, out):
        err = None
        for m in self.MODELS:
            try:
                r = self.client.models.generate_content(model=m, contents=prompt, config=self.types.GenerateContentConfig(
                    response_modalities=["IMAGE"], image_config=self.types.ImageConfig(aspect_ratio="16:9")))
                part = next(p for p in r.candidates[0].content.parts if p.inline_data)
                Path(out).write_bytes(part.inline_data.data)
                return
            except Exception as e:
                err = e
        raise RuntimeError("gói miễn phí không có lượt tạo ảnh (limit: 0)" if "limit: 0" in str(err) else str(err)[:200])

    def close(self):
        pass


def images(proj, settings):
    st = load(proj)
    if not st.get("scenes"):
        raise RuntimeError("Chưa có danh sách cảnh")
    _invalidate(proj, "images")
    folder = proj.path("images")
    folder.mkdir(exist_ok=True)
    style = st["outline"]["art_style"]
    src = proj.opt.get("story_image_source") or "auto"
    todo = []
    for sc in st["scenes"]:
        if sc.get("custom") and Path(sc.get("image", "")).exists():
            continue
        key = hashlib.md5(f"{sc['prompt']}|{style}|{sc['seed']}".encode()).hexdigest()[:12]
        if sc.get("image_key") == key and Path(sc.get("image", "")).exists():
            continue
        todo.append((sc, key))
    painter = None
    try:
        for k, (sc, key) in enumerate(todo):
            progress("images", 3 + k / len(todo) * 96, f"Đang vẽ ảnh {k + 1}/{len(todo)}"
                     + (f" ({painter.name})" if painter else ""))
            # tên file theo nội dung: ảnh mới không bị bộ nhớ đệm của app hiện nhầm ảnh cũ
            out = folder / f"{sc['id']:03d}_{key}.png"
            prompt = f"{style}, {sc['prompt']}"
            if painter is None:
                if src == "local":
                    painter = LocalPainter()
                else:
                    try:
                        painter = GeminiPainter(settings)
                        painter.paint(prompt, sc["seed"], out)
                    except Exception as e:
                        if src == "gemini":
                            raise RuntimeError(f"Gemini không vẽ được ảnh: {e}")
                        log(f"Gemini không vẽ được ảnh ({e}) → vẽ bằng card đồ họa trên máy, miễn phí")
                        painter = LocalPainter()
            if not out.exists():
                try:
                    painter.paint(prompt, sc["seed"], out)
                except Exception as e:
                    if isinstance(painter, LocalPainter) or src == "gemini":
                        raise
                    log(f"Gemini hết lượt giữa chừng ({e}) → vẽ tiếp trên máy")
                    painter = LocalPainter()
                    painter.paint(prompt, sc["seed"], out)
            sc.update(image=str(out), image_key=key)
            save(proj, st)
    finally:
        if painter:
            painter.close()
    _thumb(proj, st["scenes"][0]["image"])
    proj.mark("images")
    progress("images", 100, f"Xong {len(st['scenes'])} ảnh")


def _thumb(proj, image):
    from PIL import Image
    im = Image.open(image).convert("RGB")
    im.thumbnail((640, 640))
    im.save(proj.path("thumb.jpg"), quality=88)


# ---------- giọng đọc ----------

def _sentences(text, limit=220):
    """Tách câu để đọc; câu quá dài thì ngắt thêm ở dấu phẩy."""
    out = []
    raw = []
    for s in re.split(r"(?<=[.!?…])\s+|(?<=[.!?…][\"”’])\s+", text):
        if raw and s[:1].islower():  # lời dẫn sau câu thoại: "Ai đó?" ông hỏi → đọc liền
            raw[-1] += " " + s
        else:
            raw.append(s)
    for s in raw:
        s = s.strip()
        while len(s) > limit:
            cut = max(s.rfind(c, 0, limit) for c in (", ", "; ", ": "))
            if cut < limit * 0.3:
                cut = s.rfind(" ", 0, limit)
            if cut <= 0:
                break
            out.append(s[:cut + 1].strip())
            s = s[cut + 1:].strip()
        if re.search(r"\w", s):
            out.append(s)
    return out


def voice(proj, settings):
    import soundfile as sf

    from . import tts
    st = load(proj)
    if not st.get("chapters"):
        raise RuntimeError("Chưa có nội dung truyện")
    _invalidate(proj, "voice")
    folder = proj.path("tts")
    folder.mkdir(exist_ok=True)
    spec, speed = proj.opt["voice"], float(proj.opt.get("story_speed") or 1.0)
    units = []  # (chương, đoạn, câu, nghỉ sau câu)
    for ci, c in enumerate(st["chapters"], 1):
        ps = paragraphs(c["text"])
        for pi, p in enumerate(ps, 1):
            ss = _sentences(p)
            for si, s in enumerate(ss):
                end_p = si == len(ss) - 1
                units.append((ci, pi, s, PAUSE["chapter"] if end_p and pi == len(ps) else PAUSE["para"] if end_p
                              else PAUSE["sentence"]))
    if not units:
        raise RuntimeError("Truyện không có câu nào để đọc")

    def clip(text):
        return folder / (hashlib.md5(f"{text}|{spec}|{speed:.2f}".encode()).hexdigest()[:16] + ".mp3")

    async def run_all():
        voices = tts.Voices()
        sem = asyncio.Semaphore(voices.concurrency)
        done = 0

        async def one(text):
            nonlocal done
            async with sem:
                if not clip(text).exists():
                    # đọc ra file tạm rồi đổi tên, để lần bị dừng giữa chừng không để lại file hỏng
                    tmp = clip(text).with_suffix(".part.mp3")
                    await voices.synth(spec, text, speed, tmp)
                    tmp.replace(clip(text))
            done += 1
            progress("voice", done / len(texts) * 88, f"Đã đọc {done}/{len(texts)} câu")

        texts = list(dict.fromkeys(u[2] for u in units))
        try:
            await asyncio.gather(*(one(t) for t in texts))
        finally:
            voices.close()

    progress("voice", 0, "Đang đọc truyện...")
    asyncio.run(run_all())

    progress("voice", 90, "Đang ghép giọng đọc...")
    parts, segs, starts, t = [np.zeros(int(0.8 * SR), np.float32)], [], {}, 0.8
    for ci, pi, text, gap in units:
        a = tts._decode(clip(text))
        starts.setdefault((ci, pi), t)
        d = len(a) / SR
        segs.append({"id": len(segs) + 1, "start": round(t, 3), "end": round(t + d, 3), "v_start": round(t, 3),
                     "v_end": round(t + d, 3), "text": "", "vi": text, "chapter": ci})
        parts += [a, np.zeros(int(gap * SR), np.float32)]
        t += d + int(gap * SR) / SR
    parts.append(np.zeros(int(1.5 * SR), np.float32))
    full = np.concatenate(parts)
    sf.write(str(proj.path("dub_voice.wav")), np.clip(full, -1, 1), SR, subtype="PCM_16")
    proj.save_segments(segs)
    st["starts"] = {f"{c}:{p}": round(v, 3) for (c, p), v in starts.items()}
    save(proj, st)
    proj.meta["duration"] = round(len(full) / SR, 3)
    proj.save()
    proj.mark("voice")
    progress("voice", 100, f"Xong: {len(full) / SR / 60:.1f} phút")


# ---------- ghép video ----------

def _prepare(path):
    """Ảnh phủ kín khung 16:9, lớn hơn khung 20% để có chỗ phóng/lia."""
    import cv2
    from PIL import Image
    im = np.asarray(Image.open(path).convert("RGB"))
    tw, th = int(W * 1.2), int(H * 1.2)
    k = max(tw / im.shape[1], th / im.shape[0])
    im = cv2.resize(im, (math.ceil(im.shape[1] * k), math.ceil(im.shape[0] * k)), interpolation=cv2.INTER_LANCZOS4)
    y, x = (im.shape[0] - th) // 2, (im.shape[1] - tw) // 2
    return np.ascontiguousarray(im[y:y + th, x:x + tw])


def _frame(im, u, kind):
    """Khung hình tại vị trí u (0..1) của cảnh: phóng vào / lùi ra / lia sang hai bên."""
    import cv2
    ih, iw = im.shape[:2]
    e = u * u * (3 - 2 * u)
    if kind < 2:
        z = 1.2 - 0.18 * e if kind == 0 else 1.02 + 0.18 * e   # tỉ lệ vùng nhìn so với khung
        cx, cy = iw / 2, ih / 2
    else:
        z = 1.05
        room = iw - W * z
        cx = W * z / 2 + room * (e if kind == 2 else 1 - e)
        cy = ih / 2
    m = np.float32([[z, 0, cx - W * z / 2], [0, z, cy - H * z / 2]])
    return cv2.warpAffine(im, m, (W, H), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_REPLICATE)


def compose(proj, settings):
    import cv2

    from .render import write_subs
    st, o = load(proj), proj.opt
    if not proj.path("dub_voice.wav").exists():
        raise RuntimeError("Chưa có giọng đọc")
    scs = [s for s in st.get("scenes", []) if Path(s.get("image", "")).exists()]
    if not scs:
        raise RuntimeError("Chưa có ảnh minh họa")
    _invalidate(proj, "compose")
    dur = float(proj.meta["duration"])
    starts = st["starts"]
    for s in scs:
        # người dùng sửa truyện làm số đoạn ít đi thì lấy đầu chương
        s["start"] = starts.get(f"{s['chapter']}:{s['para']}", starts.get(f"{s['chapter']}:1", 0))
    scs.sort(key=lambda s: s["start"])
    scs[0]["start"] = 0.0
    scs = [s for i, s in enumerate(scs) if i == len(scs) - 1 or scs[i + 1]["start"] - s["start"] > 2 * FADE]
    scs[0]["start"] = 0.0
    write_subs(proj, proj.segments())

    inputs = ["-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "pipe:0", "-i", "dub_voice.wav"]
    fmt = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
    music = (o.get("music") or "").strip()
    if music and Path(music).exists():
        inputs += ["-stream_loop", "-1", "-i", music]
        graph = (f"[1:a]{fmt},volume={o['voice_volume']},asplit=2[v][sc];[2:a]{fmt},volume={o['music_volume']}[m];"
                 "[m][sc]sidechaincompress=threshold=0.03:ratio=4:attack=30:release=600[md];"
                 f"[v][md]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95,afade=t=out:st={max(0, dur - 2):.2f}:d=2[aout]")
    else:
        graph = f"[1:a]{fmt},volume={o['voice_volume']},alimiter=limit=0.95[aout]"
    vf = "format=yuv420p" + (",subtitles=subs.ass" if o.get("burn_subs") else "")
    out = proj.path("output.mp4")
    tmp = proj.path("output.part.mp4")
    cmd = [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y", *inputs, "-filter_complex", graph, "-map", "0:v",
           "-map", "[aout]", "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "192k", "-t", f"{dur:.3f}", "-movflags", "+faststart", tmp.name]
    errlog = open(proj.path("compose.log"), "w+b")
    p = subprocess.Popen(cmd, cwd=proj.dir, stdin=subprocess.PIPE, stderr=errlog, creationflags=NO_WINDOW)
    total = int(dur * FPS)
    cur, idx, img, nxt = -1, 0, None, None
    last = time.time()
    try:
        for f in range(total):
            t = f / FPS
            while idx + 1 < len(scs) and scs[idx + 1]["start"] <= t:
                idx += 1
            if idx != cur:
                img = nxt if nxt is not None and idx == cur + 1 else _prepare(scs[idx]["image"])
                nxt, cur = None, idx
            a = scs[idx]["start"]
            b = scs[idx + 1]["start"] if idx + 1 < len(scs) else dur
            # cảnh kéo dài thêm FADE giây ở đầu (lúc đang mờ dần vào trong cảnh trước)
            fr = _frame(img, min(1, (t - a + FADE) / (b - a + FADE)), idx % 4)
            if idx + 1 < len(scs) and b - t < FADE:
                if nxt is None:
                    nxt = _prepare(scs[idx + 1]["image"])
                b2 = scs[idx + 2]["start"] if idx + 2 < len(scs) else dur
                fr2 = _frame(nxt, max(0, (t - b + FADE) / (b2 - b + FADE)), (idx + 1) % 4)
                w = 1 - (b - t) / FADE
                fr = cv2.addWeighted(fr2, w, fr, 1 - w, 0)
            p.stdin.write(fr.tobytes())
            if time.time() - last > 1:
                last = time.time()
                progress("compose", f / total * 99, f"Đang ghép video {t / 60:.1f}/{dur / 60:.1f} phút")
        p.stdin.close()
    except (BrokenPipeError, OSError):
        pass  # ffmpeg dừng giữa chừng: báo lỗi theo mã thoát bên dưới
    except BaseException:
        p.kill()
        raise
    code = p.wait()
    errlog.seek(0)
    err = errlog.read().decode("utf-8", "replace").strip()
    errlog.close()
    if code:
        raise RuntimeError("ffmpeg lỗi: " + err[-1000:])
    tmp.replace(out)
    proj.path("compose.log").unlink(missing_ok=True)
    save(proj, st)
    proj.mark("compose")
    progress("compose", 100, f"Xong: {out}")


# ---------- nội dung đăng ----------

def _series_url(ep):
    try:
        return json.loads((Path(ep["series"]) / "series.json").read_text(encoding="utf-8")).get("web", {}).get("url", "")
    except Exception:
        return ""


def caption(proj):
    st = load(proj)
    y = st.get("youtube")
    if not y:
        raise RuntimeError("Chưa có tiêu đề/mô tả, hãy chạy bước chia cảnh trước")
    desc = y["description"].strip()
    # mốc thời gian chương (YouTube cần mốc đầu 0:00, ít nhất 3 mốc, mỗi chương ≥10 giây)
    starts = st.get("starts", {})
    marks = []
    for i, c in enumerate(st.get("chapters", []), 1):
        t = 0 if i == 1 else int(starts.get(f"{i}:1", -1))
        if t >= 0:
            marks.append(f"{t // 60}:{t % 60:02d} {c['title']}")
    if len(marks) >= 3 and starts:
        desc += "\n\n" + "\n".join(marks)
    from . import music
    title = music.title_of(proj.opt.get("music"))
    if title:
        desc += "\n\n" + music.credit(title)
    ep = proj.opt.get("story_episode")
    if ep:
        desc += f"\n\n{ep['title']} - Tập {ep['n']}: chương {ep['from']} đến {ep['to']}."
        url = ep.get("web_url") or _series_url(ep)
        if url:
            desc += f"\nĐọc truyện chữ đầy đủ tại: {url}"
    desc += "\n\nTruyện hư cấu; kịch bản và tranh minh họa có sử dụng AI hỗ trợ."
    desc += "\n\n" + " ".join(y["hashtags"])
    return {"title": y["title"][:100], "description": desc, "tags": ",".join(y["tags"])}
