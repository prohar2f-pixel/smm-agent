# R0 transactional foundation: release evidence

Date: 12 October 2026  
Branch: `feat/content-factory-r0`  
Implementation range: `b6cc8a81dd82ad3c34d8193546e0147b4fbf4390..2dd63a8973632ab8f3b5cda16473f1ad1c53bc04`

The range contains four implementation commits:

1. `14dab42` - checksum-protected SQLite migration runner;
2. `fa700a2` - legacy bootstrap moved to migration 001;
3. `a826e12` - R0 feature flag, inbox, outbox, audit and callback schema;
4. `2dd63a8` - verified SQLite backup and operator runbook.

This evidence document and the related `CLAUDE.md` update are committed after the implementation range by construction.

## Verified locally

Command:

```bash
python3 -m py_compile db/migrate.py db/backup.py agent/memory.py
python3 -m unittest discover -v
git diff --check
```

Observed result before the documentation commit:

- Python compilation exited with code 0;
- 22 tests ran;
- 22 tests passed;
- 0 failures and 0 errors;
- `git diff --check` exited with code 0.

Covered behaviors:

- ordered migration discovery and exact-byte SHA-256 checksums;
- one-time application and repeatable startup;
- rejection of malformed, duplicate, missing or changed applied migrations;
- atomic rollback after invalid SQL;
- empty and populated legacy database adoption without deleting the tested post;
- disabled-by-default feature flags;
- duplicate inbox event rejection;
- constrained outbox states and pending-delivery index;
- versioned, single-consumption callback actions;
- audit event fields;
- SQLite online backup, integrity and migration-history verification;
- missing, invalid and colliding backup paths;
- cleanup after failed backup verification.

## Deliberately unchanged

- Telegram commands and callback UX;
- Telegram and VK publication flow;
- OpenRouter request behavior;
- scheduler behavior;
- production database contents;
- deployment services and runtime configuration.

The new control-plane tables are infrastructure only. No handler currently writes to inbox, outbox, audit or callback tables, and no feature flag enables new user behavior.

## Not tested in this slice

- migration of the real production `data/agent.db`;
- service stop, production backup, restore or rollback drill;
- Windows/NSSM or any current live deployment startup;
- Telegram, VK or OpenRouter network calls;
- concurrent processes competing for the same production SQLite file;
- R1 domain tables, budget reservations, jobs, workers or safe publishing;
- render and video pipelines.

These items require their own controlled package. The next development slice is R1 Source, Brief and Evidence after review of this isolated branch.
