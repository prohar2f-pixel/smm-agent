CREATE TABLE llm_attempts (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    operation_id TEXT NOT NULL REFERENCES external_operations(id) ON DELETE RESTRICT,
    attempt_no INTEGER NOT NULL CHECK(attempt_no IN (1,2)),
    idempotency_key TEXT NOT NULL UNIQUE,
    response_sha256 TEXT NOT NULL CHECK(length(response_sha256)=64),
    response_ref TEXT NOT NULL,
    validation_status TEXT NOT NULL CHECK(validation_status IN ('valid','invalid')),
    validated_output_json TEXT,
    input_tokens INTEGER NOT NULL CHECK(input_tokens>=0),
    output_tokens INTEGER NOT NULL CHECK(output_tokens>=0),
    latency_ms INTEGER NOT NULL CHECK(latency_ms>=0),
    cost_minor INTEGER NOT NULL CHECK(cost_minor>=0),
    currency TEXT NOT NULL CHECK(length(currency)=3),
    model TEXT NOT NULL,
    provider TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(operation_id,attempt_no),
    UNIQUE(provider,evidence_id)
);

CREATE TRIGGER trg_reservation_scope_insert
BEFORE INSERT ON budget_reservations
WHEN NEW.project_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM content_projects p WHERE p.id=NEW.project_id AND p.pilot_id=NEW.pilot_id
)
BEGIN SELECT RAISE(ABORT,'reservation pilot/project mismatch'); END;

CREATE TRIGGER trg_reservation_scope_update
BEFORE UPDATE OF pilot_id,project_id ON budget_reservations
WHEN NEW.project_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM content_projects p WHERE p.id=NEW.project_id AND p.pilot_id=NEW.pilot_id
)
BEGIN SELECT RAISE(ABORT,'reservation pilot/project mismatch'); END;

CREATE TRIGGER trg_cost_tuple_insert
BEFORE INSERT ON cost_entries
WHEN NEW.reservation_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM budget_reservations r
    JOIN external_operations o ON o.id=r.operation_id
    WHERE r.id=NEW.reservation_id AND r.operation_id=NEW.operation_id
      AND r.currency=NEW.currency AND o.provider_key=NEW.provider
)
BEGIN SELECT RAISE(ABORT,'cost operation/reservation/provider/currency mismatch'); END;
