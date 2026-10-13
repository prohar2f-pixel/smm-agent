"""Fail-closed orchestration for budgeted, structured LLM calls."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol

from db.content_repository import ContentRepository
from domain.budget_service import BudgetContext, BudgetService
from domain.cost_service import CostService, ReconciliationRequiredError
from domain.errors import ConflictError, ValidationError
from llm.prompt_registry import PromptRegistry
from llm.validators import StructuredOutputValidator, StructuredValidationError


@dataclass(frozen=True)
class TransportResponse:
    text: str
    input_tokens: int
    output_tokens: int
    cost_minor: int
    currency: str
    model: str
    latency_ms: int
    evidence_id: str
    output_ref: str


class LlmTransport(Protocol):
    provider_key: str

    def complete(self, **request: Any) -> Awaitable[TransportResponse]: ...


@dataclass(frozen=True)
class LlmRunResult:
    run_id: str
    status: str
    output: dict[str, Any] | None
    attempt_count: int
    cost_minor: int
    error_code: str | None


class StructuredLlmGateway:
    def __init__(
        self,
        repository: ContentRepository,
        registry: PromptRegistry,
        budgets: BudgetService,
        costs: CostService,
        *,
        pricing: dict[str, dict[str, Any]],
        now: Callable[[], str],
        validator: StructuredOutputValidator | None = None,
    ):
        self.repository = repository
        self.registry = registry
        self.budgets = budgets
        self.costs = costs
        self.pricing = pricing
        self.now = now
        self.validator = validator or StructuredOutputValidator()

    @staticmethod
    def _canonical(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @classmethod
    def canonical_input_hash(cls, input_data: dict[str, Any]) -> str:
        return hashlib.sha256(cls._canonical(input_data).encode("utf-8")).hexdigest()

    async def run(
        self,
        run_id: str,
        purpose: str,
        prompt_name: str,
        prompt_version: str,
        model: str,
        input_data: dict[str, Any],
        budget_context: BudgetContext,
        upper_bound_minor: int,
        currency: str,
        transport: LlmTransport,
    ) -> LlmRunResult:
        existing = self._existing(run_id)
        if existing is not None:
            return existing
        self._guard_configuration(model, currency, upper_bound_minor, input_data)
        prompt = self.registry.get(prompt_name, prompt_version)
        input_hash = self.canonical_input_hash(input_data)
        request_hash = self.canonical_input_hash({
            "purpose": purpose, "prompt": [prompt_name, prompt_version], "model": model,
            "input": input_data, "currency": currency, "upper_bound_minor": upper_bound_minor,
        })
        operation_id = f"operation:{run_id}"
        reservation_id = f"reservation:{run_id}"
        job_id = f"job:{run_id}"
        self.budgets.reserve(
            run_id, reservation_id, operation_id, job_id, budget_context,
            upper_bound_minor, currency, request_hash, transport.provider_key, self.now(),
        )
        self._mark_submitting(operation_id, job_id)

        input_json = self._canonical(input_data)
        user = prompt.user_template.replace("{input_json}", input_json)
        responses: list[TransportResponse] = []
        output: dict[str, Any] | None = None
        error_code: str | None = None
        raw_hash: str | None = None
        output_ref: str | None = None

        for attempt in (1, 2):
            if attempt == 2:
                user = (
                    "Return only JSON matching the schema. Do not add Markdown or commentary.\n"
                    f"Schema: {self._canonical(prompt.output_schema)}"
                )
            try:
                response = await transport.complete(
                    system=prompt.system_template, user=user, model=model,
                    idempotency_key=f"{run_id}:{attempt}", attempt=attempt,
                )
            except Exception as exc:
                code = "transport_timeout" if isinstance(exc, TimeoutError) else "transport_error"
                self._mark_unknown(operation_id, job_id, code)
                raise ReconciliationRequiredError(
                    f"provider outcome is unknown for {run_id}: {code}"
                ) from exc
            try:
                self._validate_response_metadata(response, model, currency)
            except ReconciliationRequiredError:
                self._mark_unknown(operation_id, job_id, "provider_evidence_invalid")
                raise
            responses.append(response)
            raw_hash = hashlib.sha256(response.text.encode("utf-8")).hexdigest()
            output_ref = response.output_ref
            try:
                output = self.validator.parse(response.text, prompt.output_schema)
                break
            except StructuredValidationError:
                error_code = "structured_output_invalid"

        status = "valid" if output is not None else "invalid"
        total_cost = sum(item.cost_minor for item in responses)
        charged = [(index, item) for index, item in enumerate(responses) if item.cost_minor > 0]
        for charged_index, (index, item) in enumerate(charged):
            final = charged_index == len(charged) - 1
            self.costs.record_charge(
                f"cost:{run_id}:{index + 1}", operation_id, reservation_id,
                transport.provider_key, item.evidence_id, item.cost_minor, currency,
                self.now(), final=final,
            )
        if not charged:
            evidence = ",".join(item.evidence_id for item in responses)
            self.costs.mark_no_charge(operation_id, f"provider:{evidence}", self.now())
        self._persist_final(
            run_id, operation_id, job_id, purpose, prompt_name, prompt_version,
            model, input_hash, raw_hash, status, len(responses), responses,
            total_cost, currency, output_ref, error_code, output,
        )
        return LlmRunResult(run_id, status, output, len(responses), total_cost, error_code)

    def link_revision(self, link_id: str, revision_id: str, prompt_run_id: str, created_at: str) -> None:
        with self.repository.transaction() as connection:
            run = connection.execute(
                """SELECT p.validation_status,p.input_sha256,p.output_sha256,j.revision_id
                   FROM prompt_runs p JOIN jobs j ON j.operation_id=p.operation_id
                   WHERE p.id=?""", (prompt_run_id,),
            ).fetchone()
            if run is None or run["validation_status"] != "valid":
                raise ValidationError("only a valid prompt run can be linked")
            if run["revision_id"] != revision_id or not run["input_sha256"] or not run["output_sha256"]:
                raise ValidationError("prompt run does not belong to the exact revision")
            try:
                connection.execute(
                    "INSERT INTO revision_prompt_runs(id,revision_id,prompt_run_id,created_at) VALUES(?,?,?,?)",
                    (link_id, revision_id, prompt_run_id, created_at),
                )
            except sqlite3.IntegrityError as exc:
                raise ConflictError("prompt run is already linked or link identity conflicts") from exc

    def _guard_configuration(self, model, currency, upper_bound, input_data):
        if not isinstance(input_data, dict):
            raise ValidationError("input_data must be an object")
        if not isinstance(upper_bound, int) or upper_bound <= 0:
            raise ValidationError("positive upper bound is required")
        price = self.pricing.get(model)
        if price is None or price.get("currency") != currency:
            raise ValidationError("model price is missing for requested currency")

    def _existing(self, run_id: str) -> LlmRunResult | None:
        row = self.repository.fetchone(
            """SELECT p.*,o.result_json FROM prompt_runs p
               JOIN external_operations o ON o.id=p.operation_id WHERE p.command_id=?""",
            (run_id,),
        )
        if row is not None:
            stored = json.loads(row["result_json"]) if row["result_json"] else None
            output = stored.get("output") if isinstance(stored, dict) and "responses" in stored else stored
            return LlmRunResult(
                row["id"], row["validation_status"], output, row["attempt_count"],
                row["cost_minor"] or 0, row["error_code"],
            )
        operation = self.repository.fetchone(
            "SELECT status FROM external_operations WHERE command_id=?", (run_id,),
        )
        if operation is not None:
            raise ReconciliationRequiredError(
                f"run {run_id} is in-flight or requires reconciliation"
            )
        return None

    def _mark_submitting(self, operation_id: str, job_id: str) -> None:
        at = self.now()
        with self.repository.transaction() as connection:
            cursor = connection.execute(
                """UPDATE external_operations SET status='submitting',updated_at=?,
                   row_version=row_version+1 WHERE id=? AND status='intent'""",
                (at, operation_id),
            )
            if cursor.rowcount != 1:
                raise ReconciliationRequiredError("operation is already in-flight")
            connection.execute(
                """UPDATE jobs SET status='running',attempt_count=1,external_intent_at=?,
                   updated_at=?,row_version=row_version+1 WHERE id=?""", (at, at, job_id),
            )

    def _mark_unknown(self, operation_id: str, job_id: str, error_code: str) -> None:
        self.costs.mark_unknown(operation_id, self.now())
        with self.repository.transaction() as connection:
            connection.execute(
                "UPDATE external_operations SET error_code=? WHERE id=?", (error_code, operation_id),
            )
            connection.execute(
                """UPDATE jobs SET status='unknown_outcome',error_code=?,updated_at=?,
                   row_version=row_version+1 WHERE id=?""", (error_code, self.now(), job_id),
            )

    @staticmethod
    def _validate_response_metadata(response: TransportResponse, model: str, currency: str) -> None:
        if response.model != model or response.currency != currency:
            raise ReconciliationRequiredError("provider response model or currency mismatch")
        numeric = (response.input_tokens, response.output_tokens, response.cost_minor, response.latency_ms)
        if any(not isinstance(value, int) or value < 0 for value in numeric):
            raise ReconciliationRequiredError("provider response usage is invalid")
        if not response.evidence_id or not response.output_ref:
            raise ReconciliationRequiredError("provider evidence is incomplete")

    def _persist_final(
        self, run_id, operation_id, job_id, purpose, prompt_name, prompt_version,
        model, input_hash, output_hash, status, attempts, responses,
        total_cost, currency, output_ref, error_code, output,
    ):
        at = self.now()
        input_tokens = sum(item.input_tokens for item in responses)
        output_tokens = sum(item.output_tokens for item in responses)
        latency = sum(item.latency_ms for item in responses)
        response_evidence = [
            {
                "sha256": hashlib.sha256(item.text.encode("utf-8")).hexdigest(),
                "ref": item.output_ref,
            }
            for item in responses
        ]
        result_json = self._canonical({"output": output, "responses": response_evidence})
        job_result_json = self._canonical(output) if output is not None else None
        with self.repository.transaction() as connection:
            connection.execute(
                """INSERT INTO prompt_runs(id,command_id,operation_id,purpose,prompt_name,
                   prompt_version,model,input_sha256,output_sha256,validation_status,attempt_count,
                   input_tokens,output_tokens,latency_ms,cost_minor,currency,output_ref,error_code,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (run_id, run_id, operation_id, purpose, prompt_name, prompt_version, model,
                 input_hash, output_hash, status, attempts, input_tokens, output_tokens,
                 latency, total_cost, currency, output_ref, error_code, at),
            )
            connection.execute(
                """UPDATE external_operations SET status=?,result_json=?,result_sha256=?,
                   error_code=?,updated_at=?,row_version=row_version+1 WHERE id=?""",
                ("succeeded" if status == "valid" else "failed", result_json,
                 output_hash, error_code, at, operation_id),
            )
            connection.execute(
                """UPDATE jobs SET status=?,attempt_count=?,result_json=?,result_sha256=?,
                   error_code=?,updated_at=?,row_version=row_version+1 WHERE id=?""",
                ("succeeded" if status == "valid" else "failed", attempts, job_result_json,
                 output_hash, error_code, at, job_id),
            )
