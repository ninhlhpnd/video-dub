"""Lời nhắc (prompt) cho AI dịch truyện Trung → Việt."""
import json

SYSTEM_TRANSLATE = """Bạn là dịch giả truyện mạng Trung Quốc sang tiếng Việt chuyên nghiệp, đã dịch hàng trăm bộ tiên hiệp, huyền huyễn, đô thị, ngôn tình.

Yêu cầu bản dịch:
- Văn tiếng Việt tự nhiên, trôi chảy, đúng văn phong truyện mạng Việt Nam; không dịch word-by-word, không để sót câu, không thêm bớt tình tiết.
- Tiên hiệp, huyền huyễn, kiếm hiệp, lịch sử: tên người, địa danh, môn phái, công pháp, cảnh giới, pháp bảo, danh hiệu dùng âm Hán Việt (ví dụ 林动 → Lâm Động, 筑基 → Trúc Cơ, 宗门 → tông môn). Đô thị, ngôn tình hiện đại: tên người vẫn dùng Hán Việt, từ ngữ đời thường dịch thuần Việt.
- Xưng hô hợp ngữ cảnh và quan hệ nhân vật: ta/ngươi, hắn, nàng, lão phu, tại hạ, sư huynh/sư muội, bổn tọa, các hạ… cho truyện cổ đại; anh/em, tôi/cậu, cô ấy, anh ta… cho truyện hiện đại. Giữ xưng hô nhất quán giữa các chương.
- Luôn dùng đúng cách dịch trong BẢNG THUẬT NGỮ nếu từ gốc xuất hiện.
- Giữ nguyên cách chia đoạn của bản gốc: mỗi đoạn gốc là một đoạn dịch, các đoạn cách nhau bằng một dòng trống. Lời thoại đặt trong ngoặc kép “…”.
- Không thêm lời bình, chú thích, tiêu đề "Chương …" hay bất kỳ câu nào của người dịch vào nội dung.
- Bỏ các dòng quảng cáo/rác của trang web nếu còn sót (tên trang, link, "xin bình chọn", "cầu phiếu"…).
- Chỉ trả về JSON đúng lược đồ được yêu cầu."""

SYSTEM_GLOSSARY = """Bạn là biên tập viên truyện mạng Trung Quốc dịch sang tiếng Việt. Nhiệm vụ: lập bảng thuật ngữ để nhiều chương dịch sau dùng nhất quán.
Trích các tên riêng và thuật ngữ đặc thù: nhân vật, địa danh, môn phái/thế lực, công pháp/võ kỹ, cảnh giới tu luyện, pháp bảo/vật phẩm, danh hiệu/chức vị, chủng tộc.
Cách dịch: âm Hán Việt chuẩn, viết hoa đúng kiểu tiếng Việt (林动 → Lâm Động; 青阳镇 → Thanh Dương trấn; 天玄宗 → Thiên Huyền Tông; 筑基期 → Trúc Cơ kỳ). Không trích từ thông dụng.
Chỉ trả về JSON đúng lược đồ."""

SYSTEM_META = """Bạn là biên tập viên trang đọc truyện tiếng Việt. Dịch thông tin truyện Trung Quốc sang tiếng Việt: tên truyện theo âm Hán Việt (kiểu tên truyện convert quen thuộc, ví dụ 斗破苍穹 → Đấu Phá Thương Khung), tên tác giả âm Hán Việt, giới thiệu dịch mượt mà. Chỉ trả về JSON đúng lược đồ."""

KINDS = ["nhân vật", "địa danh", "môn phái", "công pháp", "cảnh giới", "vật phẩm", "danh hiệu", "khác"]

TERM_SCHEMA = {
    "type": "object",
    "properties": {
        "zh": {"type": "string"},
        "vi": {"type": "string"},
        "kind": {"type": "string"},
    },
    "required": ["zh", "vi", "kind"],
    "additionalProperties": False,
}

GLOSSARY_SCHEMA = {
    "type": "object",
    "properties": {
        "genre": {"type": "string"},
        "terms": {"type": "array", "items": TERM_SCHEMA},
    },
    "required": ["genre", "terms"],
    "additionalProperties": False,
}

CHAPTER_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "content": {"type": "string"},
        "new_terms": {"type": "array", "items": TERM_SCHEMA},
        "summary": {"type": "string"},
    },
    "required": ["title", "content", "new_terms", "summary"],
    "additionalProperties": False,
}

META_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "author": {"type": "string"},
        "description": {"type": "string"},
        "genres": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "author", "description", "genres"],
    "additionalProperties": False,
}


def glossary_block(terms) -> str:
    """Bảng thuật ngữ đặt sau system prompt (ổn định, sắp xếp cố định để cache được)."""
    rows = sorted(((t["zh"], t["vi"], t["kind"] or "") for t in terms), key=lambda r: (r[2], r[0]))
    if not rows:
        return "BẢNG THUẬT NGỮ: (chưa có)"
    lines = [f"{zh} → {vi}" + (f" ({kind})" if kind else "") for zh, vi, kind in rows]
    return "BẢNG THUẬT NGỮ (bắt buộc dùng đúng):\n" + "\n".join(lines)


def build_glossary_prompt(title: str, chapters, max_chars: int = 15000) -> str:
    """chapters: list (tiêu đề, nội dung tiếng Trung) của vài chương đầu."""
    parts, used = [], 0
    for t, c in chapters:
        room = max_chars - used
        if room <= 200:
            break
        text = c[:room]
        used += len(text)
        parts.append(f"### {t}\n{text}")
    return (
        f"Truyện: {title}\n\n"
        "Đọc các chương đầu dưới đây và lập bảng thuật ngữ (tối đa 80 mục, ưu tiên tên xuất hiện nhiều). "
        f"Trường kind chọn một trong: {', '.join(KINDS)}. "
        "Trường genre: thể loại chính của truyện bằng tiếng Việt (ví dụ: tiên hiệp, huyền huyễn, đô thị, ngôn tình cổ đại).\n\n"
        + "\n\n".join(parts)
    )


def build_chapter_prompt(n: int, title_zh: str, content_zh: str, prev_tail: str = "", summary: str = "",
                         genre: str = "") -> str:
    ctx = []
    if genre:
        ctx.append(f"Thể loại: {genre}")
    if summary:
        ctx.append(f"Tóm tắt cốt truyện tới chương trước:\n{summary}")
    if prev_tail:
        ctx.append(f"Mấy đoạn cuối bản dịch chương trước (để nối mạch, xưng hô cho khớp):\n{prev_tail}")
    head = "\n\n".join(ctx)
    return (
        (head + "\n\n" if head else "")
        + f"Dịch chương {n} dưới đây.\n"
        "- title: tên chương tiếng Việt, KHÔNG kèm \"Chương {n}\" (nếu bản gốc không có tên thì để chuỗi rỗng).\n"
        "- content: toàn bộ bản dịch, các đoạn cách nhau bằng một dòng trống.\n"
        "- new_terms: tên riêng/thuật ngữ MỚI xuất hiện ở chương này mà bảng thuật ngữ chưa có (zh, vi Hán Việt, kind).\n"
        "- summary: tóm tắt cốt truyện cập nhật tới hết chương này, tối đa 120 từ, gộp với tóm tắt cũ.\n\n"
        f"TIÊU ĐỀ GỐC: {title_zh}\n\nNỘI DUNG GỐC:\n{content_zh}"
    )


def build_meta_prompt(title: str, author: str, description: str, category: str, site_genres, sample: str = "") -> str:
    data = {"title": title, "author": author, "description": description, "category": category}
    extra = ""
    if not description.strip() and sample:
        extra = ("\nBản gốc không có giới thiệu: hãy viết giới thiệu 2-3 câu hấp dẫn dựa trên đoạn mở đầu sau, "
                 "không tiết lộ tình tiết quan trọng:\n" + sample[:2500])
    return (
        "Thông tin truyện gốc (JSON):\n" + json.dumps(data, ensure_ascii=False)
        + f"\n\ngenres: chọn 1-4 thể loại phù hợp nhất trong danh sách: {', '.join(site_genres)}."
        + extra
    )
