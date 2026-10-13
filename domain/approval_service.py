"""Immutable, exact-hash human approval decisions."""

from __future__ import annotations

import sqlite3

from db.content_repository import ContentRepository
from domain.errors import ConflictError, NotFoundError, ValidationError


ROLE_BY_STAGE = {
    "script": {"owner", "editor", "reviewer"},
    "preview": {"owner", "reviewer"},
    "final": {"owner", "reviewer"},
    "animatic": {"owner", "reviewer"},
    "publish": {"owner", "publisher"},
}


class ApprovalService:
    def __init__(self, repository: ContentRepository):
        self.repository = repository

    def decide(self, *, decision_id: str, project_id: str, revision_id: str,
               stage: str, subject_sha256: str, status: str, actor_id: str,
               actor_role: str, checklist_version: str, created_at: str,
               target_ref: str | None = None, reason: str | None = None):
        if stage not in ROLE_BY_STAGE or actor_role not in ROLE_BY_STAGE[stage]:
            raise ValidationError("actor role cannot approve this stage")
        if status not in ("approved", "changes_requested", "rejected", "invalidated"):
            raise ValidationError("invalid approval decision")
        with self.repository.transaction() as connection:
            revision = connection.execute(
                "SELECT * FROM content_revisions WHERE id=? AND project_id=?",
                (revision_id, project_id),
            ).fetchone()
            if revision is None:
                raise NotFoundError("revision not found in project")
            expected = revision["content_sha256"]
            if stage in ("preview", "final", "animatic"):
                role = "preview" if stage in ("preview", "animatic") else "master"
                asset = connection.execute(
                    """SELECT sha256 FROM assets WHERE project_id=? AND revision_id=?
                    AND role=? ORDER BY created_at DESC LIMIT 1""",
                    (project_id, revision_id, role),
                ).fetchone()
                if asset is None:
                    raise ValidationError("approval stage has no rendered asset")
                expected = asset["sha256"]
                failed = connection.execute(
                    """SELECT 1 FROM qa_results WHERE project_id=? AND revision_id=?
                    AND stage IN ('technical',?) AND status='fail' LIMIT 1""",
                    (project_id, revision_id, stage),
                ).fetchone()
                if failed:
                    raise ValidationError("failed QA blocks approval")
            elif stage == "publish":
                package = connection.execute(
                    """SELECT package_sha256 FROM publish_packages
                    WHERE project_id=? AND revision_id=? AND package_sha256=?""",
                    (project_id, revision_id, subject_sha256),
                ).fetchone()
                if package is None or not target_ref:
                    raise ValidationError("publish approval requires exact package and target")
                expected = package["package_sha256"]
            if expected != subject_sha256:
                raise ConflictError("approval subject hash is stale")
            try:
                connection.execute(
                    """INSERT INTO approval_decisions(id,project_id,revision_id,stage,
                    subject_sha256,target_ref,status,actor_id,actor_role,checklist_version,
                    reason,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (decision_id, project_id, revision_id, stage, subject_sha256,
                     target_ref, status, actor_id, actor_role, checklist_version,
                     reason, created_at),
                )
            except sqlite3.IntegrityError as exc:
                raise ConflictError("approval identity conflict") from exc
            return connection.execute(
                "SELECT * FROM approval_decisions WHERE id=?", (decision_id,),
            ).fetchone()
