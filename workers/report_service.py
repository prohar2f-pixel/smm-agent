"""Atomic acceptance of fenced worker reports."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from db.content_repository import ContentRepository
from domain.errors import ConflictError, NotFoundError
from domain.job_service import JobService
from domain.production_models import WorkerReportRecord
from workers.contracts import WorkerReport


@dataclass(frozen=True)
class VerifiedArtifact:
    path: Path
    sha256: str
    size_bytes: int
    mime_type: str
    duration_ms: int | None
    width: int | None
    height: int | None


class ArtifactVerifier:
    def __init__(self, root: str | Path, probe: Callable[[Path], dict]):
        self.root = Path(root).resolve()
        self.probe = probe

    def verify(self, report: WorkerReport, expected: dict) -> VerifiedArtifact:
        if report.output_path != expected.get("output_path"):
            raise ConflictError("worker output path differs from manifest")
        if report.render_profile != expected.get("render_profile"):
            raise ConflictError("worker render profile differs from manifest")
        candidate = self.root / report.output_path
        cursor = self.root
        for part in Path(report.output_path).parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise ConflictError("artifact path contains a symlink")
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(self.root)
        except (FileNotFoundError, ValueError) as exc:
            raise ConflictError("artifact is missing or outside worker root") from exc
        descriptor = os.open(resolved, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            digest = hashlib.sha256()
            size = 0
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
        finally:
            os.close(descriptor)
        actual_hash = digest.hexdigest()
        if actual_hash != report.output_sha256:
            raise ConflictError("artifact hash mismatch")
        metadata = self.probe(resolved)
        mime = metadata.get("mime_type") or mimetypes.guess_type(resolved.name)[0]
        for key in ("mime_type", "duration_ms", "width", "height"):
            required = expected.get(key)
            if required is not None and (mime if key == "mime_type" else metadata.get(key)) != required:
                raise ConflictError(f"artifact {key} mismatch")
        return VerifiedArtifact(resolved, actual_hash, size, mime or "application/octet-stream",
                                metadata.get("duration_ms"), metadata.get("width"),
                                metadata.get("height"))


class WorkerReportService:
    def __init__(self, repository: ContentRepository, verifier: ArtifactVerifier):
        self.repository = repository
        self.job_service = JobService(repository)
        self.verifier = verifier

    @staticmethod
    def _fingerprint(report: WorkerReport) -> str:
        payload = {name: getattr(report, name) for name in report.__dataclass_fields__
                   if name != "created_at"}
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def accept(self, report: WorkerReport, accepted_at: str) -> WorkerReportRecord:
        report.validate()
        fingerprint = self._fingerprint(report)
        artifact = None
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
            if job["project_id"] is None or job["revision_id"] is None:
                raise ConflictError("successful worker job requires project and revision scope")
            if report.status == "succeeded":
                artifact = self.verifier.verify(report, payload)
                asset_id = f"{report.report_id}:asset"
                connection.execute(
                    """INSERT INTO assets(id,project_id,revision_id,role,storage_key,sha256,
                    mime_type,size_bytes,duration_ms,width,height,created_by_job_id,
                    rights_status,privacy_status,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (asset_id, job["project_id"], job["revision_id"],
                     payload.get("asset_role", "preview"), report.output_path,
                     artifact.sha256, artifact.mime_type, artifact.size_bytes,
                     artifact.duration_ms, artifact.width, artifact.height, report.job_id,
                     "allowed", "clear", report.created_at),
                )
            qa = dict(report.machine_qa)
            qa["_fingerprint"] = fingerprint
            connection.execute(
                """INSERT INTO worker_reports(id,job_id,fence_token,schema_version,status,
                input_sha256,output_asset_id,output_path,output_sha256,duration_ms,
                render_profile,machine_qa_json,error_code,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (report.report_id, report.job_id, report.fence_token, report.schema_version,
                 report.status, report.input_sha256,
                 f"{report.report_id}:asset" if artifact else None,
                 report.output_path, report.output_sha256,
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
