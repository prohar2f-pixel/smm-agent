"""Opaque, one-use callback tokens with inbox deduplication."""

from __future__ import annotations

import hashlib
import secrets

from db.content_repository import ContentRepository
from domain.errors import ConflictError, ValidationError


class CallbackService:
    def __init__(self, repository: ContentRepository):
        self.repository = repository

    def issue(self, *, user_id: str, chat_id: str, action: str, subject_id: str,
              row_version: int, subject_sha256: str, expires_at: str,
              created_at: str) -> str:
        token = secrets.token_urlsafe(24)
        with self.repository.transaction() as connection:
            connection.execute(
                """INSERT INTO callback_actions(token,user_id,chat_id,action,subject_id,
                expected_row_version,subject_sha256,expires_at,created_at)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                (token, user_id, chat_id, action, subject_id, row_version,
                 subject_sha256, expires_at, created_at),
            )
        return token

    def consume(self, *, producer: str, event_id: str, token: str, user_id: str,
                chat_id: str, subject_sha256: str, row_version: int, now: str) -> str:
        payload_hash = hashlib.sha256(
            f"{token}:{user_id}:{chat_id}:{subject_sha256}:{row_version}".encode()
        ).hexdigest()
        with self.repository.transaction() as connection:
            duplicate = connection.execute(
                "SELECT result_ref FROM inbox_events WHERE producer=? AND event_id=?",
                (producer, event_id),
            ).fetchone()
            if duplicate:
                return duplicate["result_ref"]
            row = connection.execute(
                "SELECT * FROM callback_actions WHERE token=?", (token,),
            ).fetchone()
            if row is None or row["consumed_at"] is not None:
                raise ConflictError("callback is invalid or already consumed")
            if row["expires_at"] < now:
                raise ConflictError("callback expired")
            if (row["user_id"] != user_id or row["chat_id"] != chat_id
                    or row["subject_sha256"] != subject_sha256
                    or row["expected_row_version"] != row_version):
                raise ValidationError("callback scope mismatch")
            result_ref = f"callback:{token}"
            connection.execute(
                """INSERT INTO inbox_events(id,producer,event_id,payload_sha256,status,
                received_at,processed_at,result_ref) VALUES(?,?,?,?,?,?,?,?)""",
                (f"inbox-{event_id}", producer, event_id, payload_hash, "processed",
                 now, now, result_ref),
            )
            connection.execute(
                "UPDATE callback_actions SET consumed_at=?,result_ref=? WHERE token=?",
                (now, result_ref, token),
            )
            return result_ref
