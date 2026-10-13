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
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)

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
        self._guard_configuration(model, currency, upper_bound_minor, input_data)
        prompt = self.registry.get(prompt_name, prompt_version)
        input_hash = self.canonical_input_hash(input_data)
        request_hash = self.canonical_input_hash({
            "purpose": purpose, "prompt": [prompt_name, prompt_version], "model": model,
            "input": input_data, "currency": currency, "upper_bound_minor": upper_bound_minor,
            "provider": transport.provider_key, "budget_context": budget_context.__dict__,
        })
        existing = self._existing(
            run_id, request_hash, transport.provider_key, purpose, prompt_name,
            prompt_version, model, input_hash, currency,
        )
        if existing is not None:
            return existing
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
        original_user = user
        previous_raw = ""
        previous_error = ""

        for attempt in (1, 2):
            if attempt == 2:
                user = (
                    "Return only JSON matching the schema. Do not add Markdown or commentary.\n"
                    f"Original task:\n{original_user}\n"
                    f"Previous response:\n{previous_raw}\n"
                    f"Validation error: {previous_error}\n"
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
            validation_status = "valid"
            try:
                output = self.validator.parse(response.text, prompt.output_schema)
            except StructuredValidationError as exc:
                validation_status = "invalid"
                error_code = "structured_output_invalid"
                previous_raw = response.text
                previous_error = str(exc)
            self._persist_attempt(
                run_id, operation_id, attempt, transport.provider_key, response,
                validation_status, output,
            )
            terminal = output is not None or attempt == 2
            if response.cost_minor > 0:
                self.costs.record_charge(
                    f"cost:{run_id}:{attempt}", operation_id, reservation_id,
                    transport.provider_key, response.evidence_id, response.cost_minor,
                    currency, self.now(), final=terminal,
                )
            if terminal:
                break

        status = "valid" if output is not None else "invalid"
        total_cost = sum(item.cost_minor for item in responses)
        if total_cost == 0:
            evidence = ",".join(item.evidence_id for item in responses)
            self.costs.mark_no_charge(operation_id, f"provider:{evidence}", self.now())
        elif responses[-1].cost_minor == 0:
            self.costs.finalize_charges(operation_id, reservation_id, self.now())
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
        required = ("input_per_million_minor", "output_per_million_minor",
                    "max_input_tokens", "max_output_tokens", "exchange_basis")
        if (price is None or price.get("currency") != currency
                or any(not isinstance(price.get(key), int) or price[key] <= 0
                       for key in required[:4])
                or not isinstance(price.get("exchange_basis"), str)
                or not price["exchange_basis"].strip()):
            raise ValidationError("model price is missing for requested currency")
        conservative = max(1, (
            price["input_per_million_minor"] * price["max_input_tokens"]
            + price["output_per_million_minor"] * price["max_output_tokens"]
            + 999_999
        ) // 1_000_000)
        if upper_bound < conservative:
            raise ValidationError("upper bound is below configured worst-case price")

    def _existing(self, run_id: str, request_hash: str, provider_key: str,
                  purpose: str, prompt_name: str, prompt_version: str, model: str,
                  input_hash: str, currency: str) -> LlmRunResult | None:
        row = self.repository.fetchone(
            """SELECT p.*,o.result_json,o.request_sha256,o.provider_key FROM prompt_runs p
               JOIN external_operations o ON o.id=p.operation_id WHERE p.command_id=?""",
            (run_id,),
        )
        if row is not None:
            if row["request_sha256"] != request_hash or row["provider_key"] != provider_key:
                raise ConflictError("run id was already used with a different request")
            stored = json.loads(row["result_json"]) if row["result_json"] else None
            output = stored.get("output") if isinstance(stored, dict) and "responses" in stored else stored
            return LlmRunResult(
                row["id"], row["validation_status"], output, row["attempt_count"],
                row["cost_minor"] or 0, row["error_code"],
            )
        operation = self.repository.fetchone(
            "SELECT status,request_sha256,provider_key FROM external_operations WHERE command_id=?", (run_id,),
        )
        if operation is not None:
            if operation["request_sha256"] != request_hash or operation["provider_key"] != provider_key:
                raise ConflictError("run id was already used with a different request")
            attempts = self.repository.fetchall(
                "SELECT * FROM llm_attempts WHERE run_id=? ORDER BY attempt_no", (run_id,)
            )
            terminal = attempts and (
                attempts[-1]["validation_status"] == "valid" or attempts[-1]["attempt_no"] == 2
            )
            if terminal and operation["status"] in {"succeeded", "failed", "reconciled"}:
                last = attempts[-1]
                output = json.loads(last["validated_output_json"]) if last["validated_output_json"] else None
                status = "valid" if output is not None else "invalid"
                self._persist_final(
                    run_id, f"operation:{run_id}", f"job:{run_id}", purpose,
                    prompt_name, prompt_version, model, input_hash, None, status,
                    len(attempts), [], sum(row["cost_minor"] for row in attempts),
                    currency, last["response_ref"],
                    None if status == "valid" else "structured_output_invalid", output,
                )
                return self._existing(
                    run_id, request_hash, provider_key, purpose, prompt_name,
                    prompt_version, model, input_hash, currency,
                )
            raise ReconciliationRequiredError(f"run {run_id} is in-flight or requires reconciliation")
        return None

    def _mark_submitting(self, operation_id: str, job_id: str) -> None:
        at = self.now()
        with self.repository.transaction() as connection:
            job = connection.execute(
                """UPDATE jobs SET status='running',lease_owner=?,fence_token=fence_token+1,
                   attempt_count=attempt_count+1,external_intent_at=?,updated_at=?,row_version=row_version+1
                   WHERE id=? AND status='queued' AND external_intent_at IS NULL""",
                (f"gateway:{job_id}", at, at, job_id),
            )
            if job.rowcount != 1:
                raise ReconciliationRequiredError("job is already owned by another executor")
            cursor = connection.execute(
                """UPDATE external_operations SET status='submitting',updated_at=?,
                   row_version=row_version+1 WHERE id=? AND status='intent'""",
                (at, operation_id),
            )
            if cursor.rowcount != 1:
                raise ReconciliationRequiredError("operation is already in-flight")

    def _persist_attempt(self, run_id, operation_id, attempt, provider, response,
                         validation_status, output):
        digest = hashlib.sha256(response.text.encode("utf-8")).hexdigest()
        with self.repository.transaction() as connection:
            connection.execute(
                """INSERT INTO llm_attempts(id,run_id,operation_id,attempt_no,idempotency_key,
                   response_sha256,response_ref,validation_status,validated_output_json,
                   input_tokens,output_tokens,latency_ms,cost_minor,currency,model,provider,
                   evidence_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (f"attempt:{run_id}:{attempt}", run_id, operation_id, attempt,
                 f"{run_id}:{attempt}", digest, response.output_ref, validation_status,
                 self._canonical(output) if output is not None else None,
                 response.input_tokens, response.output_tokens, response.latency_ms,
                 response.cost_minor, response.currency, response.model, provider,
                 response.evidence_id, self.now()),
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
        attempt_rows = self.repository.fetchall(
            "SELECT * FROM llm_attempts WHERE run_id=? ORDER BY attempt_no", (run_id,)
        )
        input_tokens = sum(row["input_tokens"] for row in attempt_rows)
        output_tokens = sum(row["output_tokens"] for row in attempt_rows)
        latency = sum(row["latency_ms"] for row in attempt_rows)
        total_cost = sum(row["cost_minor"] for row in attempt_rows)
        output_hash = attempt_rows[-1]["response_sha256"]
        output_ref = attempt_rows[-1]["response_ref"]
        response_evidence = [
            {"sha256": row["response_sha256"], "ref": row["response_ref"]}
            for row in attempt_rows
        ]
        result_json = self._canonical({"output": output, "responses": response_evidence})
        job_result_json = self._canonical(output) if output is not None else None
        result_hash = hashlib.sha256(result_json.encode("utf-8")).hexdigest()
        job_result_hash = (
            hashlib.sha256(job_result_json.encode("utf-8")).hexdigest()
            if job_result_json is not None else output_hash
        )
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
                 result_hash, error_code, at, operation_id),
            )
            connection.execute(
                """UPDATE jobs SET status=?,attempt_count=?,result_json=?,result_sha256=?,
                   error_code=?,updated_at=?,row_version=row_version+1 WHERE id=?""",
                ("succeeded" if status == "valid" else "failed", attempts, job_result_json,
                 job_result_hash, error_code, at, job_id),
            )
