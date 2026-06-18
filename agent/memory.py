import aiosqlite
import json
import os
from datetime import datetime

DB_PATH = os.getenv("DB_PATH", "./data/agent.db")


async def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS brand_voice (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT,
                post_type TEXT,
                content_telegram TEXT,
                content_vk TEXT,
                status TEXT DEFAULT 'draft',
                scheduled_at DATETIME,
                published_at DATETIME,
                telegram_message_id INTEGER,
                vk_post_id INTEGER,
                reaction TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS topics_used (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT,
                angle TEXT,
                used_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS competitor_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT,
                source_type TEXT,
                content_summary TEXT,
                signal_type TEXT DEFAULT 'topic',
                collected_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
        """)
        await db.commit()


# ── brand_voice ──────────────────────────────────────────────────────────────

async def get_voice(key: str) -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM brand_voice WHERE key=?", (key,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


async def set_voice(key: str, value: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO brand_voice(key, value, updated_at) VALUES(?,?,?)",
            (key, value, datetime.utcnow().isoformat())
        )
        await db.commit()


async def get_all_voice() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT key, value FROM brand_voice") as cur:
            rows = await cur.fetchall()
            return {r[0]: r[1] for r in rows}


# ── settings ─────────────────────────────────────────────────────────────────

async def get_setting(key: str, default: str = None) -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM settings WHERE key=?", (key,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else default


async def set_setting(key: str, value: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings(key, value) VALUES(?,?)", (key, value)
        )
        await db.commit()


async def get_setting_json(key: str, default=None):
    val = await get_setting(key)
    if val is None:
        return default
    return json.loads(val)


async def set_setting_json(key: str, value):
    await set_setting(key, json.dumps(value, ensure_ascii=False))


# ── posts ─────────────────────────────────────────────────────────────────────

async def create_post(topic: str, post_type: str, content_telegram: str,
                      content_vk: str, scheduled_at: datetime = None) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO posts(topic, post_type, content_telegram, content_vk,
               status, scheduled_at) VALUES(?,?,?,?,?,?)""",
            (topic, post_type, content_telegram, content_vk, "pending",
             scheduled_at.isoformat() if scheduled_at else None)
        )
        await db.commit()
        return cur.lastrowid


async def get_post(post_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM posts WHERE id=?", (post_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def update_post_status(post_id: int, status: str, **kwargs):
    fields = {"status": status, **kwargs}
    set_clause = ", ".join(f"{k}=?" for k in fields)
    values = list(fields.values()) + [post_id]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE posts SET {set_clause} WHERE id=?", values)
        await db.commit()


async def get_pending_posts() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM posts WHERE status='pending' ORDER BY created_at"
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def get_approved_posts() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM posts WHERE status='approved' ORDER BY scheduled_at"
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


# ── topics_used ───────────────────────────────────────────────────────────────

async def add_topic_used(topic: str, angle: str = ""):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO topics_used(topic, angle) VALUES(?,?)", (topic, angle)
        )
        await db.commit()


async def get_recent_topics(limit: int = 60) -> list[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT topic FROM topics_used ORDER BY used_at DESC LIMIT ?", (limit,)
        ) as cur:
            rows = await cur.fetchall()
            return [r[0] for r in rows]


# ── competitor_signals ────────────────────────────────────────────────────────

async def add_signal(source: str, source_type: str, summary: str, signal_type: str = "topic"):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO competitor_signals(source, source_type, content_summary, signal_type)
               VALUES(?,?,?,?)""",
            (source, source_type, summary, signal_type)
        )
        await db.commit()


async def get_due_posts() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM posts
               WHERE status='postponed' AND scheduled_at IS NOT NULL
               AND scheduled_at <= datetime('now')
               ORDER BY scheduled_at"""
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def get_recent_signals(limit: int = 20) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM competitor_signals
               ORDER BY collected_at DESC LIMIT ?""", (limit,)
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]
