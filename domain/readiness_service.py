"""Evidence and human-review gate for a specific immutable revision."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from db.content_repository import ContentRepository
from domain.errors import ConflictError, NotFoundError, ValidationError


@dataclass(frozen=True)
class ReadinessResult:
    ready: bool
    blockers: tuple[str, ...]


class ReadinessService:
    def __init__(self, repository: ContentRepository):
        self.repository = repository

    def add_claim(self, claim_id, revision_id, text, location, status, hypothesis_label_location, created_at):
        if status == "hypothesis" and hypothesis_label_location is not None and not hypothesis_label_location.strip():
            raise ValidationError("hypothesis label location cannot be blank")
        try:
            with self.repository.transaction() as connection:
                connection.execute(
                    """INSERT INTO claims(id,revision_id,text,location_json,status,
                    hypothesis_label_location,created_at) VALUES(?,?,?,?,?,?,?)""",
                    (claim_id, revision_id, text, json.dumps(location, sort_keys=True, separators=(",", ":")),
                     status, hypothesis_label_location, created_at),
                )
        except sqlite3.IntegrityError as exc:
            raise ConflictError("claim identity or values conflict") from exc

    def add_evidence(
        self, evidence_id, claim_id, source_id, excerpt, asset_id, start_ms,
        end_ms, applicability_note, checked_by, checked_at, created_at,
    ):
        claim = self.repository.fetchone(
            """SELECT p.pilot_id FROM claims c
            JOIN content_revisions r ON r.id=c.revision_id
            JOIN content_projects p ON p.id=r.project_id WHERE c.id=?""", (claim_id,)
        )
        source = self.repository.fetchone("SELECT pilot_id FROM sources WHERE id=?", (source_id,))
        if claim is None or source is None:
            raise NotFoundError("claim or source not found")
        if claim["pilot_id"] != source["pilot_id"]:
            raise ValidationError("cross-pilot evidence is forbidden")
        try:
            with self.repository.transaction() as connection:
                connection.execute(
                    """INSERT INTO claim_evidence(
                    id,claim_id,source_id,excerpt,asset_id,start_ms,end_ms,
                    applicability_note,checked_by,checked_at,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (evidence_id, claim_id, source_id, excerpt, asset_id, start_ms,
                     end_ms, applicability_note, checked_by, checked_at, created_at),
                )
        except sqlite3.IntegrityError as exc:
            raise ConflictError("evidence identity or values conflict") from exc

    def add_review(
        self, review_id, project_id, revision_id, stage, subject_sha256,
        assigned_to, status, decision_by, decision_at, created_at,
    ):
        revision = self.repository.fetchone(
            "SELECT project_id FROM content_revisions WHERE id=?", (revision_id,)
        )
        if revision is None:
            raise NotFoundError(f"revision not found: {revision_id}")
        if revision["project_id"] != project_id:
            raise ValidationError("review revision belongs to another project")
        try:
            with self.repository.transaction() as connection:
                connection.execute(
                    """INSERT INTO reviews(
                    id,project_id,revision_id,stage,subject_sha256,assigned_to,status,
                    decision_by,decision_at,checklist_json,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,'{}',?)""",
                    (review_id, project_id, revision_id, stage, subject_sha256,
                     assigned_to, status, decision_by, decision_at, created_at),
                )
        except sqlite3.IntegrityError as exc:
            raise ConflictError("review identity or values conflict") from exc

    def check_production_ready(self, project_id: str, revision_id: str) -> ReadinessResult:
        revision = self.repository.fetchone(
            "SELECT project_id,content_sha256 FROM content_revisions WHERE id=?", (revision_id,)
        )
        if revision is None:
            raise NotFoundError(f"revision not found: {revision_id}")
        if revision["project_id"] != project_id:
            raise ValidationError("revision belongs to another project")
        blockers: set[str] = set()

        source_rows = self.repository.fetchall(
            """SELECT s.rights_status,s.rights_evidence_ref,s.privacy_status
            FROM content_project_sources ps JOIN sources s ON s.id=ps.source_id
            WHERE ps.project_id=?""", (project_id,),
        )
        for source in source_rows:
            if source["rights_status"] != "allowed" or not source["rights_evidence_ref"]:
                blockers.add("SOURCE_NOT_ALLOWED")
            if source["privacy_status"] != "clear":
                blockers.add("SOURCE_PRIVACY_BLOCKED")

        claims = self.repository.fetchall(
            "SELECT id,status,hypothesis_label_location FROM claims WHERE revision_id=?", (revision_id,)
        )
        project = self.repository.fetchone("SELECT pilot_id FROM content_projects WHERE id=?", (project_id,))
        for claim in claims:
            if claim["status"] == "supported":
                evidence = self.repository.fetchall(
                    """SELECT s.pilot_id FROM claim_evidence e
                    JOIN sources s ON s.id=e.source_id WHERE e.claim_id=?""", (claim["id"],),
                )
                if not evidence:
                    blockers.add("CLAIM_EVIDENCE_MISSING")
                if any(row["pilot_id"] != project["pilot_id"] for row in evidence):
                    blockers.add("CROSS_PILOT_EVIDENCE")
            elif claim["status"] == "hypothesis":
                if not claim["hypothesis_label_location"]:
                    blockers.add("HYPOTHESIS_LABEL_MISSING")
            else:
                blockers.add("CLAIM_BLOCKED")

        reviews = self.repository.fetchall(
            "SELECT status,subject_sha256 FROM reviews WHERE revision_id=? AND stage='script'",
            (revision_id,),
        )
        approved = [row for row in reviews if row["status"] == "approved"]
        matching = [row for row in approved if row["subject_sha256"] == revision["content_sha256"]]
        if not matching:
            blockers.add("SCRIPT_APPROVAL_MISSING")
        if approved and not matching:
            blockers.add("SUBJECT_HASH_MISMATCH")

        ordered = tuple(sorted(blockers))
        return ReadinessResult(not ordered, ordered)
