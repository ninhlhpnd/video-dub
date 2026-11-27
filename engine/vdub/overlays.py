"""Chữ Trung in trên video (tiêu đề, nhãn, phụ đề cứng...): dò bằng OCR chạy trên máy (RapidOCR), dịch bằng Gemini,
rồi lúc xuất video thì xóa chữ cũ và đặt chữ Việt / tên kênh vào đúng chỗ, đúng thời gian.

Kết quả lưu ở overlays.json: [{id, start, end, x, y, w, h (tỉ lệ 0..1), zh, vi, kind: "sub"|"text", action}]
action: "translate" (thay bằng chữ Việt) | "remove" (xóa) | "channel" (thay bằng tên kênh) | "keep" (giữ nguyên)
"""
import difflib
import json
import re
import subprocess
from collections import Counter

import numpy as np

from .common import FFMPEG, NO_WINDOW, Project, log, progress

STEP = "overlays"
LONG_SIDE = 960       # thu nhỏ khung hình trước khi đọc chữ (đủ nét cho chữ trên video điện thoại)
MIN_SCORE = 0.75      # độ tin cậy tối thiểu của OCR
MAX_FRAMES = 600      # video dài thì giãn khoảng lấy mẫu để không quét quá lâu
CJK = re.compile(r"[㐀-䶿一-鿿]")

TRANSLATE = """Dịch các dòng chữ tiếng Trung in trên video (tiêu đề, nhãn, chú thích) sang tiếng Việt.
- Ngắn gọn như chữ trên video, độ dài gần bằng bản gốc (tối đa khoảng {ratio}× số chữ Hán), không thêm giải thích.
- Giữ nguyên id. Tên thương hiệu/tên riêng thì phiên âm hoặc giữ nguyên.
{note}
Các dòng (JSON): {items}
Trả về JSON: [{{"id": 1, "vi": "..."}}]"""


def path(proj):
    return proj.path("overlays.json")


def load(proj):
    f = path(proj)
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []


def save(proj, items):
    path(proj).write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")


def _frames(video, step, w, h):
    """Đọc lần lượt các khung hình cách nhau [step] giây (một lệnh ffmpeg duy nhất)."""
    cmd = [str(FFMPEG), "-v", "error", "-i", str(video), "-vf", f"fps=1/{step},scale={w}:{h}",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
    size = w * h * 3
    k = 0
    try:
        while True:
            buf = p.stdout.read(size)
            if len(buf) < size:
                break
            yield float(k * step), np.frombuffer(buf, np.uint8).reshape(h, w, 3)
            k += 1
    finally:
        p.stdout.close()
        p.wait()


def _iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _same_text(a, b):
    return a == b or difflib.SequenceMatcher(None, a, b).ratio() >= 0.6


def detect(proj: Project):
    from rapidocr import RapidOCR

    vw, vh = proj.meta["width"], proj.meta["height"]
    scale = LONG_SIDE / max(vw, vh)
    w, h = int(vw * scale) // 2 * 2, int(vh * scale) // 2 * 2
    dur = float(proj.duration)
    step = max(0.5, round(dur / MAX_FRAMES, 2))
    ocr = RapidOCR()

    tracks = []  # {box, texts, first, last, hits}
    progress(STEP, 1, "Đang dò chữ trên video...")
    for t, img in _frames(proj.video, step, w, h):
        r = ocr(img)
        dets = []
        if r.boxes is not None:
            for box, txt, score in zip(r.boxes, r.txts, r.scores):
                txt = str(txt).strip()
                if score < MIN_SCORE or len(CJK.findall(txt)) < 2:
                    continue
                xs, ys = [float(p[0]) for p in box], [float(p[1]) for p in box]
                dets.append(([min(xs) / w, min(ys) / h, max(xs) / w, max(ys) / h], txt))
        for box, txt in dets:
            best = None
            for tr in tracks:
                if tr["last"] >= t - step * 2.5 and _iou(tr["box"], box) > 0.3 and _same_text(tr["texts"].most_common(1)[0][0], txt):
                    best = tr
                    break
            if best:
                b = best["box"]
                best["box"] = [min(b[0], box[0]), min(b[1], box[1]), max(b[2], box[2]), max(b[3], box[3])]
                best["texts"][txt] += 1
                best["last"] = t
                best["hits"] += 1
                best["sizes"].append(box)
            else:
                tracks.append({"box": box, "texts": Counter({txt: 1}), "first": t, "last": t, "hits": 1,
                               "sizes": [box]})
        progress(STEP, min(90, 1 + t / max(dur, 1) * 89), f"Đang dò chữ trên video... {t:.0f}/{dur:.0f}s, "
                                                         f"thấy {len(tracks)} dòng")

    # bỏ chữ chỉ thoáng qua 1 khung (thường là chữ trên đồ vật bị đọc nhầm)
    tracks = [tr for tr in tracks if tr["hits"] >= 2 or dur <= step * 3]
    tracks = _clean(tracks, step, vw / vh)
    items = []
    for i, tr in enumerate(sorted(tracks, key=lambda tr: (tr["first"], tr["box"][1])), 1):
        x0, y0, x1, y1 = tr["box"]
        # khung gộp (x,y,w,h) dùng để xóa chữ cũ; cỡ & tâm chữ "thật" lấy trung vị qua các khung hình để đặt chữ Việt
        s = np.array(tr["sizes"])
        med = {"th": float(np.median(s[:, 3] - s[:, 1])), "tw": float(np.median(s[:, 2] - s[:, 0])),
               "cx": float(np.median((s[:, 0] + s[:, 2]) / 2)), "cy": float(np.median((s[:, 1] + s[:, 3]) / 2))}
        items.append({
            **{k: round(v, 4) for k, v in med.items()},
            "id": i,
            # chữ có thể đã hiện/còn hiện tới gần một khoảng lấy mẫu trước/sau lần đọc được → xóa dư mỗi đầu
            "start": round(max(0.0, tr["first"] - step), 2),
            "end": round(min(dur, tr["last"] + step), 2),
            "x": round(x0, 4), "y": round(y0, 4), "w": round(x1 - x0, 4), "h": round(y1 - y0, 4),
            "zh": tr["texts"].most_common(1)[0][0],
            "vi": "",
        })
    _classify(items)
    return items


def _text(tr):
    return tr["texts"].most_common(1)[0][0]


def _contain(small, big):
    """Tỉ lệ diện tích khung [small] nằm trong khung [big]."""
    ix = max(0.0, min(small[2], big[2]) - max(small[0], big[0]))
    iy = max(0.0, min(small[3], big[3]) - max(small[1], big[1]))
    area = (small[2] - small[0]) * (small[3] - small[1])
    return ix * iy / area if area > 0 else 0.0


def _clean(tracks, step, aspect):
    """OCR đọc lúc đủ lúc thiếu cùng một dòng chữ → nhiều mảnh chồng nhau, chữ Việt chớp tắt liên tục. Gộp lại."""
    # chữ dọc / khung cao bất thường (đọc nhầm): bỏ qua, không đụng vào
    tracks = [tr for tr in tracks if tr["box"][3] - tr["box"][1] <= 0.2]

    # 1) cùng dòng chữ, cùng chỗ, chỉ đứt quãng ngắn (khung hình đọc trượt) → nối liền
    tracks.sort(key=lambda tr: tr["first"])
    merged = []
    for tr in tracks:
        prev = next((m for m in reversed(merged)
                     if tr["first"] - m["last"] <= max(3.0, step * 4) and _iou(m["box"], tr["box"]) > 0.4
                     and _same_text(_text(m), _text(tr))), None)
        if prev:
            b, c = prev["box"], tr["box"]
            prev["box"] = [min(b[0], c[0]), min(b[1], c[1]), max(b[2], c[2]), max(b[3], c[3])]
            prev["texts"].update(tr["texts"])
            prev["sizes"] += tr["sizes"]
            prev["last"] = max(prev["last"], tr["last"])
            prev["hits"] += tr["hits"]
        else:
            merged.append(tr)

    # 2) mảnh đọc thiếu (chữ là một phần của dòng dài hơn, nằm gọn trong khung của dòng đó, cùng lúc) → bỏ
    def rank(t):  # dòng đầy đủ hơn (chữ dài hơn, đọc được nhiều lần hơn) được giữ
        return len(_text(t)), t["hits"], -t["first"]

    def covered(a):
        for b in merged:
            if b is a or rank(b) <= rank(a):
                continue
            overlap = min(a["last"], b["last"]) - max(a["first"], b["first"]) + step
            dur = a["last"] - a["first"] + step
            if overlap / dur > 0.6 and _contain(a["box"], b["box"]) > 0.6 and \
                    (_text(a) in _text(b) or _same_text(_text(a), _text(b))):
                return True
        return False

    merged = [tr for tr in merged if not covered(tr)]

    # 3) một dòng bị đọc thành nhiều mảnh nằm cạnh nhau trên cùng hàng, cùng lúc → ghép thành một dòng
    def th(t):
        return t["box"][3] - t["box"][1]

    def same_line(a, b):
        h = max(th(a), th(b))
        cy_a, cy_b = (a["box"][1] + a["box"][3]) / 2, (b["box"][1] + b["box"][3]) / 2
        if abs(cy_a - cy_b) > h * 0.5:
            return False
        left, right = (a, b) if a["box"][0] <= b["box"][0] else (b, a)
        gap = (right["box"][0] - left["box"][2]) * aspect  # quy về cùng đơn vị với chiều cao
        overlap = min(a["last"], b["last"]) - max(a["first"], b["first"]) + step
        shorter = min(a["last"] - a["first"], b["last"] - b["first"]) + step
        return gap < h * 1.5 and overlap / shorter > 0.5

    changed = True
    while changed:
        changed = False
        for a in merged:
            b = next((b for b in merged if b is not a and same_line(a, b)), None)
            if b:
                left, right = (a, b) if a["box"][0] <= b["box"][0] else (b, a)
                text = _text(left) + _text(right)
                a["box"] = [min(a["box"][0], b["box"][0]), min(a["box"][1], b["box"][1]),
                            max(a["box"][2], b["box"][2]), max(a["box"][3], b["box"][3])]
                a["texts"] = Counter({text: a["hits"] + b["hits"]})
                a["first"], a["last"] = min(a["first"], b["first"]), max(a["last"], b["last"])
                a["hits"] += b["hits"]
                # cỡ chữ: giữ các khung của mảnh lớn, tâm ngang lấy theo cả dòng
                a["sizes"] = [[a["box"][0], s[1], a["box"][2], s[3]] for s in a["sizes"] + b["sizes"]]
                merged.remove(b)
                changed = True
                break
    return merged


def _classify(items):
    """Phụ đề cứng: các dòng nằm nửa dưới, canh giữa, cùng độ cao, nội dung thay đổi liên tục → mặc định xóa
    (đã có phụ đề Việt); các chữ khác (tiêu đề, nhãn) → mặc định dịch."""
    for it in items:
        cx, cy = it["x"] + it["w"] / 2, it["y"] + it["h"] / 2
        peers = [o for o in items if o is not it and abs((o["y"] + o["h"] / 2) - cy) < 0.04
                 and abs((o["x"] + o["w"] / 2) - 0.5) < 0.12]
        is_sub = cy > 0.55 and abs(cx - 0.5) < 0.12 and len(peers) >= 1
        it["kind"] = "sub" if is_sub else "text"
        it["action"] = "remove" if is_sub else "translate"


def translate_items(proj, items, settings):
    todo = [it for it in items if it["action"] == "translate" and not it.get("vi")]
    if not todo:
        return
    if not settings.get("gemini_api_key"):
        log("Chưa có Gemini API key: chữ Trung sẽ được xóa thay vì dịch")
        return
    from .ai import Gemini
    note = proj.opt.get("translate_note", "").strip()
    try:
        data = Gemini(settings, temperature=0.3).ask(TRANSLATE.format(
            ratio=1.8, note=f"- Ghi chú: {note}" if note else "",
            items=json.dumps([{"id": it["id"], "zh": it["zh"]} for it in todo], ensure_ascii=False)))
    except Exception as e:
        log(f"Không dịch được chữ trên video ({str(e)[:150]}), các chữ đó sẽ được xóa")
        return
    got = {int(d["id"]): str(d.get("vi", "")).strip() for d in data if isinstance(d, dict) and "id" in d}
    for it in todo:
        it["vi"] = got.get(it["id"], "")


def run(proj: Project, settings):
    old = {(o["zh"], round(o["start"])): o for o in load(proj)}
    items = detect(proj)
    # giữ lại lựa chọn / bản dịch người dùng đã sửa ở lần dò trước
    for it in items:
        prev = old.get((it["zh"], round(it["start"])))
        if prev:
            it["action"], it["vi"] = prev.get("action", it["action"]), prev.get("vi", "")
    progress(STEP, 92, f"Đang dịch {sum(it['action'] == 'translate' for it in items)} dòng chữ...")
    translate_items(proj, items, settings)
    save(proj, items)
    subs = sum(it["kind"] == "sub" for it in items)
    log(f"Tìm thấy {len(items)} dòng chữ Trung trên video ({subs} dòng phụ đề sẽ xóa, "
        f"{len(items) - subs} tiêu đề/nhãn sẽ dịch)")
    proj.mark(STEP)
    progress(STEP, 100, f"Xong: {len(items)} dòng chữ")
