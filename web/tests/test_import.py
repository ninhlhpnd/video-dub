"""Tách chương, giải mã GBK/Big5, bóc trang truyện Trung, prompt thuật ngữ, VietQR, việc dịch (AI giả lập)."""
import json

from conftest import FIXTURES

from app.donate import transfer_note, vietqr_url
from app.importer.extract import extract_chapter, extract_toc, fetch_chapter, fetch_toc, same_chapter
from app.importer.text import cn_to_int, decode_bytes, split_chapters
from app.translate import prompts
from app.translate.providers import MockProvider, estimate_claude_cost

ZH = "第一章 惊蛰\n\n二月二，龙抬头。暮色里，小镇名叫泥瓶巷的僻静地方，有位孤苦伶仃的清瘦少年。他说：“我们出去看看吧。”"


# ---------------------------------------------------------------- tách chương

def test_split_vietnamese_headings():
    text = ("Lời mở đầu không thuộc chương nào\n\n"
            "Chương 1: Thiếu niên\n\nĐoạn một.\n\nĐoạn hai.\n\n"
            "Chương 2 - Rời núi\n\nNội dung chương hai.\n\n"
            "CHƯƠNG 3\nNội dung chương ba, tiêu đề trống.\n"
            "Đây là câu có chữ Chương 4 ở giữa dòng nên không phải tiêu đề.")
    ch = split_chapters(text)
    assert [(c["n"], c["title"]) for c in ch] == [(1, "Thiếu niên"), (2, "Rời núi"), (3, "")]
    assert ch[0]["content"] == "Đoạn một.\n\nĐoạn hai."
    assert "Chương 4 ở giữa dòng" in ch[2]["content"]


def test_split_chinese_headings_and_numbering():
    text = "第一章 惊蛰\n内容一\n第二章：少年\n内容二\n第十二章 日出\n内容十二\n第一百零五章 终\n内容"
    ch = split_chapters(text)
    assert [c["n"] for c in ch] == [1, 2, 12, 105]
    assert ch[1]["title"] == "少年"
    # số lộn xộn (quyển mới đánh lại từ 1) → tự đánh tuần tự
    messy = "第1章 A\n甲\n第2章 B\n乙\n第1章 C\n丙"
    assert [c["n"] for c in split_chapters(messy)] == [1, 2, 3]
    assert [c["n"] for c in split_chapters(messy, "seq", 10)] == [10, 11, 12]
    assert cn_to_int("两千零三") == 2003 and cn_to_int("一二三") == 123 and cn_to_int("十") == 10


def test_split_without_headings():
    ch = split_chapters("Chỉ có một đoạn.\nDòng hai.", start=5)
    assert ch == [{"n": 5, "title": "", "content": "Chỉ có một đoạn.\n\nDòng hai."}]


# ---------------------------------------------------------------- bảng mã

def test_decode_gbk_big5_utf8():
    assert decode_bytes(ZH.encode("gbk")) == ZH
    assert decode_bytes(ZH.encode("gb18030")) == ZH
    tw = "第一章 驚蟄\n\n二月二，龍抬頭。暮色裡，小鎮名叫泥瓶巷的僻靜地方，有位孤苦伶仃的清瘦少年。他說：「我們出去看看吧。」"
    assert decode_bytes(tw.encode("big5")) == tw
    assert decode_bytes(ZH.encode("utf-8")) == ZH
    assert decode_bytes(b"\xef\xbb\xbf" + ZH.encode("utf-8")) == ZH
    assert decode_bytes(ZH.encode("gbk"), hint="gb2312") == ZH
    # file .txt tiếng Trung GBK được tách chương đúng
    assert split_chapters(decode_bytes(("第1章 开始\n正文\n第2章 继续\n更多").encode("gbk")))[1]["title"] == "继续"


# ---------------------------------------------------------------- bóc trang

def test_extract_toc_generic():
    html = (FIXTURES / "toc.html").read_text(encoding="utf-8")
    info = extract_toc(html, "https://novel.example.com/book/1/")
    assert info["title"] == "剑来"
    assert info["author"] == "烽火戏诸侯"
    assert "陈平安" in info["description"]
    urls = [c["url"] for c in info["chapters"]]
    assert urls == ["https://novel.example.com/book/1/1000.html", "https://novel.example.com/book/1/1001.html",
                    "https://novel.example.com/book/1/1002.html", "https://novel.example.com/book/1/1003.html"]
    assert info["chapters"][1]["title"] == "第一章 惊蛰"


def test_extract_chapter_generic_and_next_page():
    html = (FIXTURES / "chapter.html").read_text(encoding="utf-8")
    url = "https://novel.example.com/book/1/1001.html"
    d = extract_chapter(html, url)
    assert d["title"] == "第一章 惊蛰"
    paras = d["text"].split("\n\n")
    assert paras[0].startswith("二月二，龙抬头")
    assert len(paras) == 3
    assert "本站" not in d["text"] and "www." not in d["text"] and "请收藏" not in d["text"]
    assert d["next_page"] == "https://novel.example.com/book/1/1001_2.html"
    assert same_chapter(url, d["next_page"]) and not same_chapter(url, "https://novel.example.com/book/1/1002.html")

    pages = {url: html, d["next_page"]: (FIXTURES / "chapter_p2.html").read_text(encoding="utf-8")}

    class FakeFetcher:
        def __init__(self):
            self.calls = []

        def get(self, u, referer=None):
            self.calls.append(u)
            return pages[u]

    f = FakeFetcher()
    full = fetch_chapter(f, url)
    assert f.calls == [url, d["next_page"]]
    parts = full["text"].split("\n\n")
    assert len(parts) == 5 and parts[-1].startswith("巷子尽头")
    assert "本章未完" not in full["text"]

    toc_pages = {"https://novel.example.com/book/1/": (FIXTURES / "toc.html").read_text(encoding="utf-8")}
    f2 = FakeFetcher()
    pages.update(toc_pages)
    assert len(fetch_toc(f2, "https://novel.example.com/book/1/")["chapters"]) == 4


def test_site_rule_overrides_generic(monkeypatch):
    from app.importer import extract
    monkeypatch.setitem(extract.SITE_RULES, "rule.example", {"content": ".real", "title": ".t"})
    html = ('<html><body><div class="t">第五章 规则</div><div class="real">规则内容第一段。<br>规则内容第二段。</div>'
            '<div class="big">' + "很多很多干扰文字。" * 50 + "</div></body></html>")
    d = extract.extract_chapter(html, "https://www.rule.example/5.html")
    assert d["title"] == "第五章 规则" and d["text"] == "规则内容第一段。\n\n规则内容第二段。"


# ---------------------------------------------------------------- prompt

def test_glossary_prompt_building():
    terms = [{"zh": "陈平安", "vi": "Trần Bình An", "kind": "nhân vật"},
             {"zh": "泥瓶巷", "vi": "ngõ Nê Bình", "kind": "địa danh"}]
    block = prompts.glossary_block(terms)
    assert block.startswith("BẢNG THUẬT NGỮ")
    assert "陈平安 → Trần Bình An (nhân vật)" in block
    # thứ tự cố định (để cache prompt) không phụ thuộc thứ tự đầu vào
    assert prompts.glossary_block(list(reversed(terms))) == block
    assert prompts.glossary_block([]) == "BẢNG THUẬT NGỮ: (chưa có)"
    p = prompts.build_glossary_prompt("剑来", [("第一章", "甲" * 9000), ("第二章", "乙" * 9000), ("第三章", "丙" * 100)],
                                      max_chars=12000)
    assert "Truyện: 剑来" in p and p.count("甲") == 9000 and p.count("乙") == 3000 and "丙" not in p
    cp = prompts.build_chapter_prompt(2, "少年", "正文", prev_tail="Đoạn cuối chương trước.", summary="Tóm tắt cũ.",
                                      genre="tiên hiệp")
    assert "Tóm tắt cốt truyện tới chương trước:\nTóm tắt cũ." in cp
    assert "Đoạn cuối chương trước." in cp and "NỘI DUNG GỐC:\n正文" in cp and "Thể loại: tiên hiệp" in cp
    assert prompts.CHAPTER_SCHEMA["required"] == ["title", "content", "new_terms", "summary"]


def test_cost_estimate():
    c = estimate_claude_cost(3000 * 10, 10, model="claude-sonnet-5-5")
    assert 0.3 < c < 1.5


# ---------------------------------------------------------------- ủng hộ

def test_vietqr_url():
    url = vietqr_url("970436", "0123456789", 50000, "UH 12 ban doc", "NGUYEN VAN A")
    assert url == ("https://img.vietqr.io/image/970436-0123456789-compact2.png?amount=50000"
                   "&addInfo=UH%2012%20ban%20doc&accountName=NGUYEN%20VAN%20A")
    assert vietqr_url("VCB", "1", None) == "https://img.vietqr.io/image/VCB-1-compact2.png"
    assert transfer_note(12, "Đức Anh") == "UH 12 DucAnh"
    assert transfer_note(None, "") == "UH 0 bandoc"


# ---------------------------------------------------------------- việc dịch với AI giả lập

def test_translation_job_with_mock_provider(fresh_db):
    from app.auth import create_user
    from app.db import q, q1, val
    from app.services import create_story
    from app.worker import add_text_chapters, run_job

    u = create_user("uploader", "matkhau123", role="uploader")
    s = create_story(u["id"], "剑来", origin="translated", original_title="剑来")
    add_text_chapters(s["id"], split_chapters("第一章 惊蛰\n陈平安在泥瓶巷。\n第二章 少年\n宁姚来了。\n第三章 日出\n天亮了。"))
    from app.db import ex, now
    job_id = ex("INSERT INTO jobs(story_id, owner_id, status, target_n, provider, toc_done, created_at, updated_at) "
                "VALUES (?,?,'running',2,'mock',1,?,?)", (s["id"], u["id"], now(), now())).lastrowid

    def handler(system, prompt, schema):
        props = schema["properties"]
        if "terms" in props:
            return {"genre": "tiên hiệp", "terms": [{"zh": "陈平安", "vi": "Trần Bình An", "kind": "nhân vật"}]}
        if "genres" in props:
            return {"title": "Kiếm Lai", "author": "Phong Hoả Hí Chư Hầu", "description": "Giới thiệu dịch.",
                    "genres": ["Tiên hiệp", "Không có"]}
        assert "陈平安 → Trần Bình An" in system[1]  # bảng thuật ngữ được gửi kèm
        n = int(prompt.split("Dịch chương ")[1].split()[0])
        return {"title": f"Tên {n}", "content": f"Bản dịch chương {n}.\n\nĐoạn hai.",
                "new_terms": [{"zh": "宁姚", "vi": "Ninh Diêu", "kind": "nhân vật"}] if n == 2 else [],
                "summary": f"Tóm tắt tới chương {n}."}

    provider = MockProvider(handler)
    assert run_job(job_id, provider=provider) == "done"
    st = q1("SELECT * FROM stories WHERE id=?", (s["id"],))
    assert st["title"] == "Kiếm Lai" and st["slug"] == "kiem-lai" and st["chapter_count"] == 2
    assert st["author_name"] == "Phong Hoả Hí Chư Hầu"
    assert [r["name"] for r in q("SELECT g.name FROM genres g JOIN story_genres sg ON sg.genre_id=g.id WHERE sg.story_id=?",
                                 (s["id"],))] == ["Tiên hiệp"]
    assert [r["status"] for r in q("SELECT status FROM source_chapters WHERE story_id=? ORDER BY n", (s["id"],))] == \
        ["done", "done", "pending"]
    assert val("SELECT vi FROM glossary WHERE story_id=? AND zh='宁姚'", (s["id"],)) == "Ninh Diêu"
    job = q1("SELECT * FROM jobs WHERE id=?", (job_id,))
    assert job["summary"] == "Tóm tắt tới chương 2." and job["status"] == "done"
    # chương 2 được gửi kèm đoạn cuối bản dịch chương 1 và tóm tắt
    ch2_prompt = [c["prompt"] for c in provider.calls if "Dịch chương 2" in c["prompt"]][0]
    assert "Bản dịch chương 1." in ch2_prompt or "Đoạn hai." in ch2_prompt
    assert "Tóm tắt tới chương 1." in ch2_prompt
    # "dịch tiếp" → chạy nốt chương 3
    ex("UPDATE jobs SET target_n=3, status='running' WHERE id=?", (job_id,))
    assert run_job(job_id, provider=provider) == "done"
    assert val("SELECT chapter_count FROM stories WHERE id=?", (s["id"],)) == 3
    assert json.dumps(provider.calls[0]["schema"])  # schema JSON hợp lệ


def test_translation_job_fatal_error_marks_job(fresh_db):
    from app.auth import create_user
    from app.db import ex, now, q1
    from app.services import create_story
    from app.translate.providers import ProviderError
    from app.worker import add_text_chapters, run_job

    u = create_user("up2", "matkhau123", role="uploader")
    s = create_story(u["id"], "书", origin="translated", original_title="书")
    add_text_chapters(s["id"], split_chapters("第一章 一\n甲"))
    job_id = ex("INSERT INTO jobs(story_id, owner_id, status, target_n, provider, toc_done, meta_done, glossary_done, "
                "created_at, updated_at) VALUES (?,?,'running',5,'mock',1,1,1,?,?)", (s["id"], u["id"], now(), now())).lastrowid

    def boom(*a):
        raise ProviderError("Claude API key không đúng", fatal=True)

    assert run_job(job_id, provider=MockProvider(boom)) == "error"
    j = q1("SELECT * FROM jobs WHERE id=?", (job_id,))
    assert "key không đúng" in j["error"]
    assert q1("SELECT status FROM source_chapters WHERE story_id=?", (s["id"],))["status"] == "pending"
