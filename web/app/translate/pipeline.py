"""Các bước dịch: lập bảng thuật ngữ, dịch thông tin truyện, dịch từng chương."""
import time

from ..db import ex, now, q
from ..importer.text import parse_heading
from ..utils import normalize_content
from . import prompts
from .providers import ProviderError


def call(provider, system_blocks, prompt, schema, attempts=4):
    """Gọi AI, tự đợi và thử lại khi bị giới hạn tốc độ/quá tải."""
    for i in range(attempts):
        try:
            return provider.generate(system_blocks, prompt, schema)
        except ProviderError as e:
            if e.fatal or e.retry_after is None or i == attempts - 1:
                raise
            time.sleep(e.retry_after)
    raise ProviderError("Không gọi được AI")


def load_glossary(story_id):
    return [dict(r) for r in q("SELECT zh, vi, kind FROM glossary WHERE story_id=? ORDER BY kind, zh", (story_id,))]


def merge_terms(story_id, terms, conn=None):
    """Thêm thuật ngữ mới; không ghi đè mục đã có (người đăng có thể đã sửa tay)."""
    added = 0
    for t in terms or []:
        zh = str(t.get("zh", "")).strip()[:60]
        vi = str(t.get("vi", "")).strip()[:80]
        if not zh or not vi or zh == vi:
            continue
        cur = ex("INSERT OR IGNORE INTO glossary(story_id, zh, vi, kind, created_at) VALUES (?,?,?,?,?)",
                 (story_id, zh, vi, str(t.get("kind", ""))[:20], now()), conn)
        added += cur.rowcount
    return added


def build_glossary(provider, story_title, first_chapters):
    """first_chapters: [(tiêu đề, nội dung)]. Trả (genre, terms, Result)."""
    prompt = prompts.build_glossary_prompt(story_title, first_chapters)
    res = call(provider, [prompts.SYSTEM_GLOSSARY], prompt, prompts.GLOSSARY_SCHEMA)
    return str(res.data.get("genre", "")).strip()[:60], res.data.get("terms") or [], res


def translate_meta(provider, title, author, description, category, site_genres, sample=""):
    prompt = prompts.build_meta_prompt(title, author, description, category, site_genres, sample)
    res = call(provider, [prompts.SYSTEM_META], prompt, prompts.META_SCHEMA)
    return res.data, res


def chapter_title_zh(raw: str) -> str:
    """'第十二章 少年出山' → '少年出山'."""
    h = parse_heading(raw or "")
    return h[1] if h else (raw or "")


def translate_chapter(provider, n, title_zh, content_zh, glossary, prev_tail="", summary="", genre=""):
    system = [prompts.SYSTEM_TRANSLATE, prompts.glossary_block(glossary)]
    prompt = prompts.build_chapter_prompt(n, title_zh, content_zh, prev_tail, summary, genre)
    res = call(provider, system, prompt, prompts.CHAPTER_SCHEMA)
    d = res.data
    content = normalize_content(str(d.get("content", "")))
    if not content.strip():
        raise ProviderError("AI trả về bản dịch rỗng", retry_after=5)
    title = str(d.get("title", "")).strip()
    # AI lỡ thêm "Chương 12:" vào tiêu đề thì bỏ đi
    h = parse_heading(title)
    if h:
        title = h[1]
    return {"title": title[:200], "content": content, "new_terms": d.get("new_terms") or [],
            "summary": str(d.get("summary", "")).strip()[:1500]}, res


def tail_paragraphs(content: str, k=3, max_chars=900) -> str:
    paras = [p for p in (content or "").split("\n\n") if p.strip()][-k:]
    text = "\n\n".join(paras)
    return text[-max_chars:]
