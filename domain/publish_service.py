"""Guarded publishing with exact approvals and unknown-outcome handling."""

from __future__ import annotations

import hashlib
import json
import sqlite3

from db.content_repository import ContentRepository
from domain.errors import ConflictError, NotFoundError, ValidationError
from publishing.base import PublishRequest, PublisherAdapter, PublishResult


class PublishService:
    def __init__(self, repository: ContentRepository):
        self.repository = repository

    def enqueue(self, *, job_id: str, package_id: str, platform: str,
                target_ref: str, created_at: str):
        with self.repository.transaction() as connection:
            package = connection.execute(
                "SELECT * FROM publish_packages WHERE id=?", (package_id,),
            ).fetchone()
            if package is None:
                raise NotFoundError("publish package not found")
            approval = connection.execute(
                """SELECT * FROM approval_decisions WHERE project_id=? AND revision_id=?
                AND stage='publish' AND status='approved' AND subject_sha256=?
                AND target_ref=? ORDER BY created_at DESC LIMIT 1""",
                (package["project_id"], package["revision_id"],
                 package["package_sha256"], target_ref),
            ).fetchone()
            if approval is None:
                raise ValidationError("exact publish approval is required")
            status = "awaiting_manual" if platform == "manual" else "queued"
            try:
                connection.execute(
                    """INSERT INTO publish_jobs(id,package_id,platform,target_ref,approval_id,
                    status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)""",
                    (job_id, package_id, platform, target_ref, approval["id"], status,
                     created_at, created_at),
                )
            except sqlite3.IntegrityError:
                row = connection.execute(
                    "SELECT * FROM publish_jobs WHERE package_id=? AND platform=? AND target_ref=?",
                    (package_id, platform, target_ref),
                ).fetchone()
                if row is None:
                    raise ConflictError("publish job identity conflict")
                return row
            return connection.execute("SELECT * FROM publish_jobs WHERE id=?", (job_id,)).fetchone()

    def execute(self, job_id: str, adapter: PublisherAdapter, at: str):
        with self.repository.transaction() as connection:
            row = self._load_ready(connection, job_id)
            if row["status"] == "published":
                return row
            if row["status"] not in ("queued", "failed"):
                raise ConflictError(f"publish job cannot send from {row['status']}")
            attempt_no = connection.execute(
                "SELECT COUNT(*) FROM publish_attempts WHERE publish_job_id=?", (job_id,),
            ).fetchone()[0] + 1
            key = hashlib.sha256(f"publish:{job_id}:{attempt_no}".encode()).hexdigest()
            request = PublishRequest(key, row["target_ref"], row["caption_text"], row["assets_json"])
            connection.execute(
                "UPDATE publish_jobs SET status='sending',updated_at=?,row_version=row_version+1 WHERE id=?",
                (at, job_id),
            )
        try:
            result = adapter.send(request)
        except TimeoutError:
            return self._settle(job_id, attempt_no, key, "unknown_outcome", None,
                                "TIMEOUT_UNKNOWN", at)
        except Exception as exc:
            return self._settle(job_id, attempt_no, key, "failed", None,
                                type(exc).__name__, at)
        return self._settle(job_id, attempt_no, key, "sent", result, None, at)

    def reconcile(self, job_id: str, adapter: PublisherAdapter, at: str):
        with self.repository.transaction() as connection:
            row = connection.execute(
                """SELECT pa.* FROM publish_attempts pa JOIN publish_jobs pj
                ON pj.id=pa.publish_job_id WHERE pj.id=? AND pj.status='unknown_outcome'
                ORDER BY pa.attempt_no DESC LIMIT 1""", (job_id,),
            ).fetchone()
            if row is None:
                raise ConflictError("publish job is not awaiting reconciliation")
            key = row["idempotency_key"]
        result = adapter.reconcile(key)
        if result is None:
            return self.get(job_id)
        with self.repository.transaction() as connection:
            result_hash = hashlib.sha256(
                json.dumps(result.__dict__, sort_keys=True).encode()
            ).hexdigest()
            connection.execute(
                """UPDATE publish_attempts SET status='reconciled',external_id=?,
                result_sha256=?,evidence_ref=? WHERE publish_job_id=? AND idempotency_key=?""",
                (result.external_id, result_hash, result.evidence_ref, job_id, key),
            )
            connection.execute(
                """UPDATE publish_jobs SET status='published',external_id=?,result_sha256=?,
                error_code=NULL,updated_at=?,row_version=row_version+1 WHERE id=?""",
                (result.external_id, result_hash, at, job_id),
            )
        return self.get(job_id)

    def confirm_manual(self, job_id: str, external_id: str, evidence_ref: str, at: str):
        if not external_id or not evidence_ref:
            raise ValidationError("manual publication requires external id and evidence")
        result = PublishResult(external_id, evidence_ref)
        return self._settle(job_id, 1, hashlib.sha256(f"manual:{job_id}".encode()).hexdigest(),
                            "manual_confirmed", result, None, at)

    def get(self, job_id: str):
        row = self.repository.fetchone("SELECT * FROM publish_jobs WHERE id=?", (job_id,))
        if row is None:
            raise NotFoundError("publish job not found")
        return row

    @staticmethod
    def _load_ready(connection, job_id):
        row = connection.execute(
            """SELECT pj.*,pp.caption_text,pp.assets_json,pp.package_sha256,
            ad.subject_sha256 AS approved_hash,ad.status AS approval_status,
            ad.target_ref AS approved_target FROM publish_jobs pj
            JOIN publish_packages pp ON pp.id=pj.package_id
            LEFT JOIN approval_decisions ad ON ad.id=pj.approval_id WHERE pj.id=?""",
            (job_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError("publish job not found")
        if (row["approval_status"] != "approved"
                or row["approved_hash"] != row["package_sha256"]
                or row["approved_target"] != row["target_ref"]):
            raise ValidationError("publish approval is missing or stale")
        return row

    def _settle(self, job_id, attempt_no, key, attempt_status,
                result: PublishResult | None, error_code, at):
        with self.repository.transaction() as connection:
            job = connection.execute("SELECT * FROM publish_jobs WHERE id=?", (job_id,)).fetchone()
            if job is None:
                raise NotFoundError("publish job not found")
            if job["status"] == "published":
                return job
            result_hash = None
            if result:
                result_hash = hashlib.sha256(
                    json.dumps(result.__dict__, sort_keys=True).encode()
                ).hexdigest()
            connection.execute(
                """INSERT OR IGNORE INTO publish_attempts(id,publish_job_id,attempt_no,
                idempotency_key,status,request_sha256,result_sha256,external_id,error_code,
                evidence_ref,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (f"{job_id}:{attempt_no}", job_id, attempt_no, key, attempt_status,
                 hashlib.sha256(key.encode()).hexdigest(), result_hash,
                 result.external_id if result else None, error_code,
                 result.evidence_ref if result else None, at),
            )
            final_status = "published" if result else attempt_status
            connection.execute(
                """UPDATE publish_jobs SET status=?,external_id=?,result_sha256=?,error_code=?,
                updated_at=?,row_version=row_version+1 WHERE id=?""",
                (final_status, result.external_id if result else None, result_hash,
                 error_code, at, job_id),
            )
            return connection.execute("SELECT * FROM publish_jobs WHERE id=?", (job_id,)).fetchone()
