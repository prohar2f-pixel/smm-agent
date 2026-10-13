# SQLite migration and backup runbook

This runbook applies to the single-user `smm-agent` SQLite database. Commands are examples and must be pointed at the actual deployment paths.

## Stop conditions

Do not migrate, replace or restore a database when any of these is true:

- the source path or service owner is uncertain;
- a publication, paid provider call or other external operation has an unresolved outcome;
- the bot or scheduler can still write while a file replacement is planned;
- free disk space is insufficient for the source, backup and restored copy;
- the previous backup has not passed `PRAGMA integrity_check`;
- the migration files do not match the checksums recorded in `schema_migrations`.

R0 does not yet persist every external operation. Until that ledger exists, the operator must explicitly confirm that no publish or paid request is in flight.

## Create a verified backup

1. Identify the database from `DB_PATH` without printing secrets.
2. Choose a new destination. The tool refuses to overwrite an existing file.
3. Run:

```bash
python3 -m db.backup data/agent.db backups/agent-before-r0.db
sha256sum backups/agent-before-r0.db
```

Record the source path, destination path, UTC time, size and SHA-256 in the change ticket. The backup command uses SQLite online backup, checks both files with `PRAGMA integrity_check` and compares migration history when present.

## Verify migrations on a disposable copy

Never use the production file for the first migration attempt.

```bash
cp backups/agent-before-r0.db /tmp/smm-agent-migration-check.db
DB_PATH=/tmp/smm-agent-migration-check.db python3 -c "from db.migrate import run_migrations; print(run_migrations('/tmp/smm-agent-migration-check.db'))"
python3 -c "import sqlite3; db=sqlite3.connect('/tmp/smm-agent-migration-check.db'); print(db.execute('PRAGMA integrity_check').fetchone()[0]); print(db.execute('SELECT version,name FROM schema_migrations ORDER BY version').fetchall())"
```

Expected result:

- migration command exits with code 0;
- integrity result is `ok`;
- versions are ordered and checksums are present;
- legacy row counts and a sample of post content match the source;
- a second migration run returns an empty list.

## Production cutover

1. Stop the application service and scheduler using the deployment-specific service manager.
2. Confirm there is no process holding the database for writes.
3. Create a fresh verified backup with a new filename.
4. Run migrations against the production database once.
5. Check integrity, applied versions and legacy row counts.
6. Start the service.
7. Check startup logs for migration or checksum errors.
8. Exercise read-only bot commands first. Do not trigger publication as a migration smoke test.

## Restore procedure

Restoration is deliberately not automated in R0.

1. Stop every writer.
2. Copy the selected backup to a new restore candidate path. Do not overwrite the current database.
3. Verify the recorded SHA-256 and run `PRAGMA integrity_check` on the candidate.
4. Inspect `schema_migrations`, legacy row counts and the latest known post IDs.
5. Reconcile any external operations that occurred after the backup timestamp.
6. Only after reconciliation, rename the current database to a retained incident copy and move the candidate into place.
7. Start the service and verify read-only behavior.

If an external publication or charge may have happened after the backup, restoring the older database can erase local evidence and cause a duplicate send. Keep the newer database available for reconciliation and do not resume automated publishing.

## Rollback after a code failure

Prefer code rollback with the additive schema left in place:

1. disable the new feature flag;
2. stop new jobs;
3. reconcile in-flight work;
4. deploy the previous code version;
5. retain new tables and migration history;
6. investigate before any database-file rollback.

Never edit an applied SQL migration. Add a new forward migration. A checksum mismatch is a safety failure, not a prompt to update the checksum manually.
