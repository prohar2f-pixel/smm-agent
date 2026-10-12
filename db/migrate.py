"""Checksum-protected SQLite migrations."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


MIGRATION_FILENAME = re.compile(r"^(\d{3})_([a-z0-9][a-z0-9_]*)\.sql$")
DEFAULT_MIGRATIONS_DIR = Path(__file__).with_name("migrations")


class MigrationError(RuntimeError):
    """Raised when migration discovery or application is unsafe."""


@dataclass(frozen=True)
class MigrationRecord:
    version: int
    name: str
    path: Path
    checksum: str


def discover_migrations(migrations_dir: Path) -> list[MigrationRecord]:
    """Return validated SQL migrations ordered by numeric version."""
    directory = Path(migrations_dir)
    if not directory.is_dir():
        raise MigrationError(f"Migration directory does not exist: {directory}")

    records: list[MigrationRecord] = []
    versions: set[int] = set()
    for path in sorted(directory.iterdir()):
        if path.suffix != ".sql":
            continue
        match = MIGRATION_FILENAME.fullmatch(path.name)
        if not match:
            raise MigrationError(f"Malformed migration filename: {path.name}")
        version = int(match.group(1))
        if version <= 0:
            raise MigrationError(f"Migration version must be positive: {path.name}")
        if version in versions:
            raise MigrationError(f"Duplicate migration version {version}")
        versions.add(version)
        records.append(
            MigrationRecord(
                version=version,
                name=match.group(2),
                path=path,
                checksum=hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        )
    return sorted(records, key=lambda record: record.version)


def _configure_connection(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")


def _ensure_migration_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            checksum TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )
    connection.commit()


def _validate_applied(
    connection: sqlite3.Connection, records: list[MigrationRecord]
) -> set[int]:
    current = {record.version: record for record in records}
    applied: set[int] = set()
    rows = connection.execute(
        "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
    ).fetchall()
    for version, name, checksum in rows:
        record = current.get(version)
        if record is None:
            raise MigrationError(f"Applied migration file is missing: version {version}")
        if record.name != name:
            raise MigrationError(
                f"Applied migration name mismatch for version {version}: {name}"
            )
        if record.checksum != checksum:
            raise MigrationError(
                f"Applied migration checksum mismatch for version {version}"
            )
        applied.add(version)
    return applied


def _apply_migration(
    connection: sqlite3.Connection, record: MigrationRecord
) -> None:
    try:
        sql = record.path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise MigrationError(
            f"Migration must be UTF-8: {record.path.name}"
        ) from exc

    def reject_transaction_control(
        action_code: int,
        _arg1: str | None,
        _arg2: str | None,
        _database: str | None,
        _trigger: str | None,
    ) -> int:
        if action_code in (sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_SAVEPOINT):
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    try:
        connection.set_authorizer(reject_transaction_control)
        for statement in _iter_sql_statements(sql):
            connection.execute(statement)
    except sqlite3.Error as exc:
        raise MigrationError(
            f"Migration {record.version:03d}_{record.name} failed: {exc}"
        ) from exc
    finally:
        connection.set_authorizer(None)

    try:
        connection.execute(
            """
            INSERT INTO schema_migrations(version, name, checksum, applied_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                record.version,
                record.name,
                record.checksum,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
    except sqlite3.Error as exc:
        raise MigrationError(
            f"Migration {record.version:03d}_{record.name} failed: {exc}"
        ) from exc


def _iter_sql_statements(sql: str):
    """Yield complete SQLite statements without losing trigger or string syntax."""
    buffer: list[str] = []
    for character in sql:
        buffer.append(character)
        if character != ";":
            continue
        candidate = "".join(buffer)
        if sqlite3.complete_statement(candidate):
            if candidate.strip():
                yield candidate
            buffer.clear()

    remainder = "".join(buffer)
    if remainder.strip():
        yield remainder


def run_migrations(
    db_path: str | Path,
    migrations_dir: str | Path | None = None,
) -> list[int]:
    """Validate migration history and apply pending migrations once."""
    database = Path(db_path)
    database.parent.mkdir(parents=True, exist_ok=True)
    directory = Path(migrations_dir) if migrations_dir else DEFAULT_MIGRATIONS_DIR
    records = discover_migrations(directory)

    connection = sqlite3.connect(database, timeout=5.0)
    try:
        _configure_connection(connection)
        _ensure_migration_table(connection)
        newly_applied: list[int] = []
        while True:
            connection.execute("BEGIN IMMEDIATE")
            try:
                applied = _validate_applied(connection, records)
                pending = next(
                    (record for record in records if record.version not in applied),
                    None,
                )
                if pending is None:
                    connection.commit()
                    return newly_applied
                _apply_migration(connection, pending)
                connection.commit()
                newly_applied.append(pending.version)
            except Exception:
                connection.rollback()
                raise
    finally:
        connection.close()
