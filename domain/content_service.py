"""Immutable content projects, briefs and revisions."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from db.content_repository import ContentRepository
from domain.errors import ConflictError, NotFoundError, ValidationError
from domain.source_service import SourceService


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ProjectRecord:
    id: str
    pilot_id: str
    title: str
    status: str
    owner_id: str


@dataclass(frozen=True)
class BriefRecord:
    id: str
    project_id: str
    brief_no: int
    content_sha256: str


@dataclass(frozen=True)
class RevisionRecord:
    id: str
    project_id: str
    brief_id: str
    revision_no: int
    content_sha256: str


class ContentService:
    def __init__(self, repository: ContentRepository, sources: SourceService):
        self.repository = repository
        self.sources = sources

    def create_project(self, project_id, pilot_id, title, owner_id, source_ids, created_at):
        if not project_id or not title or not owner_id:
            raise ValidationError("project identity, title and owner are required")
        self.sources.assert_usable(source_ids, pilot_id)
        try:
            with self.repository.transaction() as connection:
                self._assert_sources_usable(connection, source_ids, pilot_id)
                connection.execute(
                    """INSERT INTO content_projects(
                       id,pilot_id,title,status,owner_id,created_at,updated_at
                       ) VALUES(?,?,?,'captured',?,?,?)""",
                    (project_id, pilot_id, title, owner_id, created_at, created_at),
                )
                connection.executemany(
                    "INSERT INTO content_project_sources(project_id,source_id,created_at) VALUES(?,?,?)",
                    [(project_id, source_id, created_at) for source_id in dict.fromkeys(source_ids)],
                )
        except sqlite3.IntegrityError as exc:
            raise ConflictError(f"project already exists or is invalid: {project_id}") from exc
        return ProjectRecord(project_id, pilot_id, title, "captured", owner_id)

    def create_brief(
        self, brief_id, project_id, brief_no, audience, goal, offer, angle,
        cta_text, platform, target_ref, duration_min_sec, duration_max_sec,
        constraints, prompt_version, proof_source_ids, created_by, created_at,
        expected_project_row_version,
    ):
        if not cta_text.strip() or not proof_source_ids:
            raise ValidationError("brief requires CTA and proof sources")
        project = self.repository.fetchone("SELECT * FROM content_projects WHERE id=?", (project_id,))
        if project is None:
            raise NotFoundError(f"project not found: {project_id}")
        self.sources.assert_usable(proof_source_ids, project["pilot_id"])
        placeholders = ",".join("?" for _ in proof_source_ids)
        linked = self.repository.fetchall(
            f"SELECT source_id FROM content_project_sources WHERE project_id=? AND source_id IN ({placeholders})",
            (project_id, *proof_source_ids),
        )
        if {row["source_id"] for row in linked} != set(proof_source_ids):
            raise ValidationError("proof source is outside project")
        payload = {
            "audience": audience, "goal": goal, "offer": offer, "angle": angle,
            "cta_text": cta_text, "platform": platform, "target_ref": target_ref,
            "duration_min_sec": duration_min_sec, "duration_max_sec": duration_max_sec,
            "constraints": constraints, "prompt_version": prompt_version,
            "proof_source_ids": sorted(set(proof_source_ids)),
        }
        digest = canonical_sha256(payload)
        try:
            with self.repository.transaction() as connection:
                self._assert_sources_usable(connection, proof_source_ids, project["pilot_id"])
                connection.execute(
                    """INSERT INTO content_briefs(
                    id,project_id,brief_no,audience,goal,offer,angle,cta_text,platform,
                    target_ref,duration_min_sec,duration_max_sec,constraints_json,
                    prompt_version,content_sha256,created_by,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (brief_id, project_id, brief_no, audience, goal, offer, angle,
                     cta_text, platform, target_ref, duration_min_sec, duration_max_sec,
                     _canonical_json(constraints), prompt_version, digest, created_by, created_at),
                )
                connection.executemany(
                    "INSERT INTO content_brief_sources(brief_id,source_id,created_at) VALUES(?,?,?)",
                    [(brief_id, item, created_at) for item in dict.fromkeys(proof_source_ids)],
                )
                cursor = connection.execute(
                    "UPDATE content_projects SET current_brief_id=?,status='brief_ready',updated_at=?,row_version=row_version+1 WHERE id=? AND row_version=?",
                    (brief_id, created_at, project_id, expected_project_row_version),
                )
                if cursor.rowcount != 1:
                    raise ConflictError("stale project version")
        except sqlite3.IntegrityError as exc:
            raise ConflictError("brief number or identity conflicts") from exc
        return BriefRecord(brief_id, project_id, brief_no, digest)

    def create_revision(
        self, revision_id, project_id, brief_id, revision_no, parent_revision_id,
        script_text, caption_text, timeline, prompt_versions, created_by, created_at,
        expected_project_row_version,
    ):
        brief = self.repository.fetchone(
            "SELECT project_id FROM content_briefs WHERE id=?", (brief_id,)
        )
        if brief is None:
            raise NotFoundError(f"brief not found: {brief_id}")
        if brief["project_id"] != project_id:
            raise ValidationError("brief belongs to another project")
        if parent_revision_id:
            parent = self.repository.fetchone(
                "SELECT project_id,revision_no FROM content_revisions WHERE id=?", (parent_revision_id,)
            )
            if parent is None or parent["project_id"] != project_id:
                raise ValidationError("parent revision belongs to another project")
            if parent["revision_no"] != revision_no - 1:
                raise ValidationError("parent must be the preceding revision")
        elif revision_no != 1:
            raise ValidationError("revision after the first requires a parent")
        payload = {
            "brief_id": brief_id, "parent_revision_id": parent_revision_id,
            "script_text": script_text, "caption_text": caption_text,
            "timeline": timeline, "prompt_versions": prompt_versions,
        }
        digest = canonical_sha256(payload)
        try:
            with self.repository.transaction() as connection:
                connection.execute(
                    """INSERT INTO content_revisions(
                    id,project_id,brief_id,revision_no,parent_revision_id,script_text,
                    caption_text,timeline_json,prompt_versions_json,content_sha256,
                    created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (revision_id, project_id, brief_id, revision_no, parent_revision_id,
                     script_text, caption_text, _canonical_json(timeline),
                     _canonical_json(prompt_versions), digest, created_by, created_at),
                )
                cursor = connection.execute(
                    "UPDATE content_projects SET current_revision_id=?,status='script_review',updated_at=?,row_version=row_version+1 WHERE id=? AND row_version=?",
                    (revision_id, created_at, project_id, expected_project_row_version),
                )
                if cursor.rowcount != 1:
                    raise ConflictError("stale project version")
        except sqlite3.IntegrityError as exc:
            raise ConflictError("revision number or identity conflicts") from exc
        return RevisionRecord(revision_id, project_id, brief_id, revision_no, digest)

    @staticmethod
    def _assert_sources_usable(connection, source_ids, pilot_id):
        for source_id in source_ids:
            row = connection.execute(
                "SELECT pilot_id,rights_status,rights_evidence_ref,privacy_status FROM sources WHERE id=?",
                (source_id,),
            ).fetchone()
            if row is None:
                raise NotFoundError(f"source not found: {source_id}")
            if row["pilot_id"] != pilot_id:
                raise ValidationError(f"cross-pilot source: {source_id}")
            evidence = row["rights_evidence_ref"]
            if row["rights_status"] != "allowed" or not evidence or not evidence.strip():
                raise ValidationError(f"source rights are not allowed: {source_id}")
            if row["privacy_status"] != "clear":
                raise ValidationError(f"source privacy is not clear: {source_id}")
