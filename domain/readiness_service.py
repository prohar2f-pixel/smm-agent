"""Evidence and human-review gate for a specific immutable revision."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from db.content_repository import ContentRepository
from domain.errors import ConflictError, NotFoundError, ValidationError


@dataclass(frozen=True)
class ReadinessResult:
    ready: bool
    blockers: tuple[str, ...]


class ReadinessService:
    def __init__(self, repository: ContentRepository):
        self.repository = repository

    def add_claim(
        self, claim_id, revision_id, text, location, status,
        hypothesis_label_location, created_at, checked_by=None, checked_at=None,
        valid_until=None,
    ):
        if status == "supported" and (
            not checked_by or not checked_by.strip() or not checked_at or not checked_at.strip()
        ):
            raise ValidationError("supported claim requires human check")
        if checked_at:
            datetime.fromisoformat(checked_at)
        if valid_until:
            datetime.fromisoformat(valid_until)
        if status == "hypothesis" and hypothesis_label_location is not None and not hypothesis_label_location.strip():
            raise ValidationError("hypothesis label location cannot be blank")
        try:
            with self.repository.transaction() as connection:
                if status == "supported":
                    scope = connection.execute(
                        """SELECT pp.roles_json FROM content_revisions r
                        JOIN content_projects p ON p.id=r.project_id
                        JOIN pilot_passports pp ON pp.id=p.pilot_id WHERE r.id=?""",
                        (revision_id,),
                    ).fetchone()
                    if scope is None:
                        raise NotFoundError(f"revision not found: {revision_id}")
                    reviewers = json.loads(scope["roles_json"] or "{}").get("reviewer", [])
                    if isinstance(reviewers, str):
                        reviewers = [reviewers]
                    if checked_by not in reviewers:
                        raise ValidationError("claim checker does not have reviewer role")
                connection.execute(
                    """INSERT INTO claims(id,revision_id,text,location_json,status,
                    hypothesis_label_location,checked_by,checked_at,valid_until,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (claim_id, revision_id, text, json.dumps(location, sort_keys=True, separators=(",", ":")),
                     status, hypothesis_label_location, checked_by, checked_at,
                     valid_until, created_at),
                )
        except sqlite3.IntegrityError as exc:
            raise ConflictError("claim identity or values conflict") from exc

    def add_evidence(
        self, evidence_id, claim_id, source_id, excerpt, asset_id, start_ms,
        end_ms, applicability_note, checked_by, checked_at, created_at,
    ):
        if not excerpt and not (asset_id and start_ms is not None and end_ms is not None):
            raise ValidationError("evidence requires excerpt or an addressed asset range")
        if not checked_by or not checked_by.strip() or not checked_at:
            raise ValidationError("evidence checker and timestamp are required")
        datetime.fromisoformat(checked_at)
        try:
            with self.repository.transaction() as connection:
                claim = connection.execute(
                    """SELECT p.id AS project_id,p.pilot_id FROM claims c
                    JOIN content_revisions r ON r.id=c.revision_id
                    JOIN content_projects p ON p.id=r.project_id WHERE c.id=?""",
                    (claim_id,),
                ).fetchone()
                source = connection.execute(
                    "SELECT pilot_id,rights_status,rights_evidence_ref,privacy_status FROM sources WHERE id=?",
                    (source_id,),
                ).fetchone()
                if claim is None or source is None:
                    raise NotFoundError("claim or source not found")
                if claim["pilot_id"] != source["pilot_id"]:
                    raise ValidationError("cross-pilot evidence is forbidden")
                linked = connection.execute(
                    "SELECT 1 FROM content_project_sources WHERE project_id=? AND source_id=?",
                    (claim["project_id"], source_id),
                ).fetchone()
                if linked is None:
                    raise ValidationError("evidence source is outside project")
                if (
                    source["rights_status"] != "allowed"
                    or not source["rights_evidence_ref"]
                    or not source["rights_evidence_ref"].strip()
                    or source["privacy_status"] != "clear"
                ):
                    raise ValidationError("evidence source is not usable")
                if asset_id:
                    asset = connection.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
                    if asset is None:
                        raise NotFoundError(f"asset not found: {asset_id}")
                    if (
                        asset["project_id"] != claim["project_id"]
                        or asset["rights_status"] != "allowed"
                        or asset["privacy_status"] != "clear"
                    ):
                        raise ValidationError("evidence asset is outside scope or unusable")
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
        checklist_version,
    ):
        revision = self.repository.fetchone(
            """SELECT r.project_id,pp.roles_json FROM content_revisions r
            JOIN content_projects p ON p.id=r.project_id
            JOIN pilot_passports pp ON pp.id=p.pilot_id WHERE r.id=?""", (revision_id,)
        )
        if revision is None:
            raise NotFoundError(f"revision not found: {revision_id}")
        if revision["project_id"] != project_id:
            raise ValidationError("review revision belongs to another project")
        if status == "approved":
            if not decision_by or not decision_at or not checklist_version:
                raise ValidationError("approval requires actor, timestamp and checklist version")
            reviewers = json.loads(revision["roles_json"] or "{}").get("reviewer", [])
            if isinstance(reviewers, str):
                reviewers = [reviewers]
            if decision_by not in reviewers:
                raise ValidationError("decision actor does not have reviewer role")
        try:
            with self.repository.transaction() as connection:
                connection.execute(
                    """INSERT INTO reviews(
                    id,project_id,revision_id,stage,subject_sha256,assigned_to,status,
                    decision_by,decision_at,checklist_json,created_at,checklist_version
                    ) VALUES(?,?,?,?,?,?,?,?,?,'{}',?,?)""",
                    (review_id, project_id, revision_id, stage, subject_sha256,
                     assigned_to, status, decision_by, decision_at, created_at,
                     checklist_version),
                )
        except sqlite3.IntegrityError as exc:
            raise ConflictError("review identity or values conflict") from exc

    def check_production_ready(
        self, project_id: str, revision_id: str, checked_at: str | None = None,
    ) -> ReadinessResult:
        with self.repository.transaction() as connection:
            return self.check_production_ready_in_transaction(
                connection, project_id, revision_id, checked_at
            )

    def check_production_ready_in_transaction(
        self, connection, project_id: str, revision_id: str,
        checked_at: str | None = None,
    ) -> ReadinessResult:
        now = datetime.fromisoformat(checked_at) if checked_at else datetime.now(timezone.utc)
        blockers: set[str] = set()
        revision = connection.execute(
            "SELECT project_id,content_sha256 FROM content_revisions WHERE id=?", (revision_id,)
        ).fetchone()
        if revision is None:
            raise NotFoundError(f"revision not found: {revision_id}")
        if revision["project_id"] != project_id:
            raise ValidationError("revision belongs to another project")
        project = connection.execute(
            "SELECT p.pilot_id,pp.roles_json FROM content_projects p JOIN pilot_passports pp ON pp.id=p.pilot_id WHERE p.id=?",
            (project_id,),
        ).fetchone()
        source_rows = connection.execute(
            """SELECT s.rights_status,s.rights_evidence_ref,s.privacy_status
            FROM content_project_sources ps JOIN sources s ON s.id=ps.source_id
            WHERE ps.project_id=?""", (project_id,),
        ).fetchall()
        for source in source_rows:
            evidence_ref = source["rights_evidence_ref"]
            if source["rights_status"] != "allowed" or not evidence_ref or not evidence_ref.strip():
                blockers.add("SOURCE_NOT_ALLOWED")
            if source["privacy_status"] != "clear":
                blockers.add("SOURCE_PRIVACY_BLOCKED")

        claims = connection.execute(
            "SELECT * FROM claims WHERE revision_id=?", (revision_id,)
        ).fetchall()
        for claim in claims:
            if claim["status"] == "supported":
                if not claim["checked_by"] or not claim["checked_at"]:
                    blockers.add("CLAIM_BLOCKED")
                if claim["valid_until"] and datetime.fromisoformat(claim["valid_until"]) < now:
                    blockers.add("CLAIM_BLOCKED")
                evidence = connection.execute(
                    """SELECT e.excerpt,e.start_ms,e.end_ms,s.pilot_id,
                    s.rights_status,s.rights_evidence_ref,s.privacy_status,
                    ps.project_id AS linked_project,
                    a.project_id AS asset_project,a.rights_status AS asset_rights,
                    a.privacy_status AS asset_privacy
                    FROM claim_evidence e JOIN sources s ON s.id=e.source_id
                    LEFT JOIN content_project_sources ps
                      ON ps.source_id=e.source_id AND ps.project_id=?
                    LEFT JOIN assets a ON a.id=e.asset_id WHERE e.claim_id=?""",
                    (project_id, claim["id"]),
                ).fetchall()
                usable = []
                for row in evidence:
                    if row["pilot_id"] != project["pilot_id"]:
                        blockers.add("CROSS_PILOT_EVIDENCE")
                        continue
                    if (
                        row["rights_status"] != "allowed"
                        or not row["rights_evidence_ref"]
                        or not row["rights_evidence_ref"].strip()
                        or row["privacy_status"] != "clear"
                        or row["linked_project"] is None
                    ):
                        continue
                    if row["asset_project"] is not None and (
                        row["asset_project"] != project_id
                        or row["asset_rights"] != "allowed"
                        or row["asset_privacy"] != "clear"
                    ):
                        continue
                    usable.append(row)
                if not usable:
                    blockers.add("CLAIM_EVIDENCE_MISSING")
            elif claim["status"] == "hypothesis":
                if not claim["hypothesis_label_location"]:
                    blockers.add("HYPOTHESIS_LABEL_MISSING")
            else:
                blockers.add("CLAIM_BLOCKED")

        review = connection.execute(
            """SELECT * FROM reviews WHERE revision_id=? AND stage='script'
            ORDER BY created_at DESC,rowid DESC LIMIT 1""", (revision_id,),
        ).fetchone()
        reviewers = json.loads(project["roles_json"] or "{}").get("reviewer", [])
        if isinstance(reviewers, str):
            reviewers = [reviewers]
        valid_approval = bool(
            review and review["status"] == "approved"
            and review["decision_by"] in reviewers and review["decision_at"]
            and review["checklist_version"]
            and review["subject_sha256"] == revision["content_sha256"]
        )
        if not valid_approval:
            blockers.add("SCRIPT_APPROVAL_MISSING")
        if review and review["status"] == "approved" and review["subject_sha256"] != revision["content_sha256"]:
            blockers.add("SUBJECT_HASH_MISMATCH")

        ordered = tuple(sorted(blockers))
        return ReadinessResult(not ordered, ordered)
