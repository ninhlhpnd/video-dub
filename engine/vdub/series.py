"""Truyện dài nhiều chương (đến cả nghìn chương, mỗi chương ~1 trang A4) và cắt thành từng tập video 15-20 phút.

Giữ mạch truyện xuyên suốt bằng "trí nhớ" nhiều tầng thay vì nhồi cả truyện vào mỗi lần gọi AI:
  1. Sổ tay bộ truyện (bible): thế giới, quy luật, nhân vật, giọng văn, các quyển lớn (arc) và kết cục.  Lập 1 lần.
  2. Dàn ý chi tiết từng chương của quyển đang viết: lập khi bắt đầu quyển, dựa trên những gì đã thật sự viết.
  3. Tóm tắt mỗi quyển đã xong + tóm tắt 12 chương gần nhất + nguyên văn 2 chương liền trước.
  4. Trạng thái hiện tại của nhân vật và các "nút thắt" còn mở (đã gài, chưa mở) cập nhật sau mỗi chương.
Mỗi chương lưu kèm bản chụp trạng thái (4) nên có thể quay lại viết lại từ một chương bất kỳ.

Thư mục bộ truyện: <workspace>/_series/<id>/
  series.json            thông tin, bible, arcs[{title, summary, goal, climax, start, end, beats[], recap}],
                         memory{states, threads}, written, episodes[], web{}, cost, options
  chapters/0001.json     {n, title, text, summary, arc, memory{states, threads}, edited?}
"""
import hashlib
import json
import re
import time
from pathlib import Path

from .common import log, progress
from .writer import make_writer

CPS = 14.5             # ký tự/giây khi đọc thành tiếng (giống story.py)
RECENT_SUMMARIES = 12  # số chương gần nhất đưa tóm tắt vào ngữ cảnh
RECENT_FULL = 2        # số chương liền trước đưa nguyên văn
ARC_LEN = 40           # số chương trung bình mỗi quyển


# ---------- dữ liệu ----------

class Series:
    def __init__(self, folder):
        self.dir = Path(folder)
        self.file = self.dir / "series.json"
        if not self.file.exists():
            raise RuntimeError(f"Không tìm thấy bộ truyện: {folder}")
        self.data = json.loads(self.file.read_text(encoding="utf-8"))
        (self.dir / "chapters").mkdir(exist_ok=True)

    @staticmethod
    def create(workspace, options):
        stamp = time.strftime("%Y%m%d_%H%M%S")
        folder = Path(workspace) / "_series" / f"bo_{stamp}"
        (folder / "chapters").mkdir(parents=True, exist_ok=True)
        opt = {"genre": "", "idea": "", "tone": "", "note": "", "chapters": 1000, "chapter_chars": 3000,
               "episode_minutes": 17, "voice": "vieneu:Thiện Minh", "auto_publish": False, **options}
        opt["chapters"] = max(3, min(3000, int(opt["chapters"])))
        opt["chapter_chars"] = max(1200, min(8000, int(opt["chapter_chars"])))
        data = {"title": (opt.get("idea") or "Bộ truyện mới")[:60], "created": stamp, "options": opt,
                "arcs": [], "memory": {"states": {}, "threads": []}, "written": 0, "episodes": [], "web": {},
                "cost": 0.0}
        (folder / "series.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        return Series(folder)

    @property
    def opt(self):
        return self.data["options"]

    @property
    def total(self):
        return int(self.opt["chapters"])

    def save(self):
        # đọc lại phần app có thể vừa sửa (tùy chọn, tên truyện) để không ghi đè
        try:
            fresh = json.loads(self.file.read_text(encoding="utf-8"))
            self.data["options"] = {**self.data["options"], **fresh.get("options", {})}
        except Exception:
            pass
        tmp = self.dir / "series.tmp"
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.file)

    def ch_file(self, n):
        return self.dir / "chapters" / f"{n:04d}.json"

    def chapter(self, n):
        f = self.ch_file(n)
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None

    def save_chapter(self, ch):
        f = self.ch_file(ch["n"])
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(ch, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(f)

    def count_written(self):
        """Số chương liên tục từ chương 1 đã có trên đĩa."""
        n = 0
        while self.ch_file(n + 1).exists():
            n += 1
        return n

    def arc_of(self, n):
        for i, a in enumerate(self.data["arcs"]):
            if a["start"] <= n <= a["end"]:
                return i
        return len(self.data["arcs"]) - 1

    def spend(self, ai):
        self.data["cost"] = round(self.data.get("cost", 0) + ai.cost, 4)
        ai.cost = 0.0


def paragraphs(text):
    return [p.strip() for p in re.split(r"\n+", text or "") if p.strip()]


# ---------- prompt ----------

SYSTEM = """Bạn là nhà văn Việt Nam chuyên viết truyện dài kỳ (web novel) hàng trăm, hàng nghìn chương, được độc giả theo dõi mỗi ngày.
Tay nghề bắt buộc:
- Truyện hoàn toàn nguyên bản: không phóng tác, không mượn nhân vật, thế giới hay cốt truyện của tác phẩm có sẵn, không dùng người thật.
- Mạch truyện liền một khối: tôn trọng tuyệt đối sổ tay bộ truyện và những gì đã viết (tên, tuổi, quan hệ, sức mạnh, vết thương, đồ vật, địa danh, thời gian). Không để nhân vật biết điều họ chưa được biết. Nhân vật đã chết không tự xuất hiện lại nếu không có lý do đã gài.
- Mỗi chương là một bước tiến thật của câu chuyện: có mục tiêu, trở ngại, thay đổi. Không lặp lại cảnh, tình tiết hay câu chữ của chương trước; không tóm tắt lại chương trước ở đầu chương.
- DỰNG CẢNH, không kể lướt: hành động cụ thể, giác quan, đối thoại qua lại nhiều lượt; cho thấy cảm xúc qua cử chỉ và lời nói, không gọi tên cảm xúc. Tránh sáo ngữ, tránh giảng đạo lý, tránh câu tổng kết cuối chương.
- Nhịp dài hơi: gài chi tiết sớm để mở về sau, xen kẽ cao trào và chương lắng, nhân vật phụ có đời sống riêng. Cuối mỗi chương có một móc câu tự nhiên khiến người đọc muốn sang chương sau.
- Văn để đọc và để nghe: câu vừa phải, đổi nhịp; không gạch đầu dòng, không ký hiệu, không viết tắt, số viết bằng chữ; lời thoại đặt trong ngoặc kép và rõ ai nói. Chỉ dùng tiếng Việt (thuật ngữ Hán Việt được phép nếu hợp thể loại).
- Phù hợp đăng YouTube và trang đọc truyện đại chúng: không tả bạo lực máu me chi tiết, tình dục, tự hại, thù ghét, mê tín trục lợi, thông tin sai sự thật nguy hiểm."""


def _obj(props):
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def _arr(item):
    return {"type": "array", "items": item}


S, I = {"type": "string"}, {"type": "integer"}
BIBLE_SCHEMA = _obj({
    "title": S, "logline": S, "theme": S, "setting": S, "rules": S, "style": S, "art_style": S, "ending": S,
    "characters": _arr(_obj({"name": S, "role": S, "personality": S, "goal": S, "secret": S, "arc": S,
                             "look_en": S})),
    "places": _arr(_obj({"name": S, "desc": S})),
    "arcs": _arr(_obj({"title": S, "summary": S, "goal": S, "climax": S, "chapters": I})),
})
BEATS_SCHEMA = _obj({"chapters": _arr(_obj({"title": S, "beats": S}))})
CHAPTER_SCHEMA = _obj({
    "text": S, "summary": S,
    "states": _arr(_obj({"name": S, "state": S})),
    "new_characters": _arr(_obj({"name": S, "role": S, "look_en": S})),
    "threads_opened": _arr(S), "threads_closed": _arr(I),
})
SUMMARY_SCHEMA = _obj({"summary": S})


def _bible_text(s):
    """Phần ổn định nhất của ngữ cảnh (đặt đầu để Claude lưu đệm prompt, gọi sau rẻ hơn)."""
    b = s.data["bible"]
    chars = "\n".join(f"- {c['name']} ({c['role']}): tính cách {c['personality']}; mong muốn: {c['goal']}; "
                      f"bí mật: {c['secret']}; hành trình: {c['arc']}" for c in b["characters"])
    places = "\n".join(f"- {p['name']}: {p['desc']}" for p in b.get("places", []))
    arcs = "\n".join(f"Quyển {i + 1} (chương {a['start']}-{a['end']}) \"{a['title']}\": {a['summary']} "
                     f"Mục tiêu: {a['goal']} Cao trào: {a['climax']}" for i, a in enumerate(s.data["arcs"]))
    return (f"SỔ TAY BỘ TRUYỆN \"{b['title']}\" (tổng {s.total} chương, mỗi chương khoảng {s.opt['chapter_chars']} ký tự)\n"
            f"Thể loại: {s.opt.get('genre') or 'theo sổ tay'}\nTóm tắt: {b['logline']}\nChủ đề: {b['theme']}\n"
            f"Thế giới, bối cảnh: {b['setting']}\nQuy luật thế giới: {b['rules']}\nGiọng văn, ngôi kể, xưng hô: {b['style']}\n"
            f"Kết cục định hướng: {b['ending']}\n\nNHÂN VẬT CHÍNH:\n{chars}\n\nĐỊA DANH:\n{places}\n\n"
            f"CÁC QUYỂN (dàn ý lớn, có thể điều chỉnh nhẹ theo diễn biến thực tế):\n{arcs}")


def _arc_text(s, k):
    """Tóm tắt các quyển đã xong + dàn ý chi tiết quyển k (ổn định trong suốt quyển)."""
    done = "\n".join(f"Quyển {i + 1} \"{a['title']}\": {a['recap']}"
                     for i, a in enumerate(s.data["arcs"][:k]) if a.get("recap"))
    a = s.data["arcs"][k]
    beats = "\n".join(f"Chương {a['start'] + i}: {b['title']} — {b['beats']}" for i, b in enumerate(a.get("beats", [])))
    return ((f"NHỮNG GÌ ĐÃ XẢY RA Ở CÁC QUYỂN TRƯỚC:\n{done}\n\n" if done else "")
            + f"DÀN Ý CHI TIẾT QUYỂN {k + 1} \"{a['title']}\" (chương {a['start']}-{a['end']}):\n{beats}")


def _memory_text(s, n):
    """Phần thay đổi mỗi chương: trạng thái, nút thắt, tóm tắt gần đây, nguyên văn chương trước."""
    m = s.data["memory"]
    states = "\n".join(f"- {k}: {v}" for k, v in m["states"].items()) or "(chưa có)"
    threads = "\n".join(f"[{i}] {t}" for i, t in enumerate(m["threads"])) or "(chưa có)"
    extra = "\n".join(f"- {c['name']} ({c['role']})" for c in s.data["bible"].get("extra_characters", []))
    out = [f"NHÂN VẬT PHỤ ĐÃ XUẤT HIỆN:\n{extra}" if extra else "",
           f"TRẠNG THÁI HIỆN TẠI CỦA NHÂN VẬT (sau chương {n - 1}):\n{states}",
           f"CÁC NÚT THẮT ĐANG MỞ (đã gài, chưa giải quyết):\n{threads}"]
    lo = max(1, n - RECENT_SUMMARIES)
    sums = []
    for i in range(lo, n - RECENT_FULL):
        c = s.chapter(i)
        if c:
            sums.append(f"Chương {i} \"{c['title']}\": {c['summary']}")
    if sums:
        out.append("TÓM TẮT CÁC CHƯƠNG GẦN ĐÂY:\n" + "\n".join(sums))
    for i in range(max(1, n - RECENT_FULL), n):
        c = s.chapter(i)
        if c:
            out.append(f"NGUYÊN VĂN CHƯƠNG {i} \"{c['title']}\":\n{c['text']}")
    return "\n\n".join(x for x in out if x)


# ---------- lập sổ tay + các quyển ----------

def plan(folder, settings):
    s = Series(folder)
    o = s.opt
    ai = make_writer(settings)
    n_arcs = max(1, min(60, round(s.total / ARC_LEN)))
    prompt = (f"Hãy thiết kế một bộ truyện dài kỳ {s.total} chương, mỗi chương khoảng {o['chapter_chars']} ký tự "
              f"(một trang A4), chia thành đúng {n_arcs} quyển.\n"
              f"Thể loại: {o.get('genre') or 'tự chọn thể loại được yêu thích'}\n"
              f"Ý tưởng của tác giả: {o.get('idea') or 'tự nghĩ một ý tưởng thật mới, có sức kéo dài'}\n"
              f"Giọng văn: {o.get('tone') or 'hợp với thể loại'}\nYêu cầu thêm: {o.get('note') or 'không có'}\n\n"
              "Yêu cầu cho một truyện dài hơi:\n"
              "- Xung đột trung tâm đủ lớn để kéo dài cả bộ, mỗi quyển có xung đột riêng, kẻ thù hoặc thử thách riêng, "
              "và đẩy nhân vật chính tiến lên một nấc (sức mạnh, địa vị, hiểu biết, tình cảm).\n"
              "- Các quyển leo thang dần về quy mô; gài những bí ẩn lớn từ quyển đầu và mở dần ở các quyển sau.\n"
              "- 8-14 nhân vật quan trọng có mong muốn và bí mật riêng; 5-10 địa danh chính.\n"
              "- setting và rules viết chi tiết (8-15 câu mỗi phần) để giữ nhất quán suốt nghìn chương; "
              "rules là quy luật thế giới / hệ thống sức mạnh / luật lệ xã hội, ghi rõ các cấp bậc nếu có.\n"
              "- style: ngôi kể, cách xưng hô giữa các nhân vật, nhịp văn.\n"
              "- Mỗi quyển: summary 5-8 câu, goal, climax; chapters là số chương của quyển (tổng gần bằng "
              f"{s.total}).\n"
              "- ending: kết cục của cả bộ (2-4 câu), để mọi quyển đều hướng về đó.\n"
              "- look_en: ngoại hình nhân vật bằng tiếng Anh dưới 15 từ (để vẽ minh họa nhất quán); "
              "art_style: phong cách tranh minh họa bằng tiếng Anh dưới 12 từ (tranh vẽ, không phải ảnh chụp).")
    progress("series_plan", 5, "AI đang thiết kế thế giới, nhân vật và các quyển...")
    b = ai.ask(SYSTEM, [{"role": "user", "content": prompt}], BIBLE_SCHEMA,
               on_text=lambda k: progress("series_plan", min(95, 8 + k / 200), "AI đang lập sổ tay bộ truyện..."))
    arcs = b.pop("arcs")
    if not arcs:
        raise RuntimeError("AI không chia được quyển, hãy chạy lại")
    # chia lại số chương mỗi quyển cho khớp tổng
    w = [max(1, int(a.get("chapters") or 1)) for a in arcs]
    tot = sum(w)
    start = 1
    for i, a in enumerate(arcs):
        cnt = s.total - start + 1 if i == len(arcs) - 1 else max(1, round(w[i] / tot * s.total))
        cnt = max(1, min(cnt, s.total - start + 1 - (len(arcs) - 1 - i)))
        a.update(start=start, end=start + cnt - 1, beats=[], recap="")
        a.pop("chapters", None)
        start += cnt
    b["extra_characters"] = []
    s.data.update(title=b["title"], bible=b, arcs=arcs, memory={"states": {}, "threads": []}, written=0)
    for f in (s.dir / "chapters").glob("*.json"):
        f.unlink()
    s.spend(ai)
    s.save()
    progress("series_plan", 100, f"Xong: {b['title']}, {len(arcs)} quyển, {len(b['characters'])} nhân vật")


def _plan_arc(s, k, ai):
    """Dàn ý từng chương của quyển k, dựa trên những gì đã thật sự xảy ra."""
    a = s.data["arcs"][k]
    need = a["end"] - a["start"] + 1
    beats = list(a.get("beats") or [])
    while len(beats) < need:
        first = a["start"] + len(beats)
        cnt = min(50, need - len(beats))
        progress("series_write", None, f"AI đang lập dàn ý quyển {k + 1}, chương {first}-{first + cnt - 1}...")
        done = "\n".join(f"Chương {a['start'] + i}: {b['title']} — {b['beats']}" for i, b in enumerate(beats))
        req = (f"Lập dàn ý chi tiết cho chương {first} đến {first + cnt - 1} của quyển {k + 1} \"{a['title']}\" "
               f"(quyển này gồm chương {a['start']}-{a['end']}), đúng {cnt} chương, theo thứ tự.\n"
               + (f"Các chương đầu của quyển đã lập:\n{done}\n" if done else "")
               + "Mỗi chương: title (tên chương ngắn, không ghi chữ \"Chương\" và số), beats (3-5 câu: diễn biến chính, "
                 "nhân vật tham gia, thông tin gài hoặc hé lộ, móc câu cuối chương). Bám mục tiêu và cao trào của quyển, "
                 "nối liền với trạng thái hiện tại và các nút thắt đang mở; mở dần một số nút thắt cũ. "
                 "Xen kẽ chương hành động, chương khám phá, chương lắng cảm xúc; cao trào quyển rơi vào vài chương cuối.")
        msgs = [{"role": "user", "content": [_bible_text(s), _arc_text(s, k) if k else "",
                                             _memory_text(s, a["start"] + len(beats)), req]}]
        r = ai.ask(SYSTEM, msgs, BEATS_SCHEMA, effort="medium")
        got = [{"title": re.sub(r"(?i)^chương\s*\d+\s*[:.\-–]?\s*", "", c["title"]).strip() or c["title"],
                "beats": c["beats"]} for c in r["chapters"]][:cnt]
        if not got:
            raise RuntimeError("AI không lập được dàn ý quyển, hãy chạy lại")
        beats += got
        a["beats"] = beats
        s.spend(ai)
        s.save()


def _recap_arc(s, k, ai):
    a = s.data["arcs"][k]
    sums = "\n".join(f"Chương {i}: {s.chapter(i)['summary']}" for i in range(a["start"], a["end"] + 1) if s.chapter(i))
    progress("series_write", None, f"AI đang tóm tắt quyển {k + 1}...")
    r = ai.ask(SYSTEM, [{"role": "user", "content": f"Tóm tắt các chương của quyển {k + 1} \"{a['title']}\":\n{sums}\n\n"
                         "Viết summary 8-12 câu: các biến cố chính, ai được gì mất gì, bí mật nào đã lộ, quan hệ thay đổi "
                         "thế nào, quyển kết thúc ở đâu. Chỉ dữ kiện, không bình luận."}], SUMMARY_SCHEMA, effort="low")
    a["recap"] = r["summary"]
    s.spend(ai)
    s.save()


# ---------- viết ----------

def _resummarize(s, n, ai):
    """Người dùng đã sửa chương n trong app: tóm tắt lại để các chương sau bám đúng bản đã sửa."""
    c = s.chapter(n)
    r = ai.ask(SYSTEM, [{"role": "user", "content": f"Chương {n} \"{c['title']}\":\n{c['text']}\n\n"
                         "Tóm tắt chương trong 3-5 câu: diễn biến chính, thông tin mới, chương dừng ở đâu."}],
               SUMMARY_SCHEMA, effort="low")
    c["summary"] = r["summary"]
    c.pop("edited", None)
    s.save_chapter(c)
    s.spend(ai)


def _match(name, names):
    k = name.strip().lower()
    for x in names:
        if x.strip().lower() == k:
            return x
    return None


def write(folder, settings, count=10):
    s = Series(folder)
    if not s.data.get("arcs"):
        raise RuntimeError("Chưa lập sổ tay bộ truyện")
    ai = make_writer(settings)
    have = s.count_written()
    if have != s.data.get("written"):
        # có chương bị xóa tay hoặc lần trước dừng giữa chừng: lấy trạng thái theo chương cuối còn trên đĩa
        s.data["written"] = have
        last = s.chapter(have)
        s.data["memory"] = json.loads(json.dumps(last["memory"])) if last else {"states": {}, "threads": []}
        s.save()
    if have >= s.total:
        progress("series_write", 100, "Bộ truyện đã viết xong")
        return
    goal = min(s.total, have + max(1, int(count)))
    t0 = time.time()
    for n in range(have + 1, goal + 1):
        for i in range(max(1, n - RECENT_FULL), n):
            c = s.chapter(i)
            if c and c.get("edited"):
                _resummarize(s, i, ai)
        k = s.arc_of(n)
        a = s.data["arcs"][k]
        if len(a.get("beats") or []) < a["end"] - a["start"] + 1:
            _plan_arc(s, k, ai)
        beat = a["beats"][n - a["start"]]
        nxt = a["beats"][n - a["start"] + 1] if n < a["end"] else None
        chars = int(s.opt["chapter_chars"])
        req = (f"Viết trọn CHƯƠNG {n}/{s.total} \"{beat['title']}\" (quyển {k + 1}).\n"
               f"Dàn ý chương này: {beat['beats']}\n"
               + (f"Chương sau sẽ là \"{nxt['title']}\": {nxt['beats']} (chưa viết tới, chỉ để dẫn dắt).\n" if nxt else
                  ("Đây là chương cuối của quyển: khép cao trào quyển, mở hướng cho quyển sau.\n" if n < s.total else
                   "Đây là CHƯƠNG CUỐI CÙNG của cả bộ: giải quyết xung đột trung tâm và các nút thắt còn mở, "
                   "khép lại trọn vẹn.\n"))
               + f"\ntext: khoảng {chars} ký tự, TỐI ĐA {int(chars * 1.15)} ký tự (một trang sách, viết gọn, đừng kéo "
                 "dài), nối liền mạch từ đúng cảnh cuối chương trước (không tả lại khung cảnh đã tả), "
                 "chia đoạn 2-5 câu, mỗi đoạn một dòng; không ghi tên chương; dừng ở một móc câu.\n"
                 "summary: 3-5 câu tóm tắt dữ kiện của chương (ai làm gì, biết được gì, chương dừng ở đâu).\n"
                 "states: trạng thái mới của các nhân vật có thay đổi trong chương (vị trí, sức mạnh/cấp bậc, vết thương, "
                 "đồ vật quan trọng, quan hệ, điều họ vừa biết), mỗi người một câu; nhân vật không đổi thì bỏ qua.\n"
                 "new_characters: nhân vật có tên lần đầu xuất hiện (look_en: ngoại hình tiếng Anh dưới 15 từ).\n"
                 "threads_opened: chi tiết bí ẩn/lời hứa/âm mưu mới được gài, mỗi cái một câu ngắn.\n"
                 "threads_closed: số thứ tự [i] của các nút thắt đang mở đã được giải quyết hẳn trong chương này.")
        msgs = [{"role": "user", "content": [_bible_text(s), _arc_text(s, k), _memory_text(s, n), req]}]
        done_cnt = n - have - 1
        eta = ""
        if done_cnt:
            left = (time.time() - t0) / done_cnt * (goal - n + 1)
            eta = f", còn khoảng {left / 60:.0f} phút"
        msg = f"AI đang viết chương {n}/{s.total}: {beat['title']}{eta}"
        progress("series_write", done_cnt / (goal - have) * 100, msg)
        r = ai.ask(SYSTEM, msgs, CHAPTER_SCHEMA, on_text=lambda x, d=done_cnt, m=msg: progress(
            "series_write", (d + min(0.97, x / (chars * 1.4))) / (goal - have) * 100, m))
        text = _clean(r["text"])
        if len(text) < chars * 0.35:
            raise RuntimeError(f"AI viết chương {n} quá ngắn ({len(text)} ký tự), hãy chạy lại")
        mem = s.data["memory"]
        names = [c["name"] for c in s.data["bible"]["characters"] + s.data["bible"]["extra_characters"]]
        for c in r.get("new_characters", []):
            if c["name"].strip() and not _match(c["name"], names):
                s.data["bible"]["extra_characters"].append(c)
                names.append(c["name"])
        for x in r.get("states", []):
            key = _match(x["name"], mem["states"]) or _match(x["name"], names) or x["name"].strip()
            if key:
                mem["states"][key] = x["state"].strip()
        closed = {i for i in r.get("threads_closed", []) if 0 <= i < len(mem["threads"])}
        mem["threads"] = [t for i, t in enumerate(mem["threads"]) if i not in closed]
        mem["threads"] += [t.strip() for t in r.get("threads_opened", []) if t.strip()]
        mem["threads"] = mem["threads"][-40:]  # quá nhiều nút thắt thì giữ các cái mới nhất
        s.save_chapter({"n": n, "title": beat["title"], "text": text, "summary": r["summary"].strip(), "arc": k,
                        "memory": json.loads(json.dumps(mem))})
        s.data["written"] = n
        s.spend(ai)
        s.save()
        log(f"Xong chương {n}: {beat['title']} ({len(text)} ký tự)")
        if n == a["end"]:
            _recap_arc(s, k, ai)
    progress("series_write", 100, f"Đã viết tới chương {goal}/{s.total}")
    if s.opt.get("auto_publish") and settings.get("web_url") and settings.get("web_api_key"):
        publish(folder, settings)


def rewind(folder, n):
    """Xóa chương n trở đi để viết lại; trạng thái quay về sau chương n-1."""
    s = Series(folder)
    n = max(1, int(n))
    i = n
    while s.ch_file(i).exists():
        s.ch_file(i).unlink()
        i += 1
    prev = s.chapter(n - 1)
    s.data["memory"] = prev["memory"] if prev else {"states": {}, "threads": []}
    s.data["written"] = n - 1
    for a in s.data["arcs"]:
        if a["end"] >= n:
            a["recap"] = ""
    s.save()
    return n - 1


def _clean(text):
    lines = []
    for x in (text or "").strip().splitlines():
        x = re.sub(r"^#+\s*", "", x.strip()).replace("*", "")
        if not x or re.fullmatch(r"[-–—_=]{3,}", x):
            continue
        if not lines and re.match(r"(?i)^chương\s+\d+\b.{0,80}$", x):
            continue
        lines.append(x)
    return "\n".join(lines)


# ---------- cắt tập video ----------

def episode(folder, workspace, settings, minutes=0):
    """Tạo project Truyện kể cho tập video kế tiếp (15-20 phút), nối tiếp tập trước."""
    from . import story
    s = Series(folder)
    minutes = float(minutes or s.opt.get("episode_minutes") or 17)
    eps = s.data.setdefault("episodes", [])
    k = len(eps) + 1
    first = eps[-1]["to"] + 1 if eps else 1
    written = s.count_written()
    if first > written:
        raise RuntimeError(f"Chưa có chương {first} để làm tập {k}: hãy viết thêm chương trước")
    need = minutes * 60 * CPS
    chs, total = [], 0
    n = first
    while n <= written and (total < need * 0.92 or not chs):
        c = s.chapter(n)
        # thêm chương này làm tập dài quá 20% so với mục tiêu thì để dành cho tập sau
        if chs and total + len(c["text"]) > need * 1.2:
            break
        chs.append(c)
        total += len(c["text"])
        n += 1
    last = chs[-1]["n"]
    if total < need * 0.6 and last < s.total:
        log(f"Mới có {total / CPS / 60:.0f} phút truyện cho tập {k}; viết thêm chương thì tập sẽ dài hơn")
    title = s.data["title"]
    b = s.data["bible"]
    progress("series_episode", 10, f"Tập {k}: chương {first}-{last}, đang viết lời nhắc tập trước và hé lộ tập sau...")

    ai = None
    try:
        ai = make_writer(settings)
    except Exception:
        pass
    recap = _episode_recap(s, ai, first) if first > 1 else ""
    teaser = _episode_teaser(s, ai, last) if last < s.total else ""

    intro = f"{title}. Tập {k}." + (" Tóm tắt tập trước. " + recap if recap else "")
    texts = []
    for i, c in enumerate(chs):
        head = f"Chương {c['n']}. {c['title']}."
        texts.append({"title": f"Chương {c['n']}: {c['title']}",
                      "text": "\n".join([intro] if i == 0 else []) + ("\n" if i == 0 else "") + head + "\n" + c["text"]})
    outro = (f"Hết tập {k}. " + (teaser + " " if teaser else "") + f"Mời các bạn đón nghe tập {k + 1}."
             if last < s.total else f"Hết tập {k}. Bộ truyện {title} đến đây là kết thúc. Cảm ơn các bạn đã theo dõi.")
    texts[-1]["text"] += "\n" + outro

    imgs = max(len(chs), min(60, round(total / CPS / 70)))
    proj = story.create(workspace, {
        "story_topic": f"{title} - Tập {k}", "story_polish": False, "story_minutes": round(total / CPS / 60),
        "story_images": imgs, "voice": s.opt.get("voice") or "vieneu:Thiện Minh",
        "story_episode": {"series": str(s.dir), "title": title, "n": k, "from": first, "to": last,
                          "web_url": s.data.get("web", {}).get("url", "")},
        **{k2: v for k2, v in (s.opt.get("video") or {}).items()},
    })
    looks = b["characters"] + b.get("extra_characters", [])
    st = {"cost": 0, "outline": {
        "title": f"{title} - Tập {k}", "logline": b["logline"], "theme": b["theme"], "setting": b["setting"],
        "art_style": b["art_style"],
        "characters": [{"name": c["name"], "role": c["role"], "voice": "", "look_en": c.get("look_en", "")} for c in looks],
        "chapters": [{"title": t["title"], "summary": c["summary"]} for t, c in zip(texts, chs)]},
        "chapters": texts}
    story.save(proj, st)
    proj.meta["name"] = f"{title} - Tập {k} (chương {first}-{last})"[:90]
    proj.save()
    proj.mark("outline")
    proj.mark("write")
    eps.append({"n": k, "from": first, "to": last, "project": str(proj.dir), "minutes": round(total / CPS / 60, 1),
                "created": time.strftime("%Y-%m-%d %H:%M")})
    if ai:
        s.spend(ai)
    s.save()
    progress("series_episode", 100, f"Đã tạo tập {k} ({round(total / CPS / 60)} phút)")
    return proj, k, first, last


def _episode_recap(s, ai, first):
    """2-3 câu nhắc lại tập trước để người xem tập này không bị hụt mạch."""
    prev = s.data["episodes"][-1] if s.data.get("episodes") else None
    lo = prev["from"] if prev else max(1, first - 4)
    sums = [s.chapter(i)["summary"] for i in range(lo, first) if s.chapter(i)]
    if not sums:
        return ""
    if ai:
        try:
            r = ai.ask(SYSTEM, [{"role": "user", "content": "Tóm tắt các chương của tập trước:\n" + "\n".join(sums)
                                 + "\n\nViết lời nhắc lại tập trước để đọc ở đầu video: 2-3 câu, văn kể chuyện, gợi đúng "
                                   "tình thế đang dang dở, không liệt kê."}], SUMMARY_SCHEMA, effort="low")
            return r["summary"].strip()
        except Exception as e:
            log(f"Không viết được lời nhắc tập trước ({e}), dùng tóm tắt chương")
    return " ".join(sums[-2:])


def _episode_teaser(s, ai, last):
    """Một câu hé lộ tập sau, không tiết lộ kết quả."""
    k = s.arc_of(last + 1)
    a = s.data["arcs"][k]
    b = (a.get("beats") or [])
    i = last + 1 - a["start"]
    hint = b[i]["beats"] if 0 <= i < len(b) else a["summary"]
    if ai:
        try:
            r = ai.ask(SYSTEM, [{"role": "user", "content": f"Diễn biến sắp tới: {hint}\n\nViết một câu hé lộ tập sau "
                                 "để đọc ở cuối video, gây tò mò, không tiết lộ kết quả, bắt đầu bằng \"Tập sau\"."}],
                       SUMMARY_SCHEMA, effort="low")
            return r["summary"].strip()
        except Exception:
            pass
    return ""


# ---------- đăng lên web đọc truyện ----------

def _api(settings):
    import httpx
    url = settings.get("web_url", "").strip().rstrip("/")
    key = settings.get("web_api_key", "").strip()
    if not url or not key:
        raise RuntimeError("Chưa nhập địa chỉ web truyện và API key trong Cài đặt")
    return httpx.Client(base_url=url, headers={"X-Api-Key": key}, timeout=60)


def _check(r):
    if r.status_code >= 400:
        try:
            d = r.json().get("detail")
        except Exception:
            d = r.text[:300]
        raise RuntimeError(f"Web truyện báo lỗi {r.status_code}: {d}")
    return r.json()


def publish(folder, settings):
    """Đăng/cập nhật bộ truyện và các chương đã viết lên web (chỉ gửi chương mới hoặc đã sửa)."""
    s = Series(folder)
    if not s.data.get("bible"):
        raise RuntimeError("Chưa lập sổ tay bộ truyện")
    web = s.data.setdefault("web", {})
    sent = web.setdefault("published", {})
    b = s.data["bible"]
    progress("series_publish", 2, "Đang kết nối web truyện...")
    with _api(settings) as c:
        written = s.count_written()
        info = _check(c.post("/api/stories", json={
            "source_id": s.dir.name, "title": s.data["title"], "description": b["logline"] + "\n\n" + b["setting"],
            "genres": [g.strip() for g in re.split(r"[,;/]", s.opt.get("genre") or "") if g.strip()],
            "status": "completed" if written >= s.total else "ongoing", "origin": "ai",
            **({"author_name": settings["web_author"]} if settings.get("web_author") else {})}))
        web.update(story_id=info["id"], url=info["url"], slug=info.get("slug", ""))
        cover = s.dir / "cover.jpg"
        if cover.exists() and web.get("cover") != cover.stat().st_mtime:
            with open(cover, "rb") as f:
                _check(c.post(f"/api/stories/{info['id']}/cover", files={"file": ("cover.jpg", f, "image/jpeg")}))
            web["cover"] = cover.stat().st_mtime
        todo = []
        for n in range(1, written + 1):
            ch = s.chapter(n)
            h = hashlib.md5((ch["title"] + "\n" + ch["text"]).encode()).hexdigest()[:12]
            if sent.get(str(n)) != h:
                todo.append((n, ch, h))
        for i in range(0, len(todo), 100):
            part = todo[i:i + 100]
            progress("series_publish", 5 + i / max(1, len(todo)) * 94, f"Đang đăng chương {part[0][0]}-{part[-1][0]}...")
            _check(c.put(f"/api/stories/{info['id']}/chapters", json={"chapters": [
                {"n": n, "title": ch["title"], "content": ch["text"]} for n, ch, _ in part]}))
            for n, _, h in part:
                sent[str(n)] = h
            s.save()
    s.save()
    progress("series_publish", 100, f"Đã đăng {len(todo)} chương mới/sửa → {web['url']}")
    return web["url"]
