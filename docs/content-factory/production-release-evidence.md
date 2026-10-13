# Content Factory production release evidence

Date: 2026-10-13

## Result

The guarded production core is live in the existing `smm-agent.service` on the VPS.
The Telegram control plane is enabled. Rendering, public publishing, paid generation,
and both legacy publishing paths are disabled.

## Reviewed source

- Core merge: `9049e89` via GitHub PR #1.
- Telegram proxy fix: `3029a91` via GitHub PR #2.
- Deployed release: `/opt/aiprohar/smm-agent/releases/3029a91`.
- Previous release retained for rollback: `/opt/aiprohar/smm-agent/releases/f9481d3-mig1`.

## Verification

- Unit/integration suite: 166 tests passed.
- `compileall`: passed.
- `git diff --check`: passed.
- Independent re-review: SHIP for guarded control-plane deployment with worker,
  publishing, and paid generation disabled.
- Production SQLite integrity: `ok`.
- Production schema versions: 1 through 11.
- Migration-on-copy and backup/restore drill: `ok`, versions 1 through 11.
- Service state after rollout: active/running, zero restarts.
- Telegram polling started successfully through the local Xray HTTP proxy.
- Telegram command registry includes `factory_status`, `factory_pilot`,
  `factory_queue`, `factory_reconcile`, and `factory_report`.
- Production ledger after launch: zero publish attempts and zero external operations.
- Scheduler started without the legacy auto-publish job.

## Runtime flags

- `FACTORY_CONTROL_PLANE_ENABLED=true`
- `FACTORY_WORKER_ENABLED=false`
- `FACTORY_PUBLISHING_ENABLED=false`
- `FACTORY_PAID_GENERATION_ENABLED=false`
- `LEGACY_AUTO_PUBLISH_ENABLED=false`
- `LEGACY_DIRECT_PUBLISH_ENABLED=false`

## Recovery evidence

- Verified pre-migration backup:
  `/var/lib/smm-agent/backups/pre-9049e89-20261013T2040Z.db`
- Rollback release:
  `/opt/aiprohar/smm-agent/releases/f9481d3-mig1`
- Rollback order: disable service, repoint `current` to the previous release,
  start service, and restore the backup only if migration integrity failed.

## Activation boundary

The release is deliberately read-only at the factory boundary. Before enabling worker,
publishing, or paid generation, complete the remaining activation package:

1. Register real render handlers and run the worker execution loop against them.
2. Pass verified immutable asset handles into concrete Telegram/VK adapters to close
   the final send-time filesystem race.
3. Implement and contract-test the concrete Telegram/VK adapters.
4. Extend pilot-A verdicts to distinct accepted packages, distinct sources, comparable
   median labor baselines, complete QA rubric codes, and matured metric windows.
5. Add operational approval/enqueue/reconciliation commands; current factory commands
   are safe status, queue, evidence, and report views.

No public content was sent during this rollout.
