"""AI viết tiêu đề + mô tả ngắn, gây tò mò để đăng Facebook/YouTube."""
from .ai import Gemini
from .common import Project

PROMPT = """Viết nội dung đăng kèm video {kind} lên Facebook/YouTube cho người Việt.
Nội dung video (lời tiếng Việt trong video):
{script}
{extra}
Yêu cầu:
- title: tiêu đề tối đa 80 ký tự, gây tò mò, không giật tít sai sự thật, không viết hoa toàn bộ.
- description: 2-3 câu ngắn, gợi tò mò để người xem bấm xem đến cuối, có thể kèm 1-2 emoji, KHÔNG tiết lộ hết kết quả.
- hashtags: 3-5 hashtag tiếng Việt không dấu hoặc tiếng Anh phổ biến, liên quan nội dung.
Trả về JSON: {{"title": "...", "description": "...", "hashtags": ["#..."]}}"""


def run(proj: Project, settings):
    if proj.opt.get("mode") == "story":  # truyện kể: Claude đã viết sẵn lúc chia cảnh
        from . import story
        return story.caption(proj)
    segs = proj.segments()
    script = " ".join(s.get("vi", "") for s in segs if s.get("vi")).strip()
    if not script:
        raise RuntimeError("Chưa có lời tiếng Việt, hãy chạy xong bước dịch/viết lời kể trước")
    review = proj.opt.get("mode") == "review"
    extra = f"Tóm tắt: {proj.meta['review_summary']}" if review and proj.meta.get("review_summary") else ""
    data = Gemini(settings, temperature=0.9).ask(PROMPT.format(
        kind="review phim" if review else "", script=script[:6000], extra=extra))
    tags = [t if t.startswith("#") else f"#{t}" for t in data.get("hashtags", []) if isinstance(t, str)]
    desc = str(data.get("description", "")).strip()
    from . import music
    title = music.title_of(proj.opt.get("music"))
    if title:  # nhạc CC BY: bắt buộc ghi tên tác giả
        desc += "\n\n" + music.credit(title)
    return {
        "title": str(data.get("title", "")).strip()[:100],
        "description": (desc + ("\n\n" + " ".join(tags) if tags else "")).strip(),
        "tags": ",".join(t.lstrip("#") for t in tags),
    }
