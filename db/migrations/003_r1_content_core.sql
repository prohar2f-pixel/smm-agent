CREATE TABLE pilot_passports (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'ready', 'running', 'observing', 'completed', 'stopped')),
    criteria_version INTEGER NOT NULL DEFAULT 1 CHECK (criteria_version >= 1),
    timezone TEXT NOT NULL,
    audience TEXT,
    offer TEXT,
    qualification_rules_json TEXT NOT NULL DEFAULT '{}',
    platform TEXT,
    target_ref TEXT,
    publish_mode TEXT CHECK (publish_mode IS NULL OR publish_mode IN ('manual', 'adapter')),
    roles_json TEXT NOT NULL DEFAULT '{}',
    cta_text TEXT,
    cta_base_ref TEXT,
    target_count INTEGER NOT NULL DEFAULT 3 CHECK (target_count > 0),
    duration_min_sec INTEGER NOT NULL DEFAULT 30 CHECK (duration_min_sec > 0),
    duration_max_sec INTEGER NOT NULL DEFAULT 45 CHECK (duration_max_sec >= duration_min_sec),
    start_at TEXT,
    production_deadline_at TEXT,
    observation_end_at TEXT,
    working_calendar_json TEXT NOT NULL DEFAULT '{}',
    review_sla_sec INTEGER CHECK (review_sla_sec IS NULL OR review_sla_sec > 0),
    lead_sla_sec INTEGER CHECK (lead_sla_sec IS NULL OR lead_sla_sec > 0),
    thresholds_json TEXT NOT NULL DEFAULT '{}',
    evaluation_mode TEXT NOT NULL
        CHECK (evaluation_mode IN ('production_and_response', 'internal_efficiency')),
    labor_rates_json TEXT NOT NULL DEFAULT '{}',
    allocation_policy_json TEXT NOT NULL DEFAULT '{}',
    baseline_ref TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    row_version INTEGER NOT NULL DEFAULT 1 CHECK (row_version >= 1)
);

CREATE TABLE sources (
    id TEXT PRIMARY KEY,
    pilot_id TEXT REFERENCES pilot_passports(id) ON DELETE RESTRICT,
    kind TEXT NOT NULL CHECK (kind IN ('text', 'url', 'file', 'voice', 'build_log')),
    original_ref TEXT NOT NULL,
    author TEXT,
    source_date TEXT,
    captured_at TEXT NOT NULL,
    full_text_ref TEXT,
    content_sha256 TEXT NOT NULL CHECK (length(content_sha256) = 64),
    rights_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (rights_status IN ('pending', 'allowed', 'denied', 'revoked')),
    rights_evidence_ref TEXT,
    privacy_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (privacy_status IN ('pending', 'clear', 'redaction_required')),
    verified_by TEXT,
    verified_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    row_version INTEGER NOT NULL DEFAULT 1 CHECK (row_version >= 1)
);

CREATE INDEX idx_sources_pilot ON sources(pilot_id, captured_at);
CREATE INDEX idx_sources_hash ON sources(content_sha256);

CREATE TABLE content_projects (
    id TEXT PRIMARY KEY,
    pilot_id TEXT NOT NULL REFERENCES pilot_passports(id) ON DELETE RESTRICT,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'captured'
        CHECK (status IN (
            'captured', 'brief_ready', 'script_review', 'production_ready',
            'preview_review', 'accepted', 'publish_ready', 'published',
            'observing', 'measured', 'blocked', 'cancelled'
        )),
    current_brief_id TEXT REFERENCES content_briefs(id) ON DELETE RESTRICT,
    current_revision_id TEXT REFERENCES content_revisions(id) ON DELETE RESTRICT,
    owner_id TEXT NOT NULL,
    next_actor_id TEXT,
    blocked_reason TEXT,
    first_preview_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    row_version INTEGER NOT NULL DEFAULT 1 CHECK (row_version >= 1)
);

CREATE INDEX idx_projects_pilot_status ON content_projects(pilot_id, status);

CREATE TABLE content_project_sources (
    project_id TEXT NOT NULL REFERENCES content_projects(id) ON DELETE RESTRICT,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (project_id, source_id)
);

CREATE TABLE content_briefs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES content_projects(id) ON DELETE RESTRICT,
    brief_no INTEGER NOT NULL CHECK (brief_no >= 1),
    audience TEXT NOT NULL,
    goal TEXT NOT NULL,
    offer TEXT NOT NULL,
    angle TEXT NOT NULL,
    cta_text TEXT NOT NULL,
    cta_token TEXT,
    platform TEXT NOT NULL,
    target_ref TEXT NOT NULL,
    duration_min_sec INTEGER NOT NULL CHECK (duration_min_sec > 0),
    duration_max_sec INTEGER NOT NULL CHECK (duration_max_sec >= duration_min_sec),
    constraints_json TEXT NOT NULL DEFAULT '[]',
    prompt_version TEXT NOT NULL,
    content_sha256 TEXT NOT NULL CHECK (length(content_sha256) = 64),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (project_id, brief_no)
);

CREATE TABLE content_brief_sources (
    brief_id TEXT NOT NULL REFERENCES content_briefs(id) ON DELETE RESTRICT,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (brief_id, source_id)
);

CREATE TABLE content_revisions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES content_projects(id) ON DELETE RESTRICT,
    brief_id TEXT NOT NULL REFERENCES content_briefs(id) ON DELETE RESTRICT,
    revision_no INTEGER NOT NULL CHECK (revision_no >= 1),
    parent_revision_id TEXT REFERENCES content_revisions(id) ON DELETE RESTRICT,
    script_text TEXT NOT NULL,
    caption_text TEXT NOT NULL,
    timeline_json TEXT NOT NULL DEFAULT '[]',
    voice_asset_id TEXT,
    preview_asset_id TEXT,
    master_asset_id TEXT,
    subtitles_asset_id TEXT,
    render_profile_id TEXT,
    prompt_versions_json TEXT NOT NULL DEFAULT '{}',
    content_sha256 TEXT NOT NULL CHECK (length(content_sha256) = 64),
    package_sha256 TEXT CHECK (package_sha256 IS NULL OR length(package_sha256) = 64),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (project_id, revision_no)
);

CREATE TABLE assets (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES content_projects(id) ON DELETE RESTRICT,
    source_id TEXT REFERENCES sources(id) ON DELETE RESTRICT,
    revision_id TEXT REFERENCES content_revisions(id) ON DELETE RESTRICT,
    role TEXT NOT NULL CHECK (role IN (
        'source_screen', 'source_voice', 'preview', 'master', 'subtitles',
        'thumbnail', 'image', 'document', 'transcript'
    )),
    storage_key TEXT NOT NULL,
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
    mime_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK (size_bytes > 0),
    duration_ms INTEGER CHECK (duration_ms IS NULL OR duration_ms >= 0),
    width INTEGER CHECK (width IS NULL OR width > 0),
    height INTEGER CHECK (height IS NULL OR height > 0),
    created_by_job_id TEXT,
    rights_status TEXT NOT NULL
        CHECK (rights_status IN ('pending', 'allowed', 'denied', 'revoked')),
    privacy_status TEXT NOT NULL
        CHECK (privacy_status IN ('pending', 'clear', 'redaction_required')),
    created_at TEXT NOT NULL
);

CREATE INDEX idx_assets_project_revision ON assets(project_id, revision_id);

CREATE TABLE claims (
    id TEXT PRIMARY KEY,
    revision_id TEXT NOT NULL REFERENCES content_revisions(id) ON DELETE RESTRICT,
    text TEXT NOT NULL,
    location_json TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ('supported', 'hypothesis', 'disputed', 'outdated', 'unverified')),
    hypothesis_label_location TEXT,
    checked_by TEXT,
    checked_at TEXT,
    valid_until TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_claims_revision ON claims(revision_id, status);

CREATE TABLE claim_evidence (
    id TEXT PRIMARY KEY,
    claim_id TEXT NOT NULL REFERENCES claims(id) ON DELETE RESTRICT,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE RESTRICT,
    excerpt TEXT,
    asset_id TEXT REFERENCES assets(id) ON DELETE RESTRICT,
    start_ms INTEGER CHECK (start_ms IS NULL OR start_ms >= 0),
    end_ms INTEGER CHECK (end_ms IS NULL OR end_ms > start_ms),
    applicability_note TEXT NOT NULL,
    checked_by TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    CHECK (excerpt IS NOT NULL OR asset_id IS NOT NULL)
);

CREATE INDEX idx_evidence_claim ON claim_evidence(claim_id);

CREATE TABLE reviews (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES content_projects(id) ON DELETE RESTRICT,
    revision_id TEXT NOT NULL REFERENCES content_revisions(id) ON DELETE RESTRICT,
    stage TEXT NOT NULL CHECK (stage IN ('script', 'preview', 'publish')),
    subject_sha256 TEXT NOT NULL CHECK (length(subject_sha256) = 64),
    assigned_to TEXT NOT NULL,
    due_at TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'changes_requested', 'rejected', 'invalidated')),
    decision_by TEXT,
    decision_at TEXT,
    checklist_json TEXT NOT NULL DEFAULT '{}',
    comment TEXT,
    target_ref TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_reviews_revision_stage ON reviews(revision_id, stage, status);
