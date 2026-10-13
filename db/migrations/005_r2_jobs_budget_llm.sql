CREATE TABLE external_operations (
    id TEXT PRIMARY KEY,
    command_id TEXT NOT NULL UNIQUE,
    pilot_id TEXT REFERENCES pilot_passports(id) ON DELETE RESTRICT,
    project_id TEXT REFERENCES content_projects(id) ON DELETE RESTRICT,
    operation_kind TEXT NOT NULL,
    provider_key TEXT NOT NULL,
    request_sha256 TEXT NOT NULL CHECK(length(request_sha256)=64),
    idempotency_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK(status IN ('intent','submitting','succeeded','failed','unknown_outcome','reconciled')),
    billing_status TEXT NOT NULL CHECK(billing_status IN ('pending','no_charge','partially_charged','charged','partially_refunded','refunded','unknown')),
    external_id TEXT,
    result_json TEXT,
    result_sha256 TEXT CHECK(result_sha256 IS NULL OR length(result_sha256)=64),
    error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    row_version INTEGER NOT NULL DEFAULT 1 CHECK(row_version>=1)
);

CREATE INDEX idx_operations_reconcile ON external_operations(status, updated_at)
WHERE status IN ('submitting','unknown_outcome');

CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    command_id TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    pilot_id TEXT REFERENCES pilot_passports(id) ON DELETE RESTRICT,
    project_id TEXT REFERENCES content_projects(id) ON DELETE RESTRICT,
    revision_id TEXT REFERENCES content_revisions(id) ON DELETE RESTRICT,
    operation_id TEXT REFERENCES external_operations(id) ON DELETE RESTRICT,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256)=64),
    status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','succeeded','failed','unknown_outcome','cancelled')),
    available_at TEXT NOT NULL,
    lease_owner TEXT,
    fence_token INTEGER NOT NULL DEFAULT 0 CHECK(fence_token>=0),
    lease_until TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count>=0),
    max_attempts INTEGER NOT NULL DEFAULT 3 CHECK(max_attempts>0),
    external_intent_at TEXT,
    result_json TEXT,
    result_sha256 TEXT CHECK(result_sha256 IS NULL OR length(result_sha256)=64),
    error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    row_version INTEGER NOT NULL DEFAULT 1 CHECK(row_version>=1)
);

CREATE INDEX idx_jobs_claimable ON jobs(status, available_at, created_at, id)
WHERE status IN ('queued','failed');
CREATE INDEX idx_jobs_expired ON jobs(status, lease_until)
WHERE status='running';

CREATE TABLE budget_limits (
    id TEXT PRIMARY KEY,
    scope_type TEXT NOT NULL CHECK(scope_type IN ('operation','project','pilot','day','month')),
    scope_ref TEXT NOT NULL,
    window_start TEXT,
    window_end TEXT,
    currency TEXT NOT NULL CHECK(length(currency)=3),
    limit_minor INTEGER NOT NULL CHECK(limit_minor>0),
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','blocked')),
    created_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    row_version INTEGER NOT NULL DEFAULT 1 CHECK(row_version>=1),
    UNIQUE(scope_type,scope_ref,window_start,window_end,currency)
);

CREATE TABLE budget_reservations (
    id TEXT PRIMARY KEY,
    command_id TEXT NOT NULL UNIQUE,
    operation_id TEXT NOT NULL UNIQUE REFERENCES external_operations(id) ON DELETE RESTRICT,
    job_id TEXT UNIQUE REFERENCES jobs(id) ON DELETE RESTRICT,
    pilot_id TEXT NOT NULL REFERENCES pilot_passports(id) ON DELETE RESTRICT,
    project_id TEXT REFERENCES content_projects(id) ON DELETE RESTRICT,
    upper_bound_minor INTEGER NOT NULL CHECK(upper_bound_minor>0),
    currency TEXT NOT NULL CHECK(length(currency)=3),
    status TEXT NOT NULL DEFAULT 'held' CHECK(status IN ('held','partially_settled','settled','released','unknown','overspent')),
    held_minor INTEGER NOT NULL CHECK(held_minor>=0),
    settled_minor INTEGER NOT NULL DEFAULT 0 CHECK(settled_minor>=0),
    release_evidence_ref TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    row_version INTEGER NOT NULL DEFAULT 1 CHECK(row_version>=1)
);

CREATE INDEX idx_reservations_open ON budget_reservations(status, pilot_id, project_id)
WHERE status IN ('held','partially_settled','unknown','overspent');

CREATE TABLE budget_reservation_scopes (
    reservation_id TEXT NOT NULL REFERENCES budget_reservations(id) ON DELETE RESTRICT,
    limit_id TEXT NOT NULL REFERENCES budget_limits(id) ON DELETE RESTRICT,
    exposure_minor INTEGER NOT NULL CHECK(exposure_minor>0),
    created_at TEXT NOT NULL,
    PRIMARY KEY(reservation_id,limit_id)
);

CREATE TABLE cost_entries (
    id TEXT PRIMARY KEY,
    operation_id TEXT NOT NULL REFERENCES external_operations(id) ON DELETE RESTRICT,
    reservation_id TEXT REFERENCES budget_reservations(id) ON DELETE RESTRICT,
    provider TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('charge','refund','labor','allocation','development')),
    amount_minor INTEGER NOT NULL CHECK(amount_minor>0),
    currency TEXT NOT NULL CHECK(length(currency)=3),
    occurred_at TEXT NOT NULL,
    evidence_ref TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(provider,evidence_id)
);

CREATE TABLE budget_incidents (
    id TEXT PRIMARY KEY,
    pilot_id TEXT NOT NULL REFERENCES pilot_passports(id) ON DELETE RESTRICT,
    project_id TEXT REFERENCES content_projects(id) ON DELETE RESTRICT,
    operation_id TEXT NOT NULL REFERENCES external_operations(id) ON DELETE RESTRICT,
    kind TEXT NOT NULL CHECK(kind IN ('overspend','contradictory_evidence','currency_mismatch')),
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','resolved')),
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE INDEX idx_budget_incidents_open ON budget_incidents(pilot_id,status)
WHERE status='open';

CREATE TABLE prompt_templates (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    semantic_version TEXT NOT NULL,
    system_template TEXT NOT NULL,
    user_template TEXT NOT NULL,
    output_schema_json TEXT NOT NULL,
    content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(name,semantic_version)
);

CREATE TABLE prompt_runs (
    id TEXT PRIMARY KEY,
    command_id TEXT NOT NULL UNIQUE,
    operation_id TEXT NOT NULL UNIQUE REFERENCES external_operations(id) ON DELETE RESTRICT,
    purpose TEXT NOT NULL,
    prompt_name TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    model TEXT NOT NULL,
    input_sha256 TEXT NOT NULL CHECK(length(input_sha256)=64),
    output_sha256 TEXT CHECK(output_sha256 IS NULL OR length(output_sha256)=64),
    validation_status TEXT NOT NULL CHECK(validation_status IN ('pending','valid','invalid','error')),
    attempt_count INTEGER NOT NULL CHECK(attempt_count BETWEEN 0 AND 2),
    input_tokens INTEGER CHECK(input_tokens IS NULL OR input_tokens>=0),
    output_tokens INTEGER CHECK(output_tokens IS NULL OR output_tokens>=0),
    latency_ms INTEGER CHECK(latency_ms IS NULL OR latency_ms>=0),
    cost_minor INTEGER CHECK(cost_minor IS NULL OR cost_minor>=0),
    currency TEXT CHECK(currency IS NULL OR length(currency)=3),
    output_ref TEXT,
    error_code TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(prompt_name,prompt_version) REFERENCES prompt_templates(name,semantic_version) ON DELETE RESTRICT
);

CREATE INDEX idx_prompt_runs_lookup ON prompt_runs(prompt_name,prompt_version,model,created_at);

CREATE TABLE revision_prompt_runs (
    id TEXT PRIMARY KEY,
    revision_id TEXT NOT NULL REFERENCES content_revisions(id) ON DELETE RESTRICT,
    prompt_run_id TEXT NOT NULL UNIQUE REFERENCES prompt_runs(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    UNIQUE(revision_id,prompt_run_id)
);
