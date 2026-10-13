"""Traceable idea-to-master-to-platform-variant orchestration."""

from __future__ import annotations

import hashlib
import json
from typing import Protocol

from db.content_repository import ContentRepository
from domain.errors import NotFoundError, ValidationError


class StructuredTextGateway(Protocol):
    def generate(self, payload: dict) -> dict: ...


class TextFactory:
    def __init__(self, repository: ContentRepository, gateway: StructuredTextGateway):
        self.repository = repository
        self.gateway = gateway

    def generate(self, *, output_id, idea_id, created_by, created_at):
        idea = self.repository.fetchone(
            """SELECT i.*,s.full_text_ref,s.content_sha256 AS source_sha256,
            s.rights_status,s.privacy_status FROM idea_cards i JOIN sources s ON s.id=i.source_id
            WHERE i.id=?""", (idea_id,),
        )
        if idea is None:
            raise NotFoundError("idea not found")
        if idea["decision"] not in ("scored", "selected"):
            raise ValidationError("text generation requires a scored idea")
        if idea["rights_status"] != "allowed" or idea["privacy_status"] != "clear":
            raise ValidationError("source is not usable")
        payload = {"idea_id": idea_id, "angle": idea["angle"],
                   "proof_summary": idea["proof_summary"], "source_id": idea["source_id"],
                   "source_sha256": idea["source_sha256"]}
        result = self.gateway.generate(payload)
        required = {"master_text", "facts", "hypotheses", "source_trace", "variants"}
        if set(result) != required or set(result["variants"]) != {"telegram", "vk"}:
            raise ValidationError("structured text result has invalid shape")
        if any(item.get("source_id") != idea["source_id"] for item in result["source_trace"]):
            raise ValidationError("text output cites an unbound source")
        master_hash = hashlib.sha256(result["master_text"].encode()).hexdigest()
        with self.repository.transaction() as connection:
            connection.execute(
                """INSERT INTO text_outputs(id,idea_id,source_id,master_text,facts_json,
                hypotheses_json,source_trace_json,content_sha256,created_by,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (output_id, idea_id, idea["source_id"], result["master_text"],
                 json.dumps(result["facts"], sort_keys=True, ensure_ascii=False),
                 json.dumps(result["hypotheses"], sort_keys=True, ensure_ascii=False),
                 json.dumps(result["source_trace"], sort_keys=True, ensure_ascii=False),
                 master_hash, created_by, created_at),
            )
            for platform, text in sorted(result["variants"].items()):
                digest = hashlib.sha256(text.encode()).hexdigest()
                connection.execute(
                    """INSERT INTO text_variants(id,output_id,platform,text,content_sha256,created_at)
                    VALUES(?,?,?,?,?,?)""",
                    (f"{output_id}:{platform}", output_id, platform, text, digest, created_at),
                )
        return master_hash
