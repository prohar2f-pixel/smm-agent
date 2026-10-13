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
