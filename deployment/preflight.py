"""Machine-checkable, fail-closed production preflight."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from db.backup import create_verified_backup
from db.content_repository import ContentRepository


PLACEHOLDERS = {"", "change-me", "changeme", "todo", "placeholder", "xxx"}


def _bool(env, key):
    return env.get(key, "false").lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class PreflightResult:
    ok: bool
    mode: str
    errors: tuple[str, ...]


def validate_environment(env: dict) -> PreflightResult:
    health_only = _bool(env, "FACTORY_HEALTH_ONLY")
    errors = []
    for key in ("ADMIN_TELEGRAM_ID", "DB_PATH"):
        if env.get(key, "").strip().lower() in PLACEHOLDERS:
            errors.append(f"missing or placeholder {key}")
    if not health_only:
        for key in ("TELEGRAM_BOT_TOKEN", "OPENROUTER_API_KEY"):
            if env.get(key, "").strip().lower() in PLACEHOLDERS:
                errors.append(f"missing or placeholder {key}")
    control = _bool(env, "FACTORY_CONTROL_PLANE_ENABLED")
    worker = _bool(env, "FACTORY_WORKER_ENABLED")
    publishing = _bool(env, "FACTORY_PUBLISHING_ENABLED")
    paid = _bool(env, "FACTORY_PAID_GENERATION_ENABLED")
    if health_only and any((control, worker, publishing, paid)):
        errors.append("health-only mode requires every action flag disabled")
    if publishing and (not control or not worker):
        errors.append("publishing requires control plane and worker")
    if paid and (not control or not worker):
        errors.append("paid generation requires control plane and worker")
    if any((worker, publishing, paid)):
        for key in ("FACTORY_PILOT_ID", "FACTORY_ARTIFACT_ROOT", "FACTORY_BACKUP_DIR"):
            if env.get(key, "").strip().lower() in PLACEHOLDERS:
                errors.append(f"enabled production actions require {key}")
    if _bool(env, "LEGACY_AUTO_PUBLISH_ENABLED") or _bool(env, "LEGACY_DIRECT_PUBLISH_ENABLED"):
        errors.append("legacy publishing cannot be enabled in production")
    return PreflightResult(not errors, "health-only" if health_only else "service", tuple(errors))


def validate_pilot(repository: ContentRepository, pilot_id: str) -> tuple[str, ...]:
    row = repository.fetchone("SELECT * FROM pilot_passports WHERE id=?", (pilot_id,))
    if row is None:
        return ("pilot not found",)
    errors = []
    if row["status"] not in ("ready", "running", "observing"):
        errors.append("pilot is not active")
    for key in ("audience", "offer", "platform", "target_ref", "cta_text"):
        if not row[key]:
            errors.append(f"pilot missing {key}")
    for key in ("thresholds_json", "labor_rates_json", "allocation_policy_json"):
        if row[key] in (None, "", "{}"):
            errors.append(f"pilot missing {key}")
    if not row["baseline_ref"]:
        errors.append("pilot missing baseline_ref")
    return tuple(errors)


def backup_restore_drill(source: Path, work_dir: Path) -> dict:
    backup = work_dir / "preflight-backup.db"
    restored = work_dir / "preflight-restored.db"
    create_verified_backup(source, backup)
    create_verified_backup(backup, restored)
    with sqlite3.connect(restored) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        versions = [row[0] for row in connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        )]
    return {"integrity": integrity, "versions": versions,
            "backup": str(backup), "restored": str(restored)}
