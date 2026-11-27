"""Hàng đợi dịch truyện chạy nền.

Mặc định chạy thành một luồng trong tiến trình web (WORKER=1). Muốn tách riêng: đặt WORKER=0 cho web
rồi chạy `python -m app.worker`. Trạng thái nằm hết trong DB (jobs, source_chapters) nên tắt/mở lại
lúc nào cũng được: việc đang chạy dở được đưa lại vào hàng đợi, chương đang dịch dở được dịch lại.
Chỉ chạy MỘT worker cho mỗi file DB.
"""
import logging
import threading
import time
import traceback

from .db import ex, migrate, now, q, q1, tx, val
from .importer.extract import fetch_chapter, fetch_toc
from .importer.fetch import Fetcher, FetchError
from .services import genre_ids_from_names, refresh_story_stats, set_story_genres, update_story, upsert_chapter
from .translate import pipeline
from .translate.providers import ProviderError, get_provider
from .utils import slugify, unique_slug

log = logging.getLogger("worker")


def job_update(job_id, **fields):
    fields["updated_at"] = now()
    cols = ", ".join(f"{k}=?" for k in fields)
    ex(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))


def job_add_usage(job_id, res):
    ex("UPDATE jobs SET tokens_in=tokens_in+?, tokens_out=tokens_out+?, cost_usd=cost_usd+?, updated_at=? WHERE id=?",
       (res.tokens_in, res.tokens_out, res.cost, now(), job_id))


def recover():
    """Khởi động lại sau khi tắt đột ngột."""
    ex("UPDATE jobs SET status='queued' WHERE status='running'")
    ex("UPDATE source_chapters SET status='pending' WHERE status='translating'")


def claim_job():
    row = q1("UPDATE jobs SET status='running', updated_at=? WHERE id=("
             "SELECT id FROM jobs WHERE status='queued' ORDER BY updated_at LIMIT 1) AND status='queued' RETURNING id",
             (now(),))
    return row["id"] if row else None


def _ensure_content(fetcher_box, sc):
    """Tải bản gốc của chương nếu chưa có. fetcher_box: list 1 phần tử để tạo Fetcher khi cần."""
    if sc["content_zh"]:
        return sc["content_zh"]
    if not sc["url"]:
        raise FetchError("Chương không có nội dung gốc")
    if fetcher_box[0] is None:
        fetcher_box[0] = Fetcher()
    data = fetch_chapter(fetcher_box[0], sc["url"])
    text = data["text"].strip()
    if not text:
        raise FetchError("Không bóc được nội dung chương (trang có thể chặn hoặc cần luật riêng)")
    ex("UPDATE source_chapters SET content_zh=?, title_zh=CASE WHEN title_zh='' THEN ? ELSE title_zh END, updated_at=? "
       "WHERE id=?", (text, data["title"], now(), sc["id"]))
    return text


def run_job(job_id, provider=None, fetcher=None, max_chapters=None):
    """Chạy một việc dịch tới khi xong / tạm dừng / lỗi. Trả trạng thái cuối."""
    job = q1("SELECT * FROM jobs WHERE id=?", (job_id,))
    if not job:
        return "missing"
    story_id = job["story_id"]
    fetcher_box = [fetcher]
    try:
        if provider is None:
            provider = get_provider(job["provider"])
    except ProviderError as e:
        job_update(job_id, status="error", error=str(e))
        return "error"
    try:
        # 1. mục lục
        if job["source_url"] and not job["toc_done"]:
            job_update(job_id, message="Đang tải mục lục…")
            if fetcher_box[0] is None:
                fetcher_box[0] = Fetcher()
            info = fetch_toc(fetcher_box[0], job["source_url"])
            if not info["chapters"]:
                raise FetchError("Không tìm thấy danh sách chương ở link này")
            add_toc_chapters(story_id, info["chapters"])
            st = q1("SELECT * FROM stories WHERE id=?", (story_id,))
            fields = {}
            if info["title"] and not st["original_title"]:
                fields["original_title"] = info["title"][:200]
            if info["author"] and not st["original_author"]:
                fields["original_author"] = info["author"][:100]
            if info["description"] and not st["description"]:
                fields["description"] = info["description"][:5000]
            if fields:
                update_story(story_id, **fields)
            job_update(job_id, toc_done=1, genre_hint=info.get("category", "")[:60],
                       message=f"Mục lục có {len(info['chapters'])} chương")
        job = q1("SELECT * FROM jobs WHERE id=?", (job_id,))

        # 2. thông tin truyện
        if not job["meta_done"]:
            job_update(job_id, message="Đang dịch tên truyện và giới thiệu…")
            st = q1("SELECT * FROM stories WHERE id=?", (story_id,))
            first = q1("SELECT * FROM source_chapters WHERE story_id=? ORDER BY n LIMIT 1", (story_id,))
            sample = ""
            if first and not st["description"]:
                try:
                    sample = _ensure_content(fetcher_box, first)
                except FetchError:
                    pass
            site_genres = [r["name"] for r in q("SELECT name FROM genres ORDER BY sort")]
            data, res = pipeline.translate_meta(provider, st["original_title"] or st["title"], st["original_author"],
                                                st["description"], job["genre_hint"], site_genres, sample)
            job_add_usage(job_id, res)
            fields = {}
            title_vi = str(data.get("title", "")).strip()[:200]
            if title_vi and st["title"] in ("", st["original_title"]):
                fields["title"] = title_vi
            if data.get("author"):
                fields["author_name"] = str(data["author"]).strip()[:100]
            if data.get("description"):
                fields["description"] = str(data["description"]).strip()[:5000]
            with tx():
                if fields:
                    update_story(story_id, **fields)
                if "title" in fields and not st["chapter_count"]:
                    slug = unique_slug(slugify(fields["title"]), lambda s: q1(
                        "SELECT 1 FROM stories WHERE slug=? AND id<>?", (s, story_id)))
                    ex("UPDATE stories SET slug=? WHERE id=?", (slug, story_id))
                gids = genre_ids_from_names(data.get("genres") or [], create=False)
                if gids and not val("SELECT COUNT(*) FROM story_genres WHERE story_id=?", (story_id,)):
                    set_story_genres(story_id, gids)
            job_update(job_id, meta_done=1)

        # 3. bảng thuật ngữ từ vài chương đầu
        job = q1("SELECT * FROM jobs WHERE id=?", (job_id,))
        if not job["glossary_done"]:
            job_update(job_id, message="Đang lập bảng thuật ngữ (tên nhân vật, địa danh, công pháp…)…")
            firsts = q("SELECT * FROM source_chapters WHERE story_id=? ORDER BY n LIMIT 3", (story_id,))
            samples = []
            for sc in firsts:
                try:
                    samples.append((sc["title_zh"], _ensure_content(fetcher_box, sc)))
                except FetchError:
                    continue
            st = q1("SELECT * FROM stories WHERE id=?", (story_id,))
            if samples:
                genre, terms, res = pipeline.build_glossary(provider, st["original_title"] or st["title"], samples)
                job_add_usage(job_id, res)
                pipeline.merge_terms(story_id, terms)
                job_update(job_id, glossary_done=1, genre_hint=genre or job["genre_hint"])
            else:
                job_update(job_id, glossary_done=1)

        # 4. dịch từng chương
        errors_in_row = 0
        done_now = 0
        while True:
            status = val("SELECT status FROM jobs WHERE id=?", (job_id,))
            if status != "running":
                return status
            job = q1("SELECT * FROM jobs WHERE id=?", (job_id,))
            sc = q1("SELECT * FROM source_chapters WHERE story_id=? AND status='pending' AND n<=? ORDER BY n LIMIT 1",
                    (story_id, job["target_n"]))
            if not sc or (max_chapters is not None and done_now >= max_chapters):
                left = val("SELECT COUNT(*) FROM source_chapters WHERE story_id=? AND status<>'done'", (story_id,))
                msg = "Đã dịch xong toàn bộ chương" if not left else \
                    f"Đã dịch tới chương {job['target_n']}. Bấm “Dịch tiếp” để dịch thêm."
                ex("UPDATE jobs SET status='done', message=?, updated_at=? WHERE id=? AND status='running'",
                   (msg, now(), job_id))
                return "done"
            ex("UPDATE source_chapters SET status='translating', updated_at=? WHERE id=?", (now(), sc["id"]))
            job_update(job_id, message=f"Đang dịch chương {sc['n']}…")
            try:
                content_zh = _ensure_content(fetcher_box, sc)
                prev = q1("SELECT content FROM chapters WHERE story_id=? AND n<? ORDER BY n DESC LIMIT 1",
                          (story_id, sc["n"]))
                glossary = pipeline.load_glossary(story_id)
                out, res = pipeline.translate_chapter(
                    provider, sc["n"], pipeline.chapter_title_zh(sc["title_zh"]), content_zh, glossary,
                    pipeline.tail_paragraphs(prev["content"]) if prev else "", job["summary"], job["genre_hint"])
                with tx():
                    cid = upsert_chapter(story_id, sc["n"], out["title"], out["content"], publish=True)
                    ex("UPDATE source_chapters SET status='done', error='', chapter_id=?, updated_at=? WHERE id=?",
                       (cid, now(), sc["id"]))
                    pipeline.merge_terms(story_id, out["new_terms"])
                    ex("UPDATE jobs SET summary=?, error='' WHERE id=?", (out["summary"] or job["summary"], job_id))
                job_add_usage(job_id, res)
                errors_in_row = 0
                done_now += 1
            except ProviderError as e:
                if e.fatal:
                    ex("UPDATE source_chapters SET status='pending' WHERE id=?", (sc["id"],))
                    raise
                errors_in_row += 1
                ex("UPDATE source_chapters SET status='error', error=?, updated_at=? WHERE id=?",
                   (str(e)[:500], now(), sc["id"]))
            except FetchError as e:
                errors_in_row += 1
                ex("UPDATE source_chapters SET status='error', error=?, updated_at=? WHERE id=?",
                   (str(e)[:500], now(), sc["id"]))
            if errors_in_row >= 3:
                job_update(job_id, status="error", error="Lỗi 3 chương liên tiếp, đã dừng. Xem lỗi từng chương bên dưới.")
                return "error"
    except (ProviderError, FetchError) as e:
        job_update(job_id, status="error", error=str(e)[:1000])
        return "error"
    except Exception as e:
        log.error("Job %s lỗi: %s", job_id, traceback.format_exc())
        job_update(job_id, status="error", error=f"Lỗi không mong muốn: {e}"[:1000])
        return "error"
    finally:
        if fetcher_box[0] is not None and fetcher is None:
            fetcher_box[0].close()
        with tx():
            refresh_story_stats(story_id)


def add_toc_chapters(story_id, chapters):
    """Thêm chương từ mục lục; link đã có thì bỏ qua (dùng cả khi cập nhật chương mới)."""
    known = {r["url"] for r in q("SELECT url FROM source_chapters WHERE story_id=?", (story_id,))}
    n = val("SELECT MAX(n) FROM source_chapters WHERE story_id=?", (story_id,), default=0)
    added = 0
    with tx():
        for c in chapters:
            if c["url"] in known:
                continue
            n += 1
            ex("INSERT INTO source_chapters(story_id, n, url, title_zh, updated_at) VALUES (?,?,?,?,?)",
               (story_id, n, c["url"], c["title"][:200], now()))
            added += 1
    return added


def add_text_chapters(story_id, chapters):
    """Chương tách từ file .txt tiếng Trung."""
    with tx():
        for c in chapters:
            ex("INSERT INTO source_chapters(story_id, n, title_zh, content_zh, updated_at) VALUES (?,?,?,?,?) "
               "ON CONFLICT(story_id, n) DO UPDATE SET title_zh=excluded.title_zh, content_zh=excluded.content_zh",
               (story_id, c["n"], c["title"][:200], c["content"], now()))


# ---------------------------------------------------------------- luồng nền

class Worker(threading.Thread):
    def __init__(self):
        super().__init__(name="translate-worker", daemon=True)
        self.stop_event = threading.Event()
        self._last_tick = 0

    def tick(self):
        from .services import prune_view_log, publish_due_chapters
        if time.time() - self._last_tick < 60:
            return
        self._last_tick = time.time()
        publish_due_chapters()
        prune_view_log()

    def run(self):
        try:
            recover()
        except Exception:
            log.exception("recover")
        while not self.stop_event.is_set():
            try:
                self.tick()
                job_id = claim_job()
                if job_id:
                    log.info("Bắt đầu việc dịch #%s", job_id)
                    run_job(job_id)
                    continue
            except Exception:
                log.exception("worker")
            self.stop_event.wait(3)

    def stop(self):
        self.stop_event.set()


_worker = None


def start_background():
    global _worker
    if _worker is None or not _worker.is_alive():
        _worker = Worker()
        _worker.start()
    return _worker


def stop_background():
    if _worker is not None:
        _worker.stop()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    migrate()
    w = Worker()
    print("Worker dịch truyện đang chạy (Ctrl+C để dừng)…")
    try:
        w.run()
    except KeyboardInterrupt:
        pass
