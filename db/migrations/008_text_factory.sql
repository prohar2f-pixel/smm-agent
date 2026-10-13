ALTER TABLE idea_cards ADD COLUMN scoring_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE idea_cards ADD COLUMN scoring_version TEXT;

CREATE TABLE campaign_slots (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE RESTRICT,
    publish_day TEXT NOT NULL,
    slot_no INTEGER NOT NULL CHECK(slot_no >= 1),
    idea_id TEXT NOT NULL REFERENCES idea_cards(id) ON DELETE RESTRICT,
    platform TEXT NOT NULL CHECK(platform IN ('telegram','vk')),
    created_at TEXT NOT NULL,
    UNIQUE(campaign_id, publish_day, slot_no),
    UNIQUE(campaign_id, idea_id, platform)
);

CREATE TABLE text_outputs (
    id TEXT PRIMARY KEY,
    idea_id TEXT NOT NULL REFERENCES idea_cards(id) ON DELETE RESTRICT,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE RESTRICT,
    prompt_run_id TEXT REFERENCES prompt_runs(id) ON DELETE RESTRICT,
    master_text TEXT NOT NULL,
    facts_json TEXT NOT NULL,
    hypotheses_json TEXT NOT NULL,
    source_trace_json TEXT NOT NULL,
    content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE text_variants (
    id TEXT PRIMARY KEY,
    output_id TEXT NOT NULL REFERENCES text_outputs(id) ON DELETE RESTRICT,
    platform TEXT NOT NULL CHECK(platform IN ('telegram','vk')),
    text TEXT NOT NULL,
    content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64),
    created_at TEXT NOT NULL,
    UNIQUE(output_id, platform)
);
