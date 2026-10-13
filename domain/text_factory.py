"""Budgeted, traceable idea-to-platform text orchestration."""

from __future__ import annotations

import hashlib
import json

from db.content_repository import ContentRepository
from domain.budget_service import BudgetContext
from domain.errors import ConflictError, NotFoundError, ValidationError
from llm.gateway import LlmTransport, StructuredLlmGateway


class TextFactory:
    def __init__(self, repository: ContentRepository, gateway: StructuredLlmGateway,
                 *, enabled: bool = False):
        self.repository = repository
        self.gateway = gateway
        self.enabled = enabled

    async def generate(self, *, output_id: str, command_id: str, idea_id: str,
                       created_by: str, created_at: str, prompt_version: str,
                       model: str, budget_context: BudgetContext,
                       upper_bound_minor: int, currency: str,
                       transport: LlmTransport):
        if not self.enabled:
            raise ValidationError("text factory feature is disabled")
        existing = self.repository.fetchone(
            "SELECT * FROM text_outputs WHERE id=?", (output_id,),
        )
        if existing:
            if existing["idea_id"] != idea_id:
                raise ConflictError("text output id belongs to another idea")
            return existing["content_sha256"]
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
        revision = self.repository.fetchone(
            "SELECT project_id FROM content_revisions WHERE id=?",
            (budget_context.revision_id,),
        ) if budget_context.revision_id else None
        if (budget_context.pilot_id != idea["pilot_id"]
                or idea["project_id"] is None
                or budget_context.project_id != idea["project_id"]
                or revision is None
                or revision["project_id"] != idea["project_id"]):
            raise ValidationError("budget context does not match the idea lineage")
        payload = {"idea_id": idea_id, "angle": idea["angle"],
                   "proof_summary": idea["proof_summary"],
                   "source_id": idea["source_id"],
                   "source_sha256": idea["source_sha256"]}
        run = await self.gateway.run(
            run_id=command_id, purpose="text_factory", prompt_name="text_factory",
            prompt_version=prompt_version, model=model, input_data=payload,
            budget_context=budget_context, upper_bound_minor=upper_bound_minor,
            currency=currency, transport=transport,
        )
        if run.status != "valid" or run.output is None:
            raise ValidationError("text factory LLM result is not valid")
        result = run.output
        required = {"master_text", "facts", "hypotheses", "source_trace", "variants"}
        if set(result) != required or set(result["variants"]) != {"telegram", "vk"}:
            raise ValidationError("structured text result has invalid shape")
        if any(item.get("source_id") != idea["source_id"] for item in result["source_trace"]):
            raise ValidationError("text output cites an unbound source")
        master_hash = hashlib.sha256(result["master_text"].encode()).hexdigest()
        with self.repository.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM text_outputs WHERE id=?", (output_id,),
            ).fetchone()
            if existing:
                if existing["idea_id"] != idea_id or existing["content_sha256"] != master_hash:
                    raise ConflictError("text output replay conflict")
                return existing["content_sha256"]
            connection.execute(
                """INSERT INTO text_outputs(id,idea_id,source_id,prompt_run_id,master_text,
                facts_json,hypotheses_json,source_trace_json,content_sha256,created_by,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (output_id, idea_id, idea["source_id"], run.run_id, result["master_text"],
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
