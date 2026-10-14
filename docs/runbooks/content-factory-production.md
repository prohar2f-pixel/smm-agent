# Content Factory production runbook

## Launch invariant

Deployment never grants permission to publish a package. Start with
`FACTORY_PUBLISHING_ENABLED=false`, `FACTORY_PAID_GENERATION_ENABLED=false`,
`LEGACY_AUTO_PUBLISH_ENABLED=false` and `LEGACY_DIRECT_PUBLISH_ENABLED=false`.
Worker activation also requires a selected, preflight-clean pilot.

## Before deployment

1. Run `python3 -m unittest discover -s tests -v`.
2. Compile the changed modules and run `git diff --check`.
3. Create a verified SQLite snapshot with `python3 -m db.backup SOURCE DESTINATION`.
4. Apply migrations to a copy, back that copy up again and verify both restored
   databases with `PRAGMA integrity_check`.
5. Run `deployment.preflight.validate_environment` with the effective service flags.
6. When actions will be enabled, validate the selected pilot with `validate_pilot`.

## VPS rollout

1. Export the exact reviewed Git commit into a new immutable directory under
   `/opt/aiprohar/smm-agent/releases/<sha>`.
2. Compare `requirements.txt` with the active release and update the shared virtual
   environment only when dependencies changed.
3. Atomically repoint `/opt/aiprohar/smm-agent/current` to the new release.
4. Restart `smm-agent.service` and verify `active/running`, exit status 0 and no
   restart loop.
5. Verify production database integrity, migration versions 1-12 and the immutable
   brief triggers.
6. Verify Telegram polling and the command registry.
7. Confirm the publish-attempt and external-operation ledgers did not change.

## Operational commands

- `/factory_status` - flags, schema and queue counts.
- `/factory_pilot` - selected pilot and preflight blockers.
- `/factory_queue` - render and publish queue state.
- `/factory_enqueue` - enqueue an exact preview or master manifest.
- `/factory_qa` - record a named QA result for the exact current asset.
- `/factory_approve` - record an exact hash-bound decision.
- `/factory_publish_enqueue` - create a per-platform publish job without sending it.
- `/factory_reconcile [job_id]` - list or query unknown provider outcomes.
- `/factory_reconcile_confirm` - close an unknown result from verified evidence.
- `/factory_manual_confirm` - record verified evidence for a manual publication.
- `/factory_report` - build the deterministic pilot verdict.

## Recovery

- Failed startup: keep action flags off, inspect the service journal and repoint
  `current` to the prior release before restarting.
- Failed migration: stop the new release, preserve the failed database as evidence,
  restore the verified pre-deploy backup to a new path, then repoint `DB_PATH`.
- Unknown external outcome: never retry blindly. Reconcile with provider evidence.
- Suspected unsafe publication: disable all publishing flags and revoke target tokens.

## Rollback boundary

Migrations are additive. Roll back application code first. Restore a database only
when integrity or migration application failed, never merely to undo content state.
