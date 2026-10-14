"""Immutable, exact-hash human approval decisions."""

from __future__ import annotations

import json
import sqlite3

from db.content_repository import ContentRepository
from domain.errors import ConflictError, NotFoundError, ValidationError
from domain.package_hash import canonical_package_hash
from domain.time_utils import canonical_instant, is_after

ROLE_BY_STAGE = {
    "script": {"owner", "reviewer"}, "preview": {"owner", "reviewer"},
    "final": {"owner", "reviewer"}, "animatic": {"owner", "reviewer"},
    "publish": {"owner", "publisher"},
}
FINAL_QA_CODES = {
    "source_traceable", "rights_allowed", "privacy_clear",
    "claims_supported_or_labeled", "script_approved", "duration_in_profile",
    "media_readable", "profile_matches", "subtitles_match_voice",
    "text_legible_safe_zones", "audio_clear", "cta_single_and_working",
    "no_critical_artifacts", "package_hash_verified",
}


class ApprovalService:
    def __init__(self, repository: ContentRepository):
        self.repository = repository

    def decide(self, **decision):
        with self.repository.transaction() as connection:
            return self.decide_in_transaction(connection, **decision)

    def record_qa(self, *, qa_id: str, project_id: str, revision_id: str,
                  asset_id: str, check_code: str, status: str, actor_id: str,
                  actor_role: str, created_at: str, details: dict | None = None):
        if check_code not in FINAL_QA_CODES or status not in ("pass", "fail", "not_applicable"):
            raise ValidationError("invalid QA result")
        with self.repository.transaction() as connection:
            row = connection.execute(
                """SELECT p.current_revision_id,p.owner_id,pp.roles_json
                FROM assets a JOIN content_projects p ON p.id=a.project_id
                JOIN pilot_passports pp ON pp.id=p.pilot_id
                WHERE a.id=? AND a.project_id=? AND a.revision_id=?""",
                (asset_id, project_id, revision_id),
            ).fetchone()
            if row is None or row["current_revision_id"] != revision_id:
                raise ConflictError("QA requires an exact current-revision asset")
            roles = json.loads(row["roles_json"] or "{}")
            reviewers = set(roles.get("reviewer", []))
            if actor_id == row["owner_id"]:
                reviewers.add(actor_id)
            if actor_role not in ("owner", "reviewer") or actor_id not in reviewers:
                raise ValidationError("actor is not assigned to QA review")
            try:
                connection.execute(
                    """INSERT INTO qa_results(id,project_id,revision_id,asset_id,stage,
                    check_code,status,details_json,created_by,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (qa_id, project_id, revision_id, asset_id, "final", check_code,
                     status, json.dumps(details or {}, sort_keys=True), actor_id, created_at),
                )
            except sqlite3.IntegrityError as exc:
                raise ConflictError("QA result identity conflict") from exc
            return connection.execute("SELECT * FROM qa_results WHERE id=?", (qa_id,)).fetchone()

    def decide_in_transaction(self, connection, *, decision_id: str,
                              project_id: str, revision_id: str, stage: str,
                              subject_sha256: str, status: str, actor_id: str,
                              actor_role: str, checklist_version: str,
                              created_at: str, target_ref: str | None = None,
                              platform: str | None = None, reason: str | None = None,
                              expires_at: str | None = None):
        created_at = canonical_instant(created_at, "created_at")
        expires_at = canonical_instant(expires_at, "expires_at") if expires_at else None
        if stage not in ROLE_BY_STAGE:
            raise ValidationError("unknown approval stage")
        if status not in ("approved", "changes_requested", "rejected", "invalidated"):
            raise ValidationError("invalid approval decision")
        revision = connection.execute(
            """SELECT r.*,p.current_revision_id,p.owner_id,p.pilot_id,pp.roles_json
            FROM content_revisions r JOIN content_projects p ON p.id=r.project_id
            JOIN pilot_passports pp ON pp.id=p.pilot_id
            WHERE r.id=? AND r.project_id=?""", (revision_id, project_id),
        ).fetchone()
        if revision is None:
            raise NotFoundError("revision not found in project")
        if revision["current_revision_id"] != revision_id:
            raise ConflictError("approval requires the current project revision")
        roles = json.loads(revision["roles_json"] or "{}")
        actor_roles = {name for name, actors in roles.items()
                       if isinstance(actors, list) and actor_id in actors}
        if actor_id == revision["owner_id"]:
            actor_roles.add("owner")
        if actor_role not in actor_roles or actor_role not in ROLE_BY_STAGE[stage]:
            raise ValidationError("actor is not assigned to this approval role")

        expected = revision["content_sha256"]
        if stage in ("preview", "final", "animatic"):
            role = "preview" if stage in ("preview", "animatic") else "master"
            asset = connection.execute(
                """SELECT id,sha256 FROM assets WHERE project_id=? AND revision_id=?
                AND role=? ORDER BY created_at DESC,id DESC LIMIT 1""",
                (project_id, revision_id, role),
            ).fetchone()
            if asset is None:
                raise ValidationError("approval stage has no rendered asset")
            expected = asset["sha256"]
            results = connection.execute(
                """SELECT check_code,status FROM qa_results WHERE project_id=? AND revision_id=?
                AND asset_id=? AND stage IN ('technical',?)""",
                (project_id, revision_id, asset["id"], stage),
            ).fetchall()
            if (status == "approved"
                    and (not results or any(row["status"] != "pass" for row in results))):
                raise ValidationError("positive QA evidence is required")
            if (stage == "final" and status == "approved"
                    and not FINAL_QA_CODES.issubset(
                        {row["check_code"] for row in results if row["status"] == "pass"})):
                raise ValidationError("final approval requires the complete QA rubric")
        elif stage == "publish":
            if platform not in ("telegram", "vk", "manual") or not target_ref:
                raise ValidationError("publish approval requires platform and target")
            if not expires_at or not is_after(
                    expires_at, created_at,
                    left_field="expires_at", right_field="created_at"):
                raise ValidationError("publish approval requires a future expiry")
            package = connection.execute(
                """SELECT package_sha256,caption_text,assets_json FROM publish_packages
                WHERE project_id=? AND revision_id=? AND package_sha256=?""",
                (project_id, revision_id, subject_sha256),
            ).fetchone()
            if package is None:
                raise ValidationError("publish approval requires exact package")
            if package["package_sha256"] != canonical_package_hash(
                    package["caption_text"], package["assets_json"]):
                raise ConflictError("publish package hash mismatch")
            expected = package["package_sha256"]
            final = connection.execute(
                """SELECT ad.status,a.id AS asset_id FROM approval_decisions ad JOIN assets a
                ON a.sha256=ad.subject_sha256 AND a.project_id=ad.project_id
                AND a.revision_id=ad.revision_id AND a.role='master'
                WHERE ad.project_id=? AND ad.revision_id=? AND ad.stage='final'
                ORDER BY ad.created_at DESC,ad.id DESC LIMIT 1""",
                (project_id, revision_id),
            ).fetchone()
            if final is None or final["status"] != "approved":
                raise ValidationError("publish approval requires current final approval")
            if final["asset_id"] not in json.loads(package["assets_json"]):
                raise ValidationError("publish package does not contain the approved master")
        if expected != subject_sha256:
            raise ConflictError("approval subject hash is stale")
        try:
            connection.execute(
                """INSERT INTO approval_decisions(id,project_id,revision_id,stage,
                subject_sha256,platform,target_ref,status,actor_id,actor_role,
                checklist_version,reason,expires_at,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (decision_id, project_id, revision_id, stage, subject_sha256,
                 platform, target_ref, status, actor_id, actor_role,
                 checklist_version, reason, expires_at, created_at),
            )
        except sqlite3.IntegrityError as exc:
            raise ConflictError("approval identity conflict") from exc
        return connection.execute(
            "SELECT * FROM approval_decisions WHERE id=?", (decision_id,),
        ).fetchone()
