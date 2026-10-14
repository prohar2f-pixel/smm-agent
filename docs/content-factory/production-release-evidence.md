# Content Factory production release evidence

Date: 2026-10-14

## Result

The complete guarded Content Factory execution layer is live in
`smm-agent.service` on the VPS. The Telegram control plane is enabled.
Worker execution, public publishing, paid generation and both legacy publishing
paths remain disabled until a real pilot, verified sources and exact human
approvals are present.

No public content was sent during this rollout.

## Reviewed source

- Implementation commits: `cfa6d72` and `0b6fe72`.
- GitHub integration: PR #4, merge commit `8789b44`.
- Deployed release: `/opt/aiprohar/smm-agent/releases/8789b44`.
- Rollback release retained: `/opt/aiprohar/smm-agent/releases/3029a91`.

## Delivered execution capabilities

- Real 30-45 second multi-clip FFmpeg screen-demo preview/master renderer.
- Per-job output namespaces, descriptor-relative atomic replacement and no-symlink
  guards for render outputs.
- Canonical immutable render manifests bound to the current revision and exact
  approved preview.
- Persisted machine QA plus exact-asset human QA and approval commands.
- Supervised worker loop, ten-minute leases, bounded retry backoff and terminal
  retry exhaustion.
- Concrete Telegram and VK transports using verified open asset handles.
- Guarded publishing consumer, manual-publication evidence and unknown-outcome
  reconciliation without blind retries.
- Pilot-A verdicts based on independent projects and master hashes, distinct
  sources, complete QA, approved thresholds, costs, labor, cycle time, quality
  and matured metric windows.

## Verification

- Unit/integration suite: 183 tests passed.
- Python compile checks: passed.
- `git diff --check`: passed.
- Independent review: all Critical and Important findings fixed and regression
  tested. One redundant report query remains as a deferred, behavior-neutral minor.
- Real renderer smoke: 36.000 seconds, 1080x1920, two clips, audio and subtitles;
  output SHA-256 `da24f42c451eaa541917357f25a23fa87a30dabfa1dda6cf39c57eac5ea0cf46`.
- Production preflight: service mode, `ok=True`.
- Verified pre-migration backup:
  `/var/lib/smm-agent/backups/pre-8789b44-20261014T0512Z.db`.
- Migration-on-copy and backup/restore drill: `integrity=ok`, versions 1-12,
  both immutable brief triggers present.
- Production SQLite after rollout: `integrity=ok`, versions 1-12, both immutable
  brief triggers present.
- Production service: active/running, zero automatic restarts.
- Telegram polling: connected as `@AiProharKontent_bot` through the local proxy.
- Telegram registry: all 11 factory commands registered, none missing.
- Production external ledger: zero publish attempts, zero external operations,
  zero queued or unknown publish jobs.

## Runtime flags

- `FACTORY_CONTROL_PLANE_ENABLED=true`
- `FACTORY_WORKER_ENABLED=false`
- `FACTORY_PUBLISHING_ENABLED=false`
- `FACTORY_PAID_GENERATION_ENABLED=false`
- `LEGACY_AUTO_PUBLISH_ENABLED=false`
- `LEGACY_DIRECT_PUBLISH_ENABLED=false`

## Activation boundary

Code implementation and production deployment are complete. Enabling production
actions is a business-evidence step, not a missing code step:

1. Select `FACTORY_PILOT_ID` and register at least three rights-cleared real sources.
2. Create the first project/revision and enqueue a preview with `/factory_enqueue`.
3. Record named human QA with `/factory_qa`, then approve the exact preview/master
   hashes with `/factory_approve`.
4. Enable the worker only after pilot preflight passes.
5. Enqueue each exact publish package and target with `/factory_publish_enqueue`.
6. Enable publishing only for the approved execution window. Reconcile unknown
   provider outcomes before any retry.

## Recovery evidence

- Repoint `/opt/aiprohar/smm-agent/current` to release `3029a91` and restart the
  service for application rollback.
- Restore the verified backup only if migration integrity failed. Do not restore
  the database merely to undo content state.
