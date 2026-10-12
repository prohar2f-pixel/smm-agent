CREATE TABLE feature_flags (
    key TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
    config_json TEXT NOT NULL DEFAULT '{}',
    row_version INTEGER NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_by TEXT NOT NULL
);

CREATE TABLE inbox_events (
    id TEXT PRIMARY KEY,
    producer TEXT NOT NULL,
    event_id TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) = 64),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'processing', 'processed', 'failed')),
    received_at TEXT NOT NULL,
    processed_at TEXT,
    result_ref TEXT,
    error_code TEXT,
    UNIQUE (producer, event_id)
);

CREATE INDEX idx_inbox_pending
    ON inbox_events(status, received_at)
    WHERE status IN ('pending', 'processing');

CREATE TABLE outbox_events (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'leased', 'delivered', 'unknown', 'failed')),
    available_at TEXT NOT NULL,
    lease_owner TEXT,
    lease_token INTEGER,
    lease_until TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    last_error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_outbox_pending
    ON outbox_events(status, available_at, created_at)
    WHERE status IN ('pending', 'failed');

CREATE TABLE audit_events (
    id TEXT PRIMARY KEY,
    actor_id TEXT NOT NULL,
    actor_role TEXT NOT NULL,
    command_id TEXT,
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    action TEXT NOT NULL,
    before_status TEXT,
    after_status TEXT,
    row_version INTEGER CHECK (row_version IS NULL OR row_version >= 1),
    reason TEXT,
    correlation_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_audit_aggregate
    ON audit_events(aggregate_type, aggregate_id, created_at);

CREATE INDEX idx_audit_correlation
    ON audit_events(correlation_id);

CREATE TABLE callback_actions (
    token TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    action TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    expected_row_version INTEGER NOT NULL CHECK (expected_row_version >= 1),
    subject_sha256 TEXT NOT NULL CHECK (length(subject_sha256) = 64),
    expires_at TEXT NOT NULL,
    consumed_at TEXT,
    result_ref TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_callback_expiry
    ON callback_actions(expires_at)
    WHERE consumed_at IS NULL;
