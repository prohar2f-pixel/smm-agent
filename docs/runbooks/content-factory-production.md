# Content Factory production runbook

## Launch invariant

Deployment never grants permission to publish a package. Start with
`FACTORY_PUBLISHING_ENABLED=false`, `FACTORY_PAID_GENERATION_ENABLED=false`,
`LEGACY_AUTO_PUBLISH_ENABLED=false` and `LEGACY_DIRECT_PUBLISH_ENABLED=false`.

## Before deployment

1. Run `python3 -m unittest discover -v`.
2. Run `python3 -m compileall -q .`.
3. Copy the production SQLite file with `python3 -m db.backup SOURCE DESTINATION`.
4. Run the backup/restore drill and verify `PRAGMA integrity_check` returns `ok`.
5. Validate secrets and flags with `deployment.preflight.validate_environment`.
6. Validate the selected pilot with `validate_pilot`.

## Railway rollout

1. Deploy `Procfile`. `deployment.launch` exits with code 2 on unsafe config.
2. Keep publishing and paid generation disabled.
3. Verify migration versions 1-9 and `/factory_status`.
4. Verify queued jobs remain queued and no `publish_attempts` appeared.
5. Enable only the control plane and worker after the health check.

## Recovery

- Failed build: redeploy the previous Railway deployment. The database is unchanged.
- Failed migration: stop the new deployment, preserve the failed database as evidence,
  restore the verified pre-deploy backup to a new path, then repoint `DB_PATH`.
- Unknown external outcome: never retry blindly. Use provider evidence and reconcile.
- Suspected unsafe publication: disable all publishing flags and revoke target tokens.

## Rollback boundary

Migrations are additive. Roll back application code first. Restore a database only
when integrity or migration application failed, never merely to undo content state.
