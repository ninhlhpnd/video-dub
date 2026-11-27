"""Giải mã file chữ (utf-8 / gb18030 / big5) và tách chương theo tiêu đề."""
import re

# chữ Hán thông dụng nhất, dùng chấm điểm khi đoán bảng mã
_COMMON_SC = set("的一是不了人我在有他这中大来上个国到说们为子和你地出道也时年得就那要下以生会自着去之过家学对可她里后小么心多天而能好都然没日于起还发成事只作当想看文无开手十用主行方又如前所本见经头面公同三已老从动两长知民样现分将外但身些与高意进把法此实回二理美点月明其种声全工己话儿者向情部正名定女问力机给等几很业最间新什打便位因重被走电四第门相次东政海口使教西再平真听世气信北少关并内加化由却代军产入先山五太水万市眼体别处总才场师书")
_COMMON_TC = set("的一是不了人我在有他這中大來上個國到說們為子和你地出道也時年得就那要下以生會自著去之過家學對可她裡後小麼心多天而能好都然沒日於起還發成事只作當想看文無開手十用主行方又如前所本見經頭面公同三已老從動兩長知民樣現分將外但身些與高意進把法此實回二理美點月明其種聲全工己話兒者向情部正名定女問力機給等幾很業最間新什打便位因重被走電四第門相次東政海口使教西再平真聽世氣信北少關並內加化由卻代軍產入先山五太水萬市眼體別處總才場師書")


def _score(text: str) -> float:
    if not text:
        return 0
    sample = text[:20000]
    good = sum(1 for c in sample if c in _COMMON_SC or c in _COMMON_TC)
    bad = sum(1 for c in sample if c == "�" or "" <= c <= "")
    return (good - 20 * bad) / max(1, len(sample))


def decode_bytes(data: bytes, hint: str | None = None) -> str:
    """Thử utf-8 → gb18030 (gồm gbk/gb2312) → big5; nếu nhiều bảng mã cùng giải được thì chọn
    bảng mã cho nhiều chữ Hán thông dụng hơn. Cuối cùng mới nhờ charset-normalizer."""
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    order = []
    if hint:
        h = hint.lower().strip()
        if h in ("gbk", "gb2312", "gb_2312-80", "x-gbk", "gb18030"):
            h = "gb18030"
        order.append(h)
    for enc in ("utf-8", "gb18030", "big5"):
        if enc not in order:
            order.append(enc)
    try:
        return data.decode(order[0])  # bảng mã trang khai báo hoặc utf-8, giải được trọn vẹn thì dùng luôn
    except (UnicodeDecodeError, LookupError):
        pass
    candidates = []
    for enc in order[1:]:
        try:
            candidates.append((enc, data.decode(enc)))
        except (UnicodeDecodeError, LookupError):
            continue
    if candidates:
        candidates.sort(key=lambda c: _score(c[1]), reverse=True)
        return candidates[0][1]
    try:
        from charset_normalizer import from_bytes
        best = from_bytes(data).best()
        if best is not None:
            return str(best)
    except Exception:
        pass
    return data.decode("gb18030", errors="replace")


# ---- số Hán → số ----
_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "兩": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7,
              "八": 8, "九": 9}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000, "萬": 10000}


def cn_to_int(s: str) -> int | None:
    s = s.strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    # dạng 一二三 (viết liền từng chữ số)
    if all(c in _CN_DIGITS for c in s):
        return int("".join(str(_CN_DIGITS[c]) for c in s))
    total, section, num = 0, 0, 0
    for c in s:
        if c in _CN_DIGITS:
            num = _CN_DIGITS[c]
        elif c in _CN_UNITS:
            unit = _CN_UNITS[c]
            if unit == 10000:
                section = (section + num) * unit
                total += section
                section = 0
            else:
                section += (num or 1) * unit
            num = 0
        else:
            return None
    return total + section + num


CN_NUM = r"[0-9０-９零〇一二三四五六七八九十百千万萬两兩]+"
CHAPTER_ZH_RE = re.compile(rf"第\s*({CN_NUM})\s*[章节節回]")
HEADING_RE = re.compile(
    rf"^\s*(?:"
    rf"第\s*(?P<zh>{CN_NUM})\s*[章节節回](?![节節])"
    rf"|(?:Chương|CHƯƠNG|chương|Chuong|CHUONG|Chapter|CHAPTER)\s+(?P<vi>\d+)"
    rf")\s*(?:[:：\-–—.、,，]\s*|\s+|$)(?P<title>.*)$"
)


def _fw_digits(s):
    return s.translate(str.maketrans("０１２３４５６７８９", "0123456789"))


def parse_heading(line: str):
    """Dòng tiêu đề chương → (số, tiêu đề) hoặc None."""
    line = line.strip().replace("　", " ")
    if not line or len(line) > 60:
        return None
    m = HEADING_RE.match(line)
    if not m:
        return None
    raw = m.group("zh") or m.group("vi")
    n = cn_to_int(_fw_digits(raw))
    if n is None:
        return None
    title = m.group("title").strip(" :：-–—.")
    return n, title


def split_chapters(text: str, numbering: str = "auto", start: int = 1):
    """Tách văn bản thành chương theo dòng tiêu đề "Chương 12: ...", "Chương 12 - ...", "第12章 ...".
    numbering: auto (dùng số trong tiêu đề nếu tăng dần, trùng/lộn xộn thì đánh tuần tự),
               heading (luôn dùng số trong tiêu đề), seq (đánh tuần tự từ `start`).
    Trả về list dict {n, title, content}."""
    from ..utils import normalize_content
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    chapters, cur = [], None
    for ln in lines:
        h = parse_heading(ln)
        if h:
            cur = {"num": h[0], "title": h[1], "lines": []}
            chapters.append(cur)
        elif cur is not None:
            cur["lines"].append(ln)
    if not chapters:
        body = normalize_content(text)
        return [{"n": start, "title": "", "content": body}] if body else []
    out = []
    for c in chapters:
        content = normalize_content("\n".join(c["lines"]))
        if content:
            out.append({"num": c["num"], "title": c["title"], "content": content})
    nums = [c["num"] for c in out]
    increasing = all(b > a for a, b in zip(nums, nums[1:]))
    use_heading = numbering == "heading" or (numbering == "auto" and increasing)
    if use_heading:
        # trùng số thì giữ bản sau cùng
        dedup = {}
        for c in out:
            dedup[c["num"]] = c
        return [{"n": c["num"], "title": c["title"], "content": c["content"]} for c in sorted(dedup.values(), key=lambda c: c["num"])]
    return [{"n": start + i, "title": c["title"], "content": c["content"]} for i, c in enumerate(out)]
