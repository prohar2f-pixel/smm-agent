# R2 transactional jobs, budgets and LLM core

## Release boundary

This release is package 3 of the content-factory implementation plan. It provides
the transaction and accounting core for paid structured LLM work. It is not the
full product DoD R2.

The core is disabled by architecture: no bot handler, APScheduler task, worker,
publisher or production startup path imports or invokes it. Applying migration 005
creates tables only. It does not enqueue work, call OpenRouter, render media or
publish content.

Deferred packages include idea intake and scoring, media/render pipelines,
publication adapters, analytics, learning loops, production wiring and migration
of a live Railway database.

## Delivered contracts

- `JobService` provides queued work, atomic claim, leases, fence tokens,
  retryable failure, cancellation and unknown-outcome recovery.
- `BudgetService` atomically checks revision readiness and five simultaneous
  limits: operation, project, pilot, day and month. Every paid intent receives a
  reservation before a provider boundary is crossed.
- `CostService` stores immutable provider evidence, handles partial/final charges,
  refunds, explicit no-charge evidence, overspend incidents and reconciliation.
- `PromptRegistry` stores immutable `(name, semantic_version)` templates with a
  canonical SHA-256 hash. Production commands must provide the exact version.
- `StructuredOutputValidator` accepts a JSON object or one outer `json` fence and
  validates Draft 2020-12 schemas with format checking.
- `StructuredLlmGateway` reserves budget, records intent, invokes only an injected
  transport, allows one fixed repair attempt, records hashes/references and usage,
  settles cost and writes one final immutable prompt run.

## Invariants

1. Provider execution is impossible without an active limit for all five scopes,
   a positive upper bound, a configured model/currency price and a production-ready
   exact revision.
2. Source input is data. It cannot choose the prompt, model, transport, proxy,
   budget, target revision or repair instruction.
3. An operation command ID is idempotent. A completed duplicate returns the stored
   result. An in-flight or unknown duplicate never calls the provider and requires
   reconciliation.
4. Remaining budget equals the limit minus immutable net charges minus current
   `held_minor`. Partial charges are not double-counted.
5. Raw model output is represented by SHA-256 plus a storage reference. Validated
   structured output may be stored for deterministic duplicate responses. Secrets
   and authorization headers must never be written.
6. A revision link accepts only a valid run reserved for that exact revision and
   can be created once.

## Worker and lease recovery

A worker claims a queued or retryable job atomically and receives a fence token.
Only that lease owner with that token may complete or fail it. A stale worker must
stop when the fence check fails.

When a lease expires:

- before external intent, `recover_expired` returns the job to retryable failure;
- after external intent, it becomes `unknown_outcome` and must not be retried until
  provider evidence resolves whether a charge or external side effect occurred.

Do not manually change a fence token, clear `external_intent_at` or requeue an
unknown job.

## Unknown outcome and reconciliation

A timeout or invalid provider evidence after submission is not proof of failure.
The external operation and job become `unknown_outcome`, while the remaining
reservation stays held.

Operator procedure:

1. Locate the operation by command/idempotency key.
2. Query the provider using that key outside this core and preserve the provider
   evidence reference.
3. If a charge exists, record the immutable charge with its unique evidence ID.
4. If the provider confirms no charge, call `CostService.reconcile` with
   `outcome="no_charge"` and the evidence reference.
5. Never infer no-charge from a timeout and never release funds without evidence.

An overspend opens a pilot-level incident and future reservations fail closed until
the incident is deliberately resolved by an operator workflow added in a later
package.

## Prompt and schema operations

- Register a new semantic version for every template or schema change. Existing
  versions are immutable.
- Use `MAJOR.MINOR.PATCH`; aliases such as `latest` are not accepted.
- The root output schema must be `type: object` with
  `additionalProperties: false`.
- A malformed first response gets exactly one system-defined repair request. The
  second invalid response is final `invalid` and cannot be linked to a revision.
- Do not embed model, credentials, proxy settings or budget instructions in source
  content or prompt variables.

## Verification evidence

- Branch: `feat/content-factory-r2-core`
- Integration base: `335fbd4`
- Implementation head before this documentation commit: `23c4007`
- Database migration: version 5, `005_r2_jobs_budget_llm.sql`
- Automated suite at evidence capture: 98 tests passing
- Network posture: deterministic fake transports only, zero real provider calls

Verification commands:

```bash
python3 -m py_compile db/content_repository.py domain/job_service.py domain/budget_service.py domain/cost_service.py llm/prompt_registry.py llm/validators.py llm/gateway.py
python3 -m unittest discover -v
git diff --check
```

This evidence does not claim testing of real OpenRouter billing, real rendering,
Telegram/VK publication, Railway migration or any production behavior.

## Rollback

The safe rollback is code-level: keep the feature unwired and return to the
integration base or omit the R2 commits. Migration 005 is additive. Do not delete
its tables from a database that may contain operations, reservations or cost
evidence. Restore a verified pre-migration backup if schema rollback is required.
