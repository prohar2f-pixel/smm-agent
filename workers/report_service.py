"""Atomic acceptance of fenced worker reports."""

from __future__ import annotations

import hashlib
import json

from db.content_repository import ContentRepository
from domain.errors import ConflictError, NotFoundError
from domain.job_service import JobService
from domain.production_models import WorkerReportRecord
from workers.contracts import WorkerReport


class WorkerReportService:
    def __init__(self, repository: ContentRepository):
        self.repository = repository
        self.job_service = JobService(repository)

    @staticmethod
    def _fingerprint(report: WorkerReport) -> str:
        payload = {name: getattr(report, name) for name in report.__dataclass_fields__
                   if name != "created_at"}
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def accept(self, report: WorkerReport, accepted_at: str) -> WorkerReportRecord:
        report.validate()
        fingerprint = self._fingerprint(report)
        with self.repository.transaction() as connection:
            existing = connection.execute(
                "SELECT *, json_extract(machine_qa_json, '$._fingerprint') AS fingerprint "
                "FROM worker_reports WHERE job_id=? AND fence_token=?",
                (report.job_id, report.fence_token),
            ).fetchone()
            if existing:
                if existing["fingerprint"] != fingerprint:
                    raise ConflictError("worker report replay conflict")
                return self._record(existing)
            job = connection.execute("SELECT * FROM jobs WHERE id=?", (report.job_id,)).fetchone()
            if job is None:
                raise NotFoundError(f"job not found: {report.job_id}")
            payload = json.loads(job["payload_json"])
            if (job["status"] != "running" or job["lease_owner"] != report.worker_id
                    or job["fence_token"] != report.fence_token
                    or job["lease_until"] < accepted_at
                    or payload.get("input_sha256") != report.input_sha256):
                raise ConflictError("stale worker lease or input")
            qa = dict(report.machine_qa)
            qa["_fingerprint"] = fingerprint
            connection.execute(
                """INSERT INTO worker_reports(id,job_id,fence_token,schema_version,status,
                input_sha256,output_path,output_sha256,duration_ms,render_profile,machine_qa_json,
                error_code,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (report.report_id, report.job_id, report.fence_token, report.schema_version,
                 report.status, report.input_sha256, report.output_path, report.output_sha256,
                 report.duration_ms, report.render_profile, json.dumps(qa, sort_keys=True),
                 report.error_code, report.created_at),
            )
            status = "succeeded" if report.status == "succeeded" else "failed"
            result = json.dumps({"report_id": report.report_id,
                                 "output_sha256": report.output_sha256},
                                sort_keys=True, separators=(",", ":"))
            connection.execute(
                """UPDATE jobs SET status=?,result_json=?,result_sha256=?,error_code=?,
                lease_owner=NULL,lease_until=NULL,updated_at=?,row_version=row_version+1
                WHERE id=?""",
                (status, result, hashlib.sha256(result.encode()).hexdigest(), report.error_code,
                 accepted_at, report.job_id),
            )
            row = connection.execute("SELECT * FROM worker_reports WHERE id=?",
                                     (report.report_id,)).fetchone()
            return self._record(row)

    @staticmethod
    def _record(row) -> WorkerReportRecord:
        return WorkerReportRecord(
            id=row["id"], job_id=row["job_id"], fence_token=row["fence_token"],
            schema_version=row["schema_version"], status=row["status"],
            input_sha256=row["input_sha256"], output_sha256=row["output_sha256"],
            duration_ms=row["duration_ms"],
        )
