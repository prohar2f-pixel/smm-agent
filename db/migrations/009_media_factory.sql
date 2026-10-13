CREATE TABLE media_bibles (
    id TEXT PRIMARY KEY,
    pilot_id TEXT NOT NULL REFERENCES pilot_passports(id) ON DELETE RESTRICT,
    version INTEGER NOT NULL CHECK(version >= 1),
    content_json TEXT NOT NULL,
    content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64),
    status TEXT NOT NULL CHECK(status IN ('draft','approved','invalidated')),
    created_at TEXT NOT NULL,
    UNIQUE(pilot_id, version)
);

CREATE TABLE media_episodes (
    id TEXT PRIMARY KEY,
    bible_id TEXT NOT NULL REFERENCES media_bibles(id) ON DELETE RESTRICT,
    title TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('draft','animatic_review','animatic_approved','generating','complete','blocked')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    row_version INTEGER NOT NULL DEFAULT 1 CHECK(row_version >= 1)
);

CREATE TABLE media_scenes (
    id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL REFERENCES media_episodes(id) ON DELETE RESTRICT,
    scene_no INTEGER NOT NULL CHECK(scene_no >= 1),
    description TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(episode_id, scene_no)
);

CREATE TABLE media_shots (
    id TEXT PRIMARY KEY,
    scene_id TEXT NOT NULL REFERENCES media_scenes(id) ON DELETE RESTRICT,
    shot_no INTEGER NOT NULL CHECK(shot_no >= 1),
    aspect_ratio TEXT NOT NULL CHECK(aspect_ratio IN ('16:9','9:16')),
    prompt TEXT NOT NULL,
    reference_assets_json TEXT NOT NULL,
    animatic_sha256 TEXT NOT NULL CHECK(length(animatic_sha256)=64),
    created_at TEXT NOT NULL,
    UNIQUE(scene_id, shot_no, aspect_ratio)
);

CREATE TABLE generation_attempts (
    id TEXT PRIMARY KEY,
    shot_id TEXT NOT NULL REFERENCES media_shots(id) ON DELETE RESTRICT,
    attempt_no INTEGER NOT NULL CHECK(attempt_no >= 1),
    provider TEXT NOT NULL,
    operation_id TEXT REFERENCES external_operations(id) ON DELETE RESTRICT,
    status TEXT NOT NULL CHECK(status IN ('planned','succeeded','failed','unknown_outcome')),
    cost_minor INTEGER NOT NULL CHECK(cost_minor >= 0),
    currency TEXT NOT NULL CHECK(length(currency)=3),
    cost_evidence_ref TEXT NOT NULL,
    output_sha256 TEXT CHECK(output_sha256 IS NULL OR length(output_sha256)=64),
    created_at TEXT NOT NULL,
    UNIQUE(shot_id, attempt_no)
);
