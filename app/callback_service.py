"""Opaque, one-use callback tokens with inbox deduplication."""

from __future__ import annotations

import hashlib
import json
import secrets

from db.content_repository import ContentRepository
from domain.errors import ConflictError, ValidationError
from domain.approval_service import ApprovalService, ROLE_BY_STAGE
from domain.time_utils import is_after


DECISION_STATUS_BY_ACTION = {
    "approve": "approved", "changes_requested": "changes_requested",
    "reject": "rejected", "invalidate": "invalidated",
}


class CallbackService:
    def __init__(self, repository: ContentRepository):
        self.repository = repository

    def issue(self, *, user_id: str, chat_id: str, action: str, subject_id: str,
              row_version: int, subject_sha256: str, expires_at: str,
              created_at: str) -> str:
        try:
            stage, verb = action.split(":", 1)
        except ValueError as exc:
            raise ValidationError("callback action must bind stage and decision") from exc
        if (stage not in ROLE_BY_STAGE or verb not in DECISION_STATUS_BY_ACTION
                or not is_after(expires_at, created_at,
                                left_field="expires_at", right_field="created_at")):
            raise ValidationError("invalid callback action or expiry")
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
                "SELECT result_ref,payload_sha256 FROM inbox_events WHERE producer=? AND event_id=?",
                (producer, event_id),
            ).fetchone()
            if duplicate:
                if duplicate["payload_sha256"] != payload_hash:
                    raise ConflictError("duplicate event payload conflict")
                return duplicate["result_ref"]
            row = connection.execute(
                "SELECT * FROM callback_actions WHERE token=?", (token,),
            ).fetchone()
            if row is None or row["consumed_at"] is not None:
                raise ConflictError("callback is invalid or already consumed")
            if not is_after(row["expires_at"], now,
                            left_field="expires_at", right_field="current time"):
                raise ConflictError("callback expired")
            if (row["user_id"] != user_id or row["chat_id"] != chat_id
                    or row["subject_sha256"] != subject_sha256
                    or row["expected_row_version"] != row_version):
                raise ValidationError("callback scope mismatch")
            self._require_current_version(connection, row)
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

    def consume_and_decide(self, *, approval_service: ApprovalService, producer: str,
                           event_id: str, token: str, user_id: str, chat_id: str,
                           subject_sha256: str, row_version: int, now: str,
                           decision: dict, correlation_id: str) -> str:
        payload = {"token": token, "user_id": user_id, "chat_id": chat_id,
                   "subject_sha256": subject_sha256, "row_version": row_version,
                   "decision": decision}
        payload_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        with self.repository.transaction() as connection:
            duplicate = connection.execute(
                "SELECT result_ref,payload_sha256 FROM inbox_events WHERE producer=? AND event_id=?",
                (producer, event_id),
            ).fetchone()
            if duplicate:
                if duplicate["payload_sha256"] != payload_hash:
                    raise ConflictError("duplicate event payload conflict")
                return duplicate["result_ref"]
            action = connection.execute(
                "SELECT * FROM callback_actions WHERE token=?", (token,),
            ).fetchone()
            if action is None or action["consumed_at"] is not None:
                raise ConflictError("callback is invalid or already consumed")
            if not is_after(action["expires_at"], now,
                            left_field="expires_at", right_field="current time"):
                raise ConflictError("callback expired")
            if (action["user_id"] != user_id or action["chat_id"] != chat_id
                    or action["subject_sha256"] != subject_sha256
                    or action["expected_row_version"] != row_version):
                raise ValidationError("callback scope mismatch")
            self._require_current_version(connection, action)
            try:
                expected_stage, verb = action["action"].split(":", 1)
            except ValueError as exc:
                raise ValidationError("callback action is not stage-bound") from exc
            expected_status = DECISION_STATUS_BY_ACTION.get(verb)
            if expected_status is None or decision.get("stage") != expected_stage:
                raise ValidationError("callback action cannot create a decision")
            if (decision.get("actor_id") != user_id
                    or decision.get("subject_sha256") != subject_sha256
                    or decision.get("revision_id") != action["subject_id"]
                    or decision.get("status") != expected_status):
                raise ValidationError("decision does not match callback authority")
            result = approval_service.decide_in_transaction(connection, **decision)
            result_ref = f"approval:{result['id']}"
            connection.execute(
                """INSERT INTO inbox_events(id,producer,event_id,payload_sha256,status,
                received_at,processed_at,result_ref) VALUES(?,?,?,?,?,?,?,?)""",
                (f"inbox-{event_id}", producer, event_id, payload_hash, "processed",
                 now, now, result_ref),
            )
            connection.execute(
                """INSERT INTO audit_events(id,actor_id,actor_role,command_id,
                aggregate_type,aggregate_id,action,after_status,row_version,correlation_id,
                created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (f"audit-{event_id}", user_id, result["actor_role"], event_id,
                 "approval_decision", result["id"], action["action"], result["status"],
                 row_version, correlation_id, now),
            )
            outbox_payload = json.dumps({"decision_id": result["id"]}, sort_keys=True)
            connection.execute(
                """INSERT INTO outbox_events(id,event_type,aggregate_id,payload_json,status,
                available_at,created_at,updated_at) VALUES(?,?,?,?,'pending',?,?,?)""",
                (f"outbox-{event_id}", "approval.decided", result["id"], outbox_payload,
                 now, now, now),
            )
            connection.execute(
                "UPDATE callback_actions SET consumed_at=?,result_ref=? WHERE token=?",
                (now, result_ref, token),
            )
            return result_ref

    @staticmethod
    def _require_current_version(connection, action) -> None:
        current = connection.execute(
            """SELECT p.row_version FROM content_revisions r
            JOIN content_projects p ON p.id=r.project_id WHERE r.id=?""",
            (action["subject_id"],),
        ).fetchone()
        if current is None or current["row_version"] != action["expected_row_version"]:
            raise ConflictError("callback aggregate version is stale")
