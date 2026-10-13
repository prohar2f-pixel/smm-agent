"""Guarded publishing with exact approvals and unknown-outcome handling."""

from __future__ import annotations

import hashlib
import json
import sqlite3

from db.content_repository import ContentRepository
from domain.errors import ConflictError, NotFoundError, ValidationError
from domain.package_hash import canonical_package_hash
from publishing.base import DefinitivePublishError, PublishRequest, PublisherAdapter, PublishResult


class PublishService:
    def __init__(self, repository: ContentRepository, *, publishing_enabled: bool = False):
        self.repository = repository
        self.publishing_enabled = publishing_enabled

    @staticmethod
    def package_hash(caption_text: str, assets_json: str) -> str:
        return canonical_package_hash(caption_text, assets_json)

    def enqueue(self, *, job_id: str, package_id: str, platform: str,
                target_ref: str, created_at: str):
        with self.repository.transaction() as connection:
            package = connection.execute(
                "SELECT * FROM publish_packages WHERE id=?", (package_id,),
            ).fetchone()
            if package is None:
                raise NotFoundError("publish package not found")
            if package["package_sha256"] != self.package_hash(
                    package["caption_text"], package["assets_json"]):
                raise ConflictError("publish package hash mismatch")
            approval = self._latest_approval(connection, package, platform, target_ref)
            if (approval is None or approval["status"] != "approved"
                    or approval["expires_at"] is None
                    or approval["expires_at"] < created_at):
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
        if not self.publishing_enabled:
            raise ValidationError("publishing feature is disabled")
        with self.repository.transaction() as connection:
            row = self._load_ready(connection, job_id, at)
            if row["status"] == "published":
                return row
            if row["status"] not in ("queued", "failed"):
                raise ConflictError(f"publish job cannot send from {row['status']}")
            attempt_no = connection.execute(
                "SELECT COUNT(*) FROM publish_attempts WHERE publish_job_id=?", (job_id,),
            ).fetchone()[0] + 1
            key = hashlib.sha256(f"publish:{job_id}:{attempt_no}".encode()).hexdigest()
            request = PublishRequest(key, row["target_ref"], row["caption_text"], row["assets_json"])
            operation_id = f"publish-op:{job_id}:{attempt_no}"
            request_hash = hashlib.sha256(
                json.dumps(request.__dict__, sort_keys=True).encode()
            ).hexdigest()
            connection.execute(
                """INSERT INTO external_operations(id,command_id,pilot_id,project_id,
                operation_kind,provider_key,request_sha256,idempotency_key,status,
                billing_status,created_at,updated_at) SELECT ?,?,cp.pilot_id,pp.project_id,
                'publish',?,?,?,'submitting','no_charge',?,? FROM publish_jobs pj
                JOIN publish_packages pp ON pp.id=pj.package_id
                JOIN content_projects cp ON cp.id=pp.project_id WHERE pj.id=?""",
                (operation_id, operation_id, adapter.provider_key, request_hash, key,
                 at, at, job_id),
            )
            connection.execute(
                """INSERT INTO publish_attempts(id,publish_job_id,attempt_no,idempotency_key,
                operation_id,status,request_sha256,error_code,created_at)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                (f"{job_id}:{attempt_no}", job_id, attempt_no, key, operation_id,
                 "unknown_outcome", request_hash, "SEND_IN_PROGRESS", at),
            )
            connection.execute(
                """UPDATE publish_jobs SET status='unknown_outcome',error_code='SEND_IN_PROGRESS',
                operation_id=?,updated_at=?,row_version=row_version+1 WHERE id=?""",
                (operation_id, at, job_id),
            )
        try:
            result = adapter.send(request)
        except DefinitivePublishError as exc:
            return self._settle(job_id, attempt_no, key, "failed", None,
                                type(exc).__name__, at)
        except Exception as exc:
            return self._settle(job_id, attempt_no, key, "unknown_outcome", None,
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
            current = connection.execute(
                """SELECT pj.status,pa.operation_id,pa.status AS attempt_status
                FROM publish_jobs pj JOIN publish_attempts pa ON pa.publish_job_id=pj.id
                WHERE pj.id=? AND pa.idempotency_key=?""", (job_id, key),
            ).fetchone()
            if (current is None or current["status"] != "unknown_outcome"
                    or current["attempt_status"] != "unknown_outcome"):
                raise ConflictError("publish reconciliation lost state race")
            result_hash = hashlib.sha256(
                json.dumps(result.__dict__, sort_keys=True).encode()
            ).hexdigest()
            attempt = connection.execute(
                """UPDATE publish_attempts SET status='reconciled',external_id=?,
                result_sha256=?,evidence_ref=? WHERE publish_job_id=? AND idempotency_key=?
                AND status='unknown_outcome'""",
                (result.external_id, result_hash, result.evidence_ref, job_id, key),
            )
            job = connection.execute(
                """UPDATE publish_jobs SET status='published',external_id=?,result_sha256=?,
                error_code=NULL,updated_at=?,row_version=row_version+1 WHERE id=?
                AND status='unknown_outcome'""",
                (result.external_id, result_hash, at, job_id),
            )
            if attempt.rowcount != 1 or job.rowcount != 1:
                raise ConflictError("publish reconciliation lost state race")
            if current["operation_id"]:
                connection.execute(
                    """UPDATE external_operations SET status='reconciled',billing_status='no_charge',
                    external_id=?,result_sha256=?,error_code=NULL,updated_at=?,
                    row_version=row_version+1 WHERE id=? AND status='unknown_outcome'""",
                    (result.external_id, result_hash, at, current["operation_id"]),
                )
        return self.get(job_id)

    def confirm_manual(self, job_id: str, external_id: str, evidence_ref: str, at: str):
        if not self.publishing_enabled:
            raise ValidationError("publishing feature is disabled")
        if not external_id or not evidence_ref:
            raise ValidationError("manual publication requires external id and evidence")
        with self.repository.transaction() as connection:
            row = self._load_ready(connection, job_id, at)
            if row["platform"] != "manual" or row["status"] != "awaiting_manual":
                raise ConflictError("only an awaiting manual job can be confirmed")
        result = PublishResult(external_id, evidence_ref)
        return self._settle(job_id, 1, hashlib.sha256(f"manual:{job_id}".encode()).hexdigest(),
                            "manual_confirmed", result, None, at)

    def get(self, job_id: str):
        row = self.repository.fetchone("SELECT * FROM publish_jobs WHERE id=?", (job_id,))
        if row is None:
            raise NotFoundError("publish job not found")
        return row

    @staticmethod
    def _latest_approval(connection, package, platform, target_ref):
        return connection.execute(
            """SELECT * FROM approval_decisions WHERE project_id=? AND revision_id=?
            AND stage='publish' AND subject_sha256=? AND platform=? AND target_ref=?
            ORDER BY created_at DESC,id DESC LIMIT 1""",
            (package["project_id"], package["revision_id"],
             package["package_sha256"], platform, target_ref),
        ).fetchone()

    def _load_ready(self, connection, job_id, at):
        row = connection.execute(
            """SELECT pj.*,pp.project_id,pp.revision_id,pp.caption_text,pp.assets_json,pp.package_sha256,
            ad.subject_sha256 AS approved_hash,ad.status AS approval_status,
            ad.target_ref AS approved_target FROM publish_jobs pj
            JOIN publish_packages pp ON pp.id=pj.package_id
            LEFT JOIN approval_decisions ad ON ad.id=pj.approval_id WHERE pj.id=?""",
            (job_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError("publish job not found")
        if row["package_sha256"] != self.package_hash(row["caption_text"], row["assets_json"]):
            raise ConflictError("publish package hash mismatch")
        project = connection.execute(
            "SELECT current_revision_id FROM content_projects WHERE id=?", (row["project_id"],),
        ).fetchone()
        latest = self._latest_approval(
            connection, row, row["platform"], row["target_ref"])
        if (latest is None or latest["status"] != "approved"
                or latest["id"] != row["approval_id"]
                or latest["expires_at"] is None or latest["expires_at"] < at
                or project is None or project["current_revision_id"] != row["revision_id"]
                or row["approval_status"] != "approved"
                or row["approved_hash"] != row["package_sha256"]
                or row["approved_target"] != row["target_ref"]):
            raise ValidationError("publish approval is missing or stale")
        final = connection.execute(
            """SELECT ad.status FROM approval_decisions ad JOIN assets a
            ON a.sha256=ad.subject_sha256 AND a.project_id=ad.project_id
            AND a.revision_id=ad.revision_id AND a.role='master'
            WHERE ad.project_id=? AND ad.revision_id=? AND ad.stage='final'
            ORDER BY ad.created_at DESC,ad.id DESC LIMIT 1""",
            (row["project_id"], row["revision_id"]),
        ).fetchone()
        if final is None or final["status"] != "approved":
            raise ValidationError("current final approval is required")
        bad_source = connection.execute(
            """SELECT 1 FROM content_project_sources cps JOIN sources s ON s.id=cps.source_id
            WHERE cps.project_id=? AND (s.rights_status!='allowed' OR s.privacy_status!='clear')
            LIMIT 1""", (row["project_id"],),
        ).fetchone()
        if bad_source:
            raise ValidationError("source rights or privacy no longer permit publication")
        asset_ids = json.loads(row["assets_json"])
        if asset_ids:
            placeholders = ",".join("?" for _ in asset_ids)
            valid_count = connection.execute(
                f"""SELECT COUNT(*) FROM assets WHERE id IN ({placeholders})
                AND project_id=? AND revision_id=? AND rights_status='allowed'
                AND privacy_status='clear'""",
                (*asset_ids, row["project_id"], row["revision_id"]),
            ).fetchone()[0]
            if valid_count != len(set(asset_ids)) or len(asset_ids) != len(set(asset_ids)):
                raise ValidationError("package assets are missing, duplicated or not publishable")
        bad_claim = connection.execute(
            """SELECT 1 FROM claims c WHERE c.revision_id=? AND (
            c.status NOT IN ('supported','hypothesis') OR
            (c.status='supported' AND NOT EXISTS (
                SELECT 1 FROM claim_evidence ce WHERE ce.claim_id=c.id
                AND ce.checked_by IS NOT NULL AND ce.checked_at IS NOT NULL
            )) OR
            (c.status='hypothesis' AND c.hypothesis_label_location IS NULL)) LIMIT 1""",
            (row["revision_id"],),
        ).fetchone()
        if bad_claim:
            raise ValidationError("claim readiness changed after approval")
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
                """INSERT INTO publish_attempts(id,publish_job_id,attempt_no,
                idempotency_key,status,request_sha256,result_sha256,external_id,error_code,
                evidence_ref,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(publish_job_id,attempt_no) DO UPDATE SET
                  status=excluded.status,result_sha256=excluded.result_sha256,
                  external_id=excluded.external_id,error_code=excluded.error_code,
                  evidence_ref=excluded.evidence_ref""",
                (f"{job_id}:{attempt_no}", job_id, attempt_no, key, attempt_status,
                 hashlib.sha256(key.encode()).hexdigest(), result_hash,
                 result.external_id if result else None, error_code,
                 result.evidence_ref if result else None, at),
            )
            operation = connection.execute(
                "SELECT operation_id FROM publish_attempts WHERE publish_job_id=? AND attempt_no=?",
                (job_id, attempt_no),
            ).fetchone()
            final_status = "published" if result else attempt_status
            cursor = connection.execute(
                """UPDATE publish_jobs SET status=?,external_id=?,result_sha256=?,error_code=?,
                updated_at=?,row_version=row_version+1 WHERE id=?
                AND status IN ('unknown_outcome','awaiting_manual')""",
                (final_status, result.external_id if result else None, result_hash,
                 error_code, at, job_id),
            )
            if cursor.rowcount != 1:
                raise ConflictError("publish settlement lost state race")
            if operation and operation["operation_id"]:
                op_status = "succeeded" if result else ("failed" if attempt_status == "failed" else "unknown_outcome")
                billing = "no_charge" if op_status in ("succeeded", "failed") else "unknown"
                connection.execute(
                    """UPDATE external_operations SET status=?,billing_status=?,external_id=?,
                    result_sha256=?,error_code=?,updated_at=?,row_version=row_version+1
                    WHERE id=? AND status IN ('submitting','unknown_outcome')""",
                    (op_status, billing, result.external_id if result else None,
                     result_hash, error_code, at, operation["operation_id"]),
                )
            return connection.execute("SELECT * FROM publish_jobs WHERE id=?", (job_id,)).fetchone()
