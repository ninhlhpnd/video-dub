"""SQLite (WAL): mỗi luồng một kết nối, tự nâng cấp lược đồ khi khởi động."""
import sqlite3
import threading
import time
from contextlib import contextmanager

from .config import settings

_local = threading.local()

# Mỗi phần tử là một bước nâng cấp; PRAGMA user_version ghi số bước đã chạy.
MIGRATIONS = [
    # 1: lược đồ gốc
    """
    CREATE TABLE users (
        id INTEGER PRIMARY KEY,
        username TEXT NOT NULL UNIQUE COLLATE NOCASE,
        display_name TEXT NOT NULL,
        email TEXT,
        pw_hash TEXT NOT NULL,
        pw_salt TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'reader',          -- reader | uploader | admin
        is_banned INTEGER NOT NULL DEFAULT 0,
        api_key TEXT UNIQUE,
        avatar TEXT,
        bio TEXT NOT NULL DEFAULT '',
        bank_bin TEXT NOT NULL DEFAULT '',
        bank_account TEXT NOT NULL DEFAULT '',
        bank_holder TEXT NOT NULL DEFAULT '',
        momo TEXT NOT NULL DEFAULT '',
        paypal TEXT NOT NULL DEFAULT '',
        kofi TEXT NOT NULL DEFAULT '',
        created_at INTEGER NOT NULL
    );

    CREATE TABLE genres (
        id INTEGER PRIMARY KEY,
        slug TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        sort INTEGER NOT NULL DEFAULT 100
    );

    CREATE TABLE stories (
        id INTEGER PRIMARY KEY,
        owner_id INTEGER NOT NULL REFERENCES users(id),
        source_id TEXT,
        slug TEXT NOT NULL UNIQUE,
        title TEXT NOT NULL,
        title_norm TEXT NOT NULL DEFAULT '',
        author_name TEXT NOT NULL DEFAULT '',
        author_norm TEXT NOT NULL DEFAULT '',
        original_title TEXT NOT NULL DEFAULT '',
        original_author TEXT NOT NULL DEFAULT '',
        description TEXT NOT NULL DEFAULT '',
        cover TEXT,
        status TEXT NOT NULL DEFAULT 'ongoing',        -- ongoing | completed
        origin TEXT NOT NULL DEFAULT 'original',       -- original | translated | ai
        is_hidden INTEGER NOT NULL DEFAULT 0,
        is_featured INTEGER NOT NULL DEFAULT 0,
        chapter_count INTEGER NOT NULL DEFAULT 0,      -- số chương đang hiển thị
        last_chapter_n INTEGER NOT NULL DEFAULT 0,
        last_chapter_title TEXT NOT NULL DEFAULT '',
        last_chapter_at INTEGER NOT NULL DEFAULT 0,
        views INTEGER NOT NULL DEFAULT 0,
        follows INTEGER NOT NULL DEFAULT 0,
        rating_sum INTEGER NOT NULL DEFAULT 0,
        rating_count INTEGER NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        UNIQUE (owner_id, source_id)
    );
    CREATE INDEX ix_stories_latest ON stories(is_hidden, last_chapter_at DESC);
    CREATE INDEX ix_stories_views ON stories(is_hidden, views DESC);
    CREATE INDEX ix_stories_chapters ON stories(is_hidden, chapter_count DESC);
    CREATE INDEX ix_stories_status ON stories(status, last_chapter_at DESC);
    CREATE INDEX ix_stories_origin ON stories(origin, last_chapter_at DESC);
    CREATE INDEX ix_stories_owner ON stories(owner_id, updated_at DESC);
    CREATE INDEX ix_stories_featured ON stories(is_featured, views DESC);

    CREATE TABLE story_genres (
        story_id INTEGER NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
        genre_id INTEGER NOT NULL REFERENCES genres(id) ON DELETE CASCADE,
        PRIMARY KEY (story_id, genre_id)
    );
    CREATE INDEX ix_story_genres_genre ON story_genres(genre_id, story_id);

    CREATE TABLE chapters (
        id INTEGER PRIMARY KEY,
        story_id INTEGER NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
        n INTEGER NOT NULL,
        title TEXT NOT NULL DEFAULT '',
        content TEXT NOT NULL DEFAULT '',
        words INTEGER NOT NULL DEFAULT 0,
        is_published INTEGER NOT NULL DEFAULT 1,
        publish_at INTEGER NOT NULL DEFAULT 0,         -- hiện từ thời điểm này (hẹn giờ đăng)
        views INTEGER NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        UNIQUE (story_id, n)
    );
    CREATE INDEX ix_chapters_visible ON chapters(story_id, is_published, publish_at);
    CREATE INDEX ix_chapters_schedule ON chapters(is_published, publish_at);

    CREATE TABLE view_log (
        key TEXT PRIMARY KEY,
        day TEXT NOT NULL
    ) WITHOUT ROWID;
    CREATE INDEX ix_view_log_day ON view_log(day);

    CREATE TABLE story_views_daily (
        story_id INTEGER NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
        day TEXT NOT NULL,
        views INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (story_id, day)
    );
    CREATE INDEX ix_svd_day ON story_views_daily(day, story_id);

    CREATE TABLE follows (
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        story_id INTEGER NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
        last_seen_n INTEGER NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL,
        PRIMARY KEY (user_id, story_id)
    );
    CREATE INDEX ix_follows_story ON follows(story_id);

    CREATE TABLE ratings (
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        story_id INTEGER NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
        stars INTEGER NOT NULL,
        created_at INTEGER NOT NULL,
        PRIMARY KEY (user_id, story_id)
    );

    CREATE TABLE reading_history (
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        story_id INTEGER NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
        chapter_n INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        PRIMARY KEY (user_id, story_id)
    );
    CREATE INDEX ix_history_user ON reading_history(user_id, updated_at DESC);

    CREATE TABLE comments (
        id INTEGER PRIMARY KEY,
        story_id INTEGER NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
        chapter_id INTEGER REFERENCES chapters(id) ON DELETE CASCADE,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        body TEXT NOT NULL,
        is_deleted INTEGER NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL
    );
    CREATE INDEX ix_comments_story ON comments(story_id, chapter_id, created_at DESC);
    CREATE INDEX ix_comments_user ON comments(user_id, created_at DESC);

    CREATE TABLE donations (
        id INTEGER PRIMARY KEY,
        story_id INTEGER REFERENCES stories(id) ON DELETE SET NULL,
        to_user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,  -- NULL = ủng hộ trang
        from_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        donor_name TEXT NOT NULL,
        amount INTEGER NOT NULL,
        message TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'pending',        -- pending | confirmed | rejected
        source TEXT NOT NULL DEFAULT 'claim',          -- claim | webhook
        ref TEXT,
        ip TEXT NOT NULL DEFAULT '',
        created_at INTEGER NOT NULL,
        confirmed_at INTEGER
    );
    CREATE INDEX ix_donations_to ON donations(to_user_id, status, created_at DESC);
    CREATE INDEX ix_donations_story ON donations(story_id, status);
    CREATE UNIQUE INDEX ix_donations_ref ON donations(ref) WHERE ref IS NOT NULL;

    CREATE TABLE site_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );

    CREATE TABLE jobs (
        id INTEGER PRIMARY KEY,
        story_id INTEGER NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
        owner_id INTEGER NOT NULL REFERENCES users(id),
        status TEXT NOT NULL DEFAULT 'queued',         -- queued | running | paused | done | error
        source_url TEXT,
        target_n INTEGER NOT NULL DEFAULT 0,           -- dịch tới chương số này thì dừng
        provider TEXT NOT NULL DEFAULT 'gemini',
        toc_done INTEGER NOT NULL DEFAULT 0,
        meta_done INTEGER NOT NULL DEFAULT 0,
        glossary_done INTEGER NOT NULL DEFAULT 0,
        summary TEXT NOT NULL DEFAULT '',
        genre_hint TEXT NOT NULL DEFAULT '',
        cost_usd REAL NOT NULL DEFAULT 0,
        tokens_in INTEGER NOT NULL DEFAULT 0,
        tokens_out INTEGER NOT NULL DEFAULT 0,
        message TEXT NOT NULL DEFAULT '',
        error TEXT NOT NULL DEFAULT '',
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    );
    CREATE INDEX ix_jobs_status ON jobs(status, updated_at);
    CREATE INDEX ix_jobs_story ON jobs(story_id);

    CREATE TABLE source_chapters (
        id INTEGER PRIMARY KEY,
        story_id INTEGER NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
        n INTEGER NOT NULL,
        url TEXT,
        title_zh TEXT NOT NULL DEFAULT '',
        content_zh TEXT,                                -- NULL = chưa tải bản gốc
        status TEXT NOT NULL DEFAULT 'pending',        -- pending | translating | done | error
        error TEXT NOT NULL DEFAULT '',
        chapter_id INTEGER REFERENCES chapters(id) ON DELETE SET NULL,
        updated_at INTEGER NOT NULL DEFAULT 0,
        UNIQUE (story_id, n)
    );
    CREATE INDEX ix_source_status ON source_chapters(story_id, status, n);

    CREATE TABLE glossary (
        id INTEGER PRIMARY KEY,
        story_id INTEGER NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
        zh TEXT NOT NULL,
        vi TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT '',
        locked INTEGER NOT NULL DEFAULT 0,             -- người đăng đã sửa tay: AI không ghi đè
        created_at INTEGER NOT NULL,
        UNIQUE (story_id, zh)
    );
    """,
]


def now() -> int:
    return int(time.time())


def connect(path=None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path or settings.db_path), timeout=30, isolation_level=None,
                           check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def get_db() -> sqlite3.Connection:
    """Kết nối dùng lại trong cùng luồng (đổi DATA_DIR khi test thì tự mở lại)."""
    path = str(settings.db_path)
    conn = getattr(_local, "conn", None)
    if conn is None or getattr(_local, "path", None) != path:
        conn = connect(path)
        _local.conn, _local.path = conn, path
    return conn


@contextmanager
def tx(conn=None):
    """Giao dịch ghi nguyên khối."""
    conn = conn or get_db()
    if conn.in_transaction:
        yield conn
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def q(sql, params=(), conn=None):
    return (conn or get_db()).execute(sql, params).fetchall()


def q1(sql, params=(), conn=None):
    return (conn or get_db()).execute(sql, params).fetchone()


def val(sql, params=(), conn=None, default=None):
    row = (conn or get_db()).execute(sql, params).fetchone()
    return row[0] if row and row[0] is not None else default


def ex(sql, params=(), conn=None):
    return (conn or get_db()).execute(sql, params)


def migrate():
    settings.ensure()
    conn = get_db()
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for i, script in enumerate(MIGRATIONS[version:], start=version + 1):
        conn.executescript("BEGIN;" + script + f";PRAGMA user_version={i};COMMIT;")
    seed_genres(conn)


DEFAULT_GENRES = [
    "Tiên hiệp", "Huyền huyễn", "Kiếm hiệp", "Đô thị", "Ngôn tình", "Xuyên không", "Trọng sinh",
    "Hệ thống", "Khoa huyễn", "Võng du", "Lịch sử", "Quân sự", "Đồng nhân", "Linh dị", "Trinh thám",
    "Điền văn", "Cung đấu", "Mạt thế", "Hài hước", "Đam mỹ", "Bách hợp", "Truyện ngắn",
]


def seed_genres(conn=None):
    from .utils import slugify
    conn = conn or get_db()
    if conn.execute("SELECT COUNT(*) FROM genres").fetchone()[0]:
        return
    with tx(conn):
        for i, name in enumerate(DEFAULT_GENRES):
            conn.execute("INSERT OR IGNORE INTO genres(slug, name, sort) VALUES (?,?,?)", (slugify(name), name, i))


def get_setting(key, default=""):
    v = val("SELECT value FROM site_settings WHERE key=?", (key,))
    return default if v is None else v


def set_setting(key, value):
    ex("INSERT INTO site_settings(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
       (key, value))
