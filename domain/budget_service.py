"""Fail-closed multi-scope budget reservations."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from db.content_repository import ContentRepository
from domain.errors import ConflictError, NotFoundError, ValidationError
from domain.readiness_service import ReadinessService


class BudgetUnavailableError(ValidationError):
    pass


class BudgetConfigurationError(ValidationError):
    pass


@dataclass(frozen=True)
class BudgetContext:
    pilot_id: str
    project_id: str
    revision_id: str
    operation_ref: str
    day_ref: str
    month_ref: str

    def scopes(self):
        return {
            "operation": self.operation_ref,
            "project": self.project_id,
            "pilot": self.pilot_id,
            "day": self.day_ref,
            "month": self.month_ref,
        }


@dataclass(frozen=True)
class ReservationRecord:
    id: str
    command_id: str
    operation_id: str
    job_id: str
    pilot_id: str
    project_id: str
    upper_bound_minor: int
    currency: str
    status: str
    held_minor: int
    settled_minor: int
    row_version: int


def _reservation(row):
    return ReservationRecord(**{name: row[name] for name in ReservationRecord.__dataclass_fields__})


class BudgetService:
    def __init__(self, repository: ContentRepository, readiness: ReadinessService):
        self.repository = repository
        self.readiness = readiness

    def configure_limit(self, limit_id, scope_type, scope_ref, window_start,
                        window_end, currency, limit_minor, expected_row_version,
                        actor_id, at):
        if scope_type not in {"operation", "project", "pilot", "day", "month"}:
            raise ValidationError("invalid budget scope")
        if not isinstance(limit_minor, int) or limit_minor <= 0:
            raise ValidationError("budget limit must be positive minor units")
        if len(currency) != 3 or not actor_id:
            raise ValidationError("currency and actor are required")
        with self.repository.transaction() as connection:
            existing = connection.execute("SELECT * FROM budget_limits WHERE id=?", (limit_id,)).fetchone()
            if expected_row_version is None:
                if existing:
                    raise ConflictError("budget limit already exists")
                try:
                    connection.execute(
                        """INSERT INTO budget_limits(id,scope_type,scope_ref,window_start,
                        window_end,currency,limit_minor,status,created_by,created_at,updated_at)
                        VALUES(?,?,?,?,?,?,?,'active',?,?,?)""",
                        (limit_id, scope_type, scope_ref, window_start, window_end,
                         currency, limit_minor, actor_id, at, at),
                    )
                except sqlite3.IntegrityError as exc:
                    raise ConflictError("budget scope already configured") from exc
            else:
                cursor = connection.execute(
                    """UPDATE budget_limits SET limit_minor=?,updated_at=?,row_version=row_version+1
                    WHERE id=? AND row_version=?""",
                    (limit_minor, at, limit_id, expected_row_version),
                )
                if cursor.rowcount != 1:
                    if existing is None:
                        raise NotFoundError(f"budget limit not found: {limit_id}")
                    raise ConflictError("stale budget limit version")

    def reserve(self, command_id, reservation_id, operation_id, job_id,
                context: BudgetContext, upper_bound_minor, currency,
                request_sha256, provider_key, at) -> ReservationRecord:
        if not isinstance(upper_bound_minor, int) or upper_bound_minor <= 0:
            raise ValidationError("upper bound must be positive minor units")
        if len(request_sha256) != 64 or any(c not in "0123456789abcdef" for c in request_sha256):
            raise ValidationError("request hash must be lowercase SHA-256")
        with self.repository.transaction() as connection:
            existing = connection.execute(
                """SELECT r.*,o.request_sha256,o.provider_key FROM budget_reservations r
                JOIN external_operations o ON o.id=r.operation_id WHERE r.command_id=?""",
                (command_id,),
            ).fetchone()
            if existing:
                if (existing["upper_bound_minor"] != upper_bound_minor
                        or existing["currency"] != currency
                        or existing["request_sha256"] != request_sha256
                        or existing["provider_key"] != provider_key):
                    raise ConflictError("reservation command payload conflict")
                return _reservation(existing)

            pilot = connection.execute(
                "SELECT status FROM pilot_passports WHERE id=?", (context.pilot_id,)
            ).fetchone()
            if pilot is None or pilot["status"] != "running":
                raise BudgetUnavailableError("pilot is not running")
            if connection.execute(
                "SELECT 1 FROM budget_incidents WHERE pilot_id=? AND status='open'",
                (context.pilot_id,),
            ).fetchone():
                raise BudgetUnavailableError("pilot has an open budget incident")
            readiness = self.readiness.check_production_ready_in_transaction(
                connection, context.project_id, context.revision_id, at
            )
            if not readiness.ready:
                raise BudgetUnavailableError("revision is not production ready: " + ",".join(readiness.blockers))

            limits = self._resolve_limits(connection, context, currency, at)
            balances = self._availability_in(connection, limits)
            insufficient = [scope for scope, value in balances.items()
                            if value["available"] < upper_bound_minor]
            if insufficient:
                raise BudgetUnavailableError("insufficient budget: " + ",".join(sorted(insufficient)))

            payload = json.dumps(
                {"operation_id": operation_id, "reservation_id": reservation_id,
                 "revision_id": context.revision_id},
                sort_keys=True, separators=(",", ":"),
            )
            try:
                connection.execute(
                    """INSERT INTO external_operations(id,command_id,pilot_id,project_id,
                    operation_kind,provider_key,request_sha256,idempotency_key,status,
                    billing_status,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?, 'intent','pending',?,?)""",
                    (operation_id, command_id, context.pilot_id, context.project_id,
                     context.operation_ref, provider_key, request_sha256, command_id, at, at),
                )
                connection.execute(
                    """INSERT INTO jobs(id,command_id,kind,pilot_id,project_id,revision_id,
                    operation_id,payload_json,payload_sha256,status,available_at,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,'queued',?,?,?)""",
                    (job_id, command_id, context.operation_ref, context.pilot_id,
                     context.project_id, context.revision_id, operation_id, payload,
                     request_sha256, at, at, at),
                )
                connection.execute(
                    """INSERT INTO budget_reservations(id,command_id,operation_id,job_id,pilot_id,
                    project_id,upper_bound_minor,currency,status,held_minor,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,'held',?,?,?)""",
                    (reservation_id, command_id, operation_id, job_id, context.pilot_id,
                     context.project_id, upper_bound_minor, currency, upper_bound_minor, at, at),
                )
                connection.executemany(
                    """INSERT INTO budget_reservation_scopes(
                    reservation_id,limit_id,exposure_minor,created_at) VALUES(?,?,?,?)""",
                    [(reservation_id, row["id"], upper_bound_minor, at) for row in limits.values()],
                )
                connection.execute(
                    """INSERT INTO outbox_events(id,event_type,aggregate_id,payload_json,status,
                    available_at,created_at,updated_at) VALUES(?,?,?,?,'pending',?,?,?)""",
                    (f"outbox:{command_id}", "paid_job.reserved", job_id, payload, at, at, at),
                )
            except sqlite3.IntegrityError as exc:
                raise ConflictError("reservation identity conflict") from exc
        return self.get(reservation_id)

    def get(self, reservation_id):
        row = self.repository.fetchone("SELECT * FROM budget_reservations WHERE id=?", (reservation_id,))
        if row is None:
            raise NotFoundError(f"reservation not found: {reservation_id}")
        return _reservation(row)

    def availability(self, context: BudgetContext, currency, at):
        with self.repository.transaction() as connection:
            limits = self._resolve_limits(connection, context, currency, at)
            return self._availability_in(connection, limits)

    @staticmethod
    def _resolve_limits(connection, context, currency, at):
        limits = {}
        for scope_type, scope_ref in sorted(context.scopes().items()):
            rows = connection.execute(
                """SELECT * FROM budget_limits WHERE scope_type=? AND scope_ref=?
                AND currency=? AND status='active'
                AND (window_start IS NULL OR window_start<=?)
                AND (window_end IS NULL OR window_end>?) ORDER BY id""",
                (scope_type, scope_ref, currency, at, at),
            ).fetchall()
            if len(rows) != 1:
                raise BudgetConfigurationError(f"missing or ambiguous {scope_type} limit")
            limits[scope_type] = rows[0]
        return limits

    @staticmethod
    def _availability_in(connection, limits):
        result = {}
        for scope, limit in limits.items():
            outstanding = connection.execute(
                """SELECT COALESCE(SUM(r.held_minor),0)
                FROM budget_reservation_scopes s JOIN budget_reservations r ON r.id=s.reservation_id
                WHERE s.limit_id=? AND r.status IN ('held','partially_settled','unknown','overspent')""",
                (limit["id"],),
            ).fetchone()[0]
            charges = connection.execute(
                """SELECT COALESCE(SUM(CASE WHEN c.kind='refund' THEN -c.amount_minor ELSE c.amount_minor END),0)
                FROM cost_entries c JOIN budget_reservation_scopes s ON s.reservation_id=c.reservation_id
                WHERE s.limit_id=?""", (limit["id"],),
            ).fetchone()[0]
            result[scope] = {
                "limit": limit["limit_minor"], "committed": charges,
                "outstanding": outstanding,
                "available": limit["limit_minor"] - charges - outstanding,
            }
        return result
