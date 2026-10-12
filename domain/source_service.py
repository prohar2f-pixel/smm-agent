"""Capture and verify provenance sources before content generation."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from db.content_repository import ContentRepository
from domain.errors import ConflictError, NotFoundError, ValidationError


SOURCE_KINDS = {"text", "url", "file", "voice", "build_log"}
RIGHTS = {"pending", "allowed", "denied", "revoked"}
PRIVACY = {"pending", "clear", "redaction_required"}


@dataclass(frozen=True)
class SourceRecord:
    id: str
    pilot_id: str | None
    kind: str
    original_ref: str
    content_sha256: str
    rights_status: str
    rights_evidence_ref: str | None
    privacy_status: str
    verified_by: str | None
    verified_at: str | None
    row_version: int


def _record(row) -> SourceRecord:
    return SourceRecord(**{field: row[field] for field in SourceRecord.__dataclass_fields__})


class SourceService:
    def __init__(self, repository: ContentRepository):
        self.repository = repository

    def capture_source(
        self, source_id: str, pilot_id: str, kind: str, original_ref: str,
        content_sha256: str, captured_at: str,
    ) -> SourceRecord:
        if not source_id or not original_ref or kind not in SOURCE_KINDS:
            raise ValidationError("invalid source identity, kind or reference")
        if len(content_sha256) != 64 or any(c not in "0123456789abcdef" for c in content_sha256):
            raise ValidationError("content_sha256 must be 64 lowercase hexadecimal characters")
        try:
            with self.repository.transaction() as connection:
                if connection.execute("SELECT 1 FROM pilot_passports WHERE id=?", (pilot_id,)).fetchone() is None:
                    raise NotFoundError(f"pilot not found: {pilot_id}")
                connection.execute(
                    """INSERT INTO sources(
                       id,pilot_id,kind,original_ref,captured_at,content_sha256,
                       rights_status,privacy_status,created_at,updated_at
                       ) VALUES(?,?,?,?,?,?,'pending','pending',?,?)""",
                    (source_id, pilot_id, kind, original_ref, captured_at,
                     content_sha256, captured_at, captured_at),
                )
        except sqlite3.IntegrityError as exc:
            raise ConflictError(f"source already exists or is invalid: {source_id}") from exc
        return self.get_source(source_id)

    def get_source(self, source_id: str) -> SourceRecord:
        row = self.repository.fetchone("SELECT * FROM sources WHERE id=?", (source_id,))
        if row is None:
            raise NotFoundError(f"source not found: {source_id}")
        return _record(row)

    def verify_source(
        self, source_id: str, expected_row_version: int, rights_status: str,
        rights_evidence_ref: str | None, privacy_status: str, actor_id: str,
        verified_at: str,
    ) -> SourceRecord:
        if rights_status not in RIGHTS or privacy_status not in PRIVACY:
            raise ValidationError("invalid rights or privacy status")
        if rights_status == "allowed" and (
            not rights_evidence_ref or not rights_evidence_ref.strip()
        ):
            raise ValidationError("allowed sources require rights evidence")
        if not actor_id or not actor_id.strip() or not verified_at or not verified_at.strip():
            raise ValidationError("verification actor and timestamp are required")
        with self.repository.transaction() as connection:
            cursor = connection.execute(
                """UPDATE sources SET rights_status=?,rights_evidence_ref=?,
                   privacy_status=?,verified_by=?,verified_at=?,updated_at=?,
                   row_version=row_version+1 WHERE id=? AND row_version=?""",
                (rights_status, rights_evidence_ref, privacy_status, actor_id,
                 verified_at, verified_at, source_id, expected_row_version),
            )
            if cursor.rowcount != 1:
                exists = connection.execute("SELECT 1 FROM sources WHERE id=?", (source_id,)).fetchone()
                if exists is None:
                    raise NotFoundError(f"source not found: {source_id}")
                raise ConflictError(f"stale source version: {source_id}")
        return self.get_source(source_id)

    def assert_usable(self, source_ids: list[str], pilot_id: str) -> list[SourceRecord]:
        if not source_ids:
            raise ValidationError("at least one source is required")
        records = []
        for source_id in source_ids:
            record = self.get_source(source_id)
            if record.pilot_id != pilot_id:
                raise ValidationError(f"cross-pilot source: {source_id}")
            if (
                record.rights_status != "allowed"
                or not record.rights_evidence_ref
                or not record.rights_evidence_ref.strip()
            ):
                raise ValidationError(f"source rights are not allowed: {source_id}")
            if record.privacy_status != "clear":
                raise ValidationError(f"source privacy is not clear: {source_id}")
            records.append(record)
        return records
