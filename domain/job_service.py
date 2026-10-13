"""Durable background jobs with leases and monotonic fence tokens."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass

from db.content_repository import ContentRepository
from domain.errors import ConflictError, NotFoundError, ValidationError


class JobStateError(ValidationError):
    pass


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class JobRecord:
    id: str
    command_id: str
    kind: str
    status: str
    available_at: str
    lease_owner: str | None
    fence_token: int
    lease_until: str | None
    attempt_count: int
    external_intent_at: str | None
    operation_id: str | None
    row_version: int


JobClaim = JobRecord


def _record(row) -> JobRecord:
    return JobRecord(**{name: row[name] for name in JobRecord.__dataclass_fields__})


class JobService:
    def __init__(self, repository: ContentRepository):
        self.repository = repository

    def get(self, job_id: str) -> JobRecord:
        row = self.repository.fetchone("SELECT * FROM jobs WHERE id=?", (job_id,))
        if row is None:
            raise NotFoundError(f"job not found: {job_id}")
        return _record(row)

    def enqueue(self, job_id, command_id, kind, pilot_id, project_id, revision_id,
                payload, available_at, created_at) -> JobRecord:
        payload_json = _canonical(payload)
        digest = hashlib.sha256(payload_json.encode()).hexdigest()
        with self.repository.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM jobs WHERE command_id=?", (command_id,)
            ).fetchone()
            if existing:
                if existing["payload_sha256"] != digest:
                    raise ConflictError("command payload conflict")
                return _record(existing)
            try:
                connection.execute(
                    """INSERT INTO jobs(id,command_id,kind,pilot_id,project_id,revision_id,
                    payload_json,payload_sha256,status,available_at,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,'queued',?,?,?)""",
                    (job_id, command_id, kind, pilot_id, project_id, revision_id,
                     payload_json, digest, available_at, created_at, created_at),
                )
            except sqlite3.IntegrityError as exc:
                raise ConflictError("job identity conflict") from exc
        return self.get(job_id)

    def claim_next(self, worker_id, now, lease_until, kinds=None) -> JobClaim | None:
        if lease_until <= now:
            raise ValidationError("lease must end after claim time")
        with self.repository.transaction() as connection:
            parameters = [now]
            kind_sql = ""
            if kinds:
                kind_sql = f" AND kind IN ({','.join('?' for _ in kinds)})"
                parameters.extend(kinds)
            row = connection.execute(
                f"""SELECT id FROM jobs WHERE status IN ('queued','failed')
                AND available_at<=? AND attempt_count<max_attempts {kind_sql}
                ORDER BY available_at,created_at,id LIMIT 1""",
                tuple(parameters),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """UPDATE jobs SET status='running',lease_owner=?,lease_until=?,
                fence_token=fence_token+1,attempt_count=attempt_count+1,
                updated_at=?,row_version=row_version+1 WHERE id=?""",
                (worker_id, lease_until, now, row["id"]),
            )
            return _record(connection.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone())

    def create_operation(self, operation_id, command_id, operation_kind,
                         provider_key, request_sha256, created_at):
        with self.repository.transaction() as connection:
            try:
                connection.execute(
                    """INSERT INTO external_operations(
                    id,command_id,operation_kind,provider_key,request_sha256,idempotency_key,
                    status,billing_status,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,'intent','pending',?,?)""",
                    (operation_id, command_id, operation_kind, provider_key,
                     request_sha256, command_id, created_at, created_at),
                )
            except sqlite3.IntegrityError as exc:
                raise ConflictError("operation identity conflict") from exc

    def mark_external_intent(self, job_id, worker_id, fence_token, operation_id, at):
        with self.repository.transaction() as connection:
            cursor = connection.execute(
                """UPDATE jobs SET operation_id=?,external_intent_at=?,updated_at=?,
                row_version=row_version+1 WHERE id=? AND status='running'
                AND lease_owner=? AND fence_token=? AND external_intent_at IS NULL""",
                (operation_id, at, at, job_id, worker_id, fence_token),
            )
            if cursor.rowcount != 1:
                self._transition_failure(connection, job_id)
            connection.execute(
                "UPDATE external_operations SET status='submitting',updated_at=?,row_version=row_version+1 WHERE id=?",
                (at, operation_id),
            )
        return self.get(job_id)

    def succeed(self, job_id, worker_id, fence_token, result, at):
        result_json = _canonical(result)
        digest = hashlib.sha256(result_json.encode()).hexdigest()
        return self._owned_transition(
            job_id, worker_id, fence_token, "succeeded", at,
            "result_json=?,result_sha256=?,error_code=NULL", (result_json, digest),
        )

    def fail_retryable(self, job_id, worker_id, fence_token, error_code, available_at, at):
        with self.repository.transaction() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise NotFoundError(f"job not found: {job_id}")
            if (row["status"] != "running" or row["lease_owner"] != worker_id
                    or row["fence_token"] != fence_token):
                raise ConflictError("stale job lease")
            if row["external_intent_at"] is not None:
                raise JobStateError("job after external intent requires reconciliation")
            connection.execute(
                """UPDATE jobs SET status='queued',available_at=?,lease_owner=NULL,
                lease_until=NULL,error_code=?,updated_at=?,row_version=row_version+1 WHERE id=?""",
                (available_at, error_code, at, job_id),
            )
        return self.get(job_id)

    def cancel(self, job_id, at):
        with self.repository.transaction() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise NotFoundError(f"job not found: {job_id}")
            if row["external_intent_at"] is not None:
                raise JobStateError("cannot cancel after external intent")
            if row["status"] not in ("queued", "failed", "running"):
                raise JobStateError(f"cannot cancel job in {row['status']}")
            connection.execute(
                """UPDATE jobs SET status='cancelled',lease_owner=NULL,lease_until=NULL,
                updated_at=?,row_version=row_version+1 WHERE id=?""", (at, job_id),
            )
        return self.get(job_id)

    def mark_unknown(self, job_id, worker_id, fence_token, error_code, at):
        return self._owned_transition(
            job_id, worker_id, fence_token, "unknown_outcome", at,
            "error_code=?", (error_code,),
        )

    def recover_expired(self, now) -> tuple[int, int]:
        with self.repository.transaction() as connection:
            requeued = connection.execute(
                """UPDATE jobs SET status='queued',lease_owner=NULL,lease_until=NULL,
                updated_at=?,row_version=row_version+1 WHERE status='running'
                AND lease_until<? AND external_intent_at IS NULL""", (now, now),
            ).rowcount
            unknown = connection.execute(
                """UPDATE jobs SET status='unknown_outcome',lease_owner=NULL,lease_until=NULL,
                error_code='LEASE_EXPIRED_AFTER_INTENT',updated_at=?,row_version=row_version+1
                WHERE status='running' AND lease_until<? AND external_intent_at IS NOT NULL""",
                (now, now),
            ).rowcount
        return requeued, unknown

    def _owned_transition(self, job_id, worker_id, fence_token, status, at, fields, values):
        with self.repository.transaction() as connection:
            cursor = connection.execute(
                f"""UPDATE jobs SET status=?,{fields},lease_owner=NULL,lease_until=NULL,
                updated_at=?,row_version=row_version+1 WHERE id=? AND status='running'
                AND lease_owner=? AND fence_token=?""",
                (status, *values, at, job_id, worker_id, fence_token),
            )
            if cursor.rowcount != 1:
                self._transition_failure(connection, job_id)
        return self.get(job_id)

    @staticmethod
    def _transition_failure(connection, job_id):
        if connection.execute("SELECT 1 FROM jobs WHERE id=?", (job_id,)).fetchone() is None:
            raise NotFoundError(f"job not found: {job_id}")
        raise ConflictError("stale job lease or invalid state")
