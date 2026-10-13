CREATE TABLE worker_reports (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE RESTRICT,
    fence_token INTEGER NOT NULL CHECK(fence_token >= 1),
    schema_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('succeeded','failed')),
    input_sha256 TEXT NOT NULL CHECK(length(input_sha256)=64),
    output_asset_id TEXT REFERENCES assets(id) ON DELETE RESTRICT,
    output_path TEXT,
    output_sha256 TEXT CHECK(output_sha256 IS NULL OR length(output_sha256)=64),
    duration_ms INTEGER NOT NULL CHECK(duration_ms >= 0),
    render_profile TEXT,
    machine_qa_json TEXT NOT NULL DEFAULT '{}',
    error_code TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(job_id, fence_token)
);

CREATE TABLE qa_results (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES content_projects(id) ON DELETE RESTRICT,
    revision_id TEXT NOT NULL REFERENCES content_revisions(id) ON DELETE RESTRICT,
    asset_id TEXT REFERENCES assets(id) ON DELETE RESTRICT,
    stage TEXT NOT NULL CHECK(stage IN ('technical','preview','final','animatic')),
    check_code TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pass','fail','not_applicable')),
    details_json TEXT NOT NULL DEFAULT '{}',
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_qa_results_subject ON qa_results(project_id, revision_id, stage, check_code);

CREATE TABLE approval_decisions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES content_projects(id) ON DELETE RESTRICT,
    revision_id TEXT NOT NULL REFERENCES content_revisions(id) ON DELETE RESTRICT,
    stage TEXT NOT NULL CHECK(stage IN ('script','preview','final','publish','animatic')),
    subject_sha256 TEXT NOT NULL CHECK(length(subject_sha256)=64),
    target_ref TEXT,
    status TEXT NOT NULL CHECK(status IN ('approved','changes_requested','rejected','invalidated')),
    actor_id TEXT NOT NULL,
    actor_role TEXT NOT NULL,
    checklist_version TEXT NOT NULL,
    reason TEXT,
    expires_at TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_approval_subject ON approval_decisions(project_id, revision_id, stage, created_at);

CREATE TABLE publish_packages (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES content_projects(id) ON DELETE RESTRICT,
    revision_id TEXT NOT NULL REFERENCES content_revisions(id) ON DELETE RESTRICT,
    package_sha256 TEXT NOT NULL CHECK(length(package_sha256)=64),
    caption_text TEXT NOT NULL,
    assets_json TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(project_id, revision_id, package_sha256)
);

CREATE TABLE publish_jobs (
    id TEXT PRIMARY KEY,
    package_id TEXT NOT NULL REFERENCES publish_packages(id) ON DELETE RESTRICT,
    platform TEXT NOT NULL CHECK(platform IN ('telegram','vk','manual')),
    target_ref TEXT NOT NULL,
    approval_id TEXT REFERENCES approval_decisions(id) ON DELETE RESTRICT,
    operation_id TEXT REFERENCES external_operations(id) ON DELETE RESTRICT,
    status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN (
        'draft','approved','queued','sending','published','failed',
        'unknown_outcome','awaiting_manual','cancelled'
    )),
    external_id TEXT,
    result_sha256 TEXT CHECK(result_sha256 IS NULL OR length(result_sha256)=64),
    error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    row_version INTEGER NOT NULL DEFAULT 1 CHECK(row_version >= 1),
    UNIQUE(package_id, platform, target_ref)
);

CREATE INDEX idx_publish_jobs_status ON publish_jobs(status, platform, created_at);

CREATE TABLE publish_attempts (
    id TEXT PRIMARY KEY,
    publish_job_id TEXT NOT NULL REFERENCES publish_jobs(id) ON DELETE RESTRICT,
    operation_id TEXT REFERENCES external_operations(id) ON DELETE RESTRICT,
    attempt_no INTEGER NOT NULL CHECK(attempt_no >= 1),
    idempotency_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK(status IN ('sent','failed','unknown_outcome','reconciled','manual_confirmed')),
    request_sha256 TEXT NOT NULL CHECK(length(request_sha256)=64),
    result_sha256 TEXT CHECK(result_sha256 IS NULL OR length(result_sha256)=64),
    external_id TEXT,
    error_code TEXT,
    evidence_ref TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(publish_job_id, attempt_no)
);

CREATE TABLE time_entries (
    id TEXT PRIMARY KEY,
    pilot_id TEXT NOT NULL REFERENCES pilot_passports(id) ON DELETE RESTRICT,
    project_id TEXT REFERENCES content_projects(id) ON DELETE RESTRICT,
    actor_id TEXT NOT NULL,
    role TEXT NOT NULL,
    stage TEXT NOT NULL,
    started_at TEXT NOT NULL,
    stopped_at TEXT NOT NULL,
    active_sec INTEGER NOT NULL CHECK(active_sec >= 0),
    rate_minor_per_hour INTEGER CHECK(rate_minor_per_hour IS NULL OR rate_minor_per_hour >= 0),
    currency TEXT CHECK(currency IS NULL OR length(currency)=3),
    created_at TEXT NOT NULL
);

CREATE TABLE baselines (
    id TEXT PRIMARY KEY,
    pilot_id TEXT NOT NULL REFERENCES pilot_passports(id) ON DELETE RESTRICT,
    kind TEXT NOT NULL,
    comparable_format TEXT NOT NULL,
    duration_sec INTEGER CHECK(duration_sec IS NULL OR duration_sec >= 0),
    quality_score REAL,
    evidence_ref TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE metric_snapshots (
    id TEXT PRIMARY KEY,
    publish_job_id TEXT NOT NULL REFERENCES publish_jobs(id) ON DELETE RESTRICT,
    window TEXT NOT NULL CHECK(window IN ('24h','72h','7d','manual')),
    metric_name TEXT NOT NULL,
    value_numeric REAL,
    is_measured INTEGER NOT NULL CHECK(is_measured IN (0,1)),
    definition TEXT NOT NULL,
    source TEXT NOT NULL,
    evidence_ref TEXT,
    captured_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    CHECK((is_measured=0 AND value_numeric IS NULL) OR is_measured=1),
    UNIQUE(publish_job_id, window, metric_name)
);

CREATE TABLE leads (
    id TEXT PRIMARY KEY,
    pilot_id TEXT NOT NULL REFERENCES pilot_passports(id) ON DELETE RESTRICT,
    publish_job_id TEXT REFERENCES publish_jobs(id) ON DELETE RESTRICT,
    cta_token TEXT NOT NULL,
    contact_hash TEXT NOT NULL,
    contact_ref TEXT,
    qualification TEXT NOT NULL DEFAULT 'unknown' CHECK(qualification IN ('unknown','qualified','unqualified')),
    occurred_at TEXT NOT NULL,
    next_step TEXT,
    status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new','contacted','converted','closed')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    row_version INTEGER NOT NULL DEFAULT 1 CHECK(row_version >= 1)
);

CREATE TABLE pilot_reports (
    id TEXT PRIMARY KEY,
    pilot_id TEXT NOT NULL REFERENCES pilot_passports(id) ON DELETE RESTRICT,
    report_sha256 TEXT NOT NULL CHECK(length(report_sha256)=64),
    markdown_text TEXT NOT NULL,
    decision TEXT NOT NULL CHECK(decision IN ('sufficient_data','insufficient_data','success','failure')),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE idea_cards (
    id TEXT PRIMARY KEY,
    pilot_id TEXT NOT NULL REFERENCES pilot_passports(id) ON DELETE RESTRICT,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE RESTRICT,
    project_id TEXT REFERENCES content_projects(id) ON DELETE RESTRICT,
    angle TEXT NOT NULL,
    role TEXT NOT NULL,
    proof_summary TEXT NOT NULL,
    format TEXT NOT NULL,
    relevance_score INTEGER CHECK(relevance_score BETWEEN 0 AND 3),
    proof_score INTEGER CHECK(proof_score BETWEEN 0 AND 3),
    novelty_score INTEGER CHECK(novelty_score BETWEEN 0 AND 3),
    fit_score INTEGER CHECK(fit_score BETWEEN 0 AND 3),
    effort_score INTEGER CHECK(effort_score BETWEEN 0 AND 3),
    total_score INTEGER CHECK(total_score BETWEEN 0 AND 15),
    decision TEXT NOT NULL DEFAULT 'captured' CHECK(decision IN ('captured','scored','selected','rejected')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    row_version INTEGER NOT NULL DEFAULT 1 CHECK(row_version >= 1)
);

CREATE TABLE campaigns (
    id TEXT PRIMARY KEY,
    pilot_id TEXT NOT NULL REFERENCES pilot_passports(id) ON DELETE RESTRICT,
    name TEXT NOT NULL,
    goal TEXT NOT NULL,
    start_day TEXT NOT NULL,
    end_day TEXT NOT NULL,
    capacity INTEGER NOT NULL CHECK(capacity > 0),
    formats_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','running','completed','stopped')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    row_version INTEGER NOT NULL DEFAULT 1 CHECK(row_version >= 1),
    CHECK(end_day >= start_day)
);

CREATE TABLE learnings (
    id TEXT PRIMARY KEY,
    pilot_id TEXT NOT NULL REFERENCES pilot_passports(id) ON DELETE RESTRICT,
    campaign_id TEXT REFERENCES campaigns(id) ON DELETE RESTRICT,
    publish_job_id TEXT REFERENCES publish_jobs(id) ON DELETE RESTRICT,
    kind TEXT NOT NULL CHECK(kind IN ('fact','hypothesis')),
    statement TEXT NOT NULL,
    action TEXT NOT NULL CHECK(action IN ('repeat','change','test_next')),
    supporting_json TEXT NOT NULL DEFAULT '{}',
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TRIGGER guard_qa_scope
BEFORE INSERT ON qa_results
WHEN NOT EXISTS (
    SELECT 1 FROM content_revisions r
    WHERE r.id=NEW.revision_id AND r.project_id=NEW.project_id
)
BEGIN SELECT RAISE(ABORT, 'qa revision project mismatch'); END;

CREATE TRIGGER guard_approval_scope
BEFORE INSERT ON approval_decisions
WHEN NOT EXISTS (
    SELECT 1 FROM content_revisions r
    WHERE r.id=NEW.revision_id AND r.project_id=NEW.project_id
)
BEGIN SELECT RAISE(ABORT, 'approval revision project mismatch'); END;

CREATE TRIGGER guard_publish_package_scope
BEFORE INSERT ON publish_packages
WHEN NOT EXISTS (
    SELECT 1 FROM content_revisions r
    WHERE r.id=NEW.revision_id AND r.project_id=NEW.project_id
)
BEGIN SELECT RAISE(ABORT, 'publish package revision project mismatch'); END;
