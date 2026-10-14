# Content Factory Production Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the safe screen-demo production vertical, deploy the integrated bot and worker runtime, then add the Text Factory and later factory branches behind disabled feature flags.

**Architecture:** Extend the existing SQLite modular monolith through additive migrations and focused domain services. The bot remains the control plane; workers consume fenced jobs through versioned JSON contracts; every external send is an independent guarded operation. Production deployment starts the new runtime fail-closed, with public publication disabled until an exact approval and complete pilot configuration exist.

**Tech Stack:** Python 3.11+, aiogram 3.15, SQLite, APScheduler, OpenRouter-compatible transport, FFmpeg/Playwright/HyperFrames adapters behind protocols, Ubuntu VPS and systemd.

**Spec:** `/home/agent/workspace/outputs/3f6f506a-7a85-4204-9066-da7e11b9d76b/smm-agent-content-factory-master-spec-v2.md`

## Global Constraints

- Preserve every invariant in master-spec section 4.
- All schema changes are additive migrations; legacy tables remain intact.
- New production capabilities default disabled and fail closed on missing configuration.
- Preview approval, final approval and publish approval are separate immutable decisions.
- Each platform receives an independent publish job and idempotency key.
- No real public content is sent during automated tests or deployment verification.
- Secrets never enter DB rows, prompts, job JSON, logs or reports.
- Use `Europe/Moscow` for owner-facing schedules and UTC ISO timestamps internally.
- Use `unittest` with temporary SQLite and injected fake external adapters.

## Review Focus

- Duplicate worker reports or callbacks must not create a second asset, approval or external send; covered in Tasks 2, 4 and 5.
- A stale worker, expired lease or mismatched artifact hash must never advance a project; covered in Tasks 2 and 3.
- Partial platform success must not retry the successful platform; covered in Task 5.
- Missing pilot configuration, price, rights, approval or target must stop before an external boundary; covered in Tasks 3, 5, 8 and 11.
- Deployment must start without triggering publication or paid generation; covered in Tasks 9 and 11.

---

### Task 1: Production schema and repositories

**Files:**
- Create: `db/migrations/007_production_vertical.sql`
- Create: `domain/production_models.py`
- Create: `tests/test_production_schema.py`
- Modify: `tests/test_r1_schema.py`
- Modify: `tests/test_r2_schema.py`

**Interfaces:**
- Consumes: migrations 001-006 and `content_projects`/`content_revisions`.
- Produces: additive tables for assets, worker reports, publish packages/jobs/attempts, time entries, metric snapshots, leads, reports, ideas, campaigns and learnings.

- [x] Write failing schema tests for tables, enums, uniqueness, immutable shapes, project lineage and independent platform jobs.
- [x] Run `python3 -m unittest tests.test_production_schema -v`; expect migration/table failures.
- [x] Implement migration 007 and typed immutable records.
- [x] Run focused and full tests; expect green.
- [x] Commit `feat: add production vertical schema`.

### Task 2: Versioned worker contracts and fenced reports

**Files:**
- Create: `workers/__init__.py`
- Create: `workers/contracts.py`
- Create: `workers/report_service.py`
- Create: `tests/test_worker_contracts.py`

**Interfaces:**
- Consumes: `JobService`, asset/report tables and exact revision hashes.
- Produces: `WorkerManifest`, `WorkerReport`, `WorkerReportService.accept(...)` with schema version, owner/fence, input hash, output hashes and idempotent report ID.

- [x] Write RED tests for valid report, duplicate report, stale fence, expired lease, wrong revision/input hash, unsafe path and missing output hash.
- [x] Implement strict dataclasses/parser and atomic report acceptance.
- [x] Run focused plus full suite.
- [x] Commit `feat: add fenced worker report contracts`.

### Task 3: Screen-demo pipeline, preview and QA

**Files:**
- Create: `workers/screen_demo.py`
- Create: `domain/qa_service.py`
- Create: `tests/test_screen_demo_pipeline.py`
- Create: `tests/fixtures/screen_demo_manifest.json`

**Interfaces:**
- Consumes: approved script revision, source assets and worker contracts.
- Produces: deterministic preview/master manifests, technical QA results and separate preview/master acceptance gates.

- [x] Write RED tests for a 30-45 second manifest, audio/subtitle/preview requirements, checksum verification, resource caps and master blocked before preview approval.
- [x] Implement a pure manifest planner and technical QA service; adapters remain injected.
- [x] Verify no external process or network runs in tests.
- [x] Commit `feat: add screen demo preview and qa pipeline`.

### Task 4: Immutable approval callbacks and role checks

**Files:**
- Create: `domain/approval_service.py`
- Create: `app/__init__.py`
- Create: `app/callback_service.py`
- Create: `tests/test_approval_callbacks.py`

**Interfaces:**
- Consumes: R0 callback/audit tables, project revisions and QA results.
- Produces: one-use callback tokens and exact-hash script/preview/final/publish decisions with role and expiry enforcement.

- [x] Write RED tests for duplicate Telegram update, replayed callback, wrong role, expiry, stale revision/hash and separate decision stages.
- [x] Implement authorization, CAS transitions, inbox and audit writes in one transaction.
- [x] Run focused plus full suite.
- [x] Commit `feat: add immutable approval workflow`.

### Task 5: Independent safe publishing

**Files:**
- Create: `publishing/base.py`
- Create: `domain/publish_service.py`
- Create: `tests/test_publish_service.py`
- Modify: `publishing/telegram_pub.py`
- Modify: `publishing/vk_pub.py`

**Interfaces:**
- Consumes: exact publish approval, validated package, `JobService` and external operations.
- Produces: one publish job per platform, `PublisherAdapter` protocol, idempotent success and unknown-outcome reconciliation.

- [x] Write RED tests for missing approval/target, two-platform independence, partial success, timeout unknown, duplicate callback/report and manual evidence mode.
- [x] Implement guarded enqueue/claim/settle around injected fake adapters.
- [x] Keep legacy direct publishers available but unreachable from the new flow.
- [x] Run full suite and commit `feat: add independent guarded publishing`.

### Task 6: Metrics, labor, leads and reproducible report

**Files:**
- Create: `reporting/__init__.py`
- Create: `reporting/pilot_report.py`
- Create: `domain/measurement_service.py`
- Create: `tests/test_pilot_reporting.py`

**Interfaces:**
- Consumes: costs, time entries, publish evidence, 24/72h/7d snapshots and lead classifications.
- Produces: deterministic Markdown report with measured zero distinct from missing data and a final sufficient/insufficient-data decision.

- [x] Write RED tests for missing windows, measured zero, partial costs, baseline comparison, 20 percent target and unknown operation blocking success.
- [x] Implement measurement and report generation from DB only.
- [x] Run focused plus full suite and commit `feat: add reproducible pilot reporting`.

### Task 7: Text Factory idea-to-variant trace

**Files:**
- Create: `db/migrations/008_text_factory.sql`
- Create: `domain/idea_service.py`
- Create: `domain/campaign_service.py`
- Create: `domain/text_factory.py`
- Create: `tests/test_text_factory.py`

**Interfaces:**
- Consumes: sources, evidence, briefs, prompt runs and budgeted LLM gateway.
- Produces: idea cards/scoring, 14-day campaigns, master text and immutable Telegram/VK variants with full trace.

- [x] Write RED tests for no topic without idea, scoring provenance, campaign slot uniqueness, exact source trace, facts/hypotheses and platform variants.
- [x] Implement deterministic orchestration with injected structured LLM gateway.
- [x] Run full suite and commit `feat: add traceable text factory`.

### Task 8: Carousel, production-video and generative contracts

**Files:**
- Create: `db/migrations/009_media_factory.sql`
- Create: `workers/carousel.py`
- Create: `workers/production_video.py`
- Create: `workers/generative_video.py`
- Create: `tests/test_media_factory_contracts.py`

**Interfaces:**
- Consumes: accepted master revision, worker contracts, cost budgets and approvals.
- Produces: safe carousel manifest, replaceable video adapter manifests, bible/episode/scene/shot/generation-attempt/animatic contracts behind disabled flags.

- [x] Write RED contract tests for safe paths/network-off, first/last avatar rule, preview before master, animatic before spend, separate 16:9/9:16 tasks and attempt cost evidence.
- [x] Implement contract planners and schema only; no provider-specific production calls.
- [x] Run full suite and commit `feat: add media factory contracts`.

### Task 9: Analytics and learning loop

**Files:**
- Create: `domain/analytics_service.py`
- Create: `tests/test_analytics_service.py`

**Interfaces:**
- Consumes: comparable metric snapshots, experiments, ideas and publications.
- Produces: guarded comparisons, facts/hypotheses learnings and continuation recommendations.

- [x] Write RED tests for comparable groups, missing metrics, facts versus hypotheses and winner continuation.
- [x] Implement provider-neutral analytics service.
- [x] Run full suite and commit `feat: add analytics learning loop`.

### Task 10: Bot control plane and production worker runtime

**Files:**
- Create: `app/runtime.py`
- Create: `workers/runner.py`
- Create: `bot/content_factory_handlers.py`
- Create: `tests/test_runtime_fail_closed.py`
- Modify: `main.py`
- Modify: `bot/keyboards.py`

**Interfaces:**
- Consumes: feature flags, approval, publish and worker services.
- Produces: owner-only status/pilot/queue/reconcile/report commands and a worker loop that starts safely with all production actions disabled by default.

- [x] Write RED tests for missing env, disabled flags, startup migrations, no automatic publication, command authorization and graceful worker recovery.
- [x] Implement dependency wiring without importing secrets into domain objects.
- [x] Run full suite and commit `feat: wire fail closed content factory runtime`.

### Task 11: Deployment safety, migration and recovery drill

**Files:**
- Create: `deployment/preflight.py`
- Create: `deployment/__init__.py`
- Create: `tests/test_deployment_preflight.py`
- Create: `docs/runbooks/content-factory-production.md`
- Modify: `.env.example`
- Modify: `Procfile`
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: runtime configuration and migration/backup APIs.
- Produces: machine-checkable preflight, database backup/restore drill, health mode and exact rollback steps.

- [x] Write RED tests for missing/placeholder secrets, incomplete pilot config, unsafe feature combinations and health-only startup.
- [x] Implement preflight and run backup/restore against a temporary production-shaped DB.
- [x] Run full suite, compile and diff checks.
- [x] Commit `docs: add production deployment gate`.

### Task 12: Review, push, deploy and launch verification

**Files:**
- Create: `docs/content-factory/production-release-evidence.md`

**Interfaces:**
- Consumes: Tasks 1-11, green verification, the production VPS environment and GitHub remote.
- Produces: reviewed branch, deployed service, startup/migration evidence and rollback reference.

- [x] Run a fresh whole-branch review focused on money, fencing, permissions, paths and external idempotency; fix all Critical/Important findings with RED-GREEN tests.
- [x] Run full suite, compilation, migration-on-copy, backup/restore and `git diff --check`.
- [x] Push the reviewed branch/main through the repository's normal flow.
- [x] Deploy with feature flags disabled, verify systemd start logs and bot health without sending content.
- [x] Enable only non-publishing control-plane commands, verify `/factory_status`, then record exact evidence.
- [x] Commit `docs: record production launch evidence` and push.
