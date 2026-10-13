"""Create a new, verified SQLite backup without overwriting prior evidence."""

from __future__ import annotations

import argparse
import os
import sqlite3
from pathlib import Path


class BackupError(RuntimeError):
    """Raised when a safe SQLite backup cannot be created or verified."""


def _integrity_result(connection: sqlite3.Connection) -> str:
    row = connection.execute("PRAGMA integrity_check").fetchone()
    return str(row[0]) if row else "missing result"


def _has_migration_table(connection: sqlite3.Connection) -> bool:
    return (
        connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'schema_migrations'
            """
        ).fetchone()
        is not None
    )


def _migration_rows(connection: sqlite3.Connection) -> list[tuple]:
    if not _has_migration_table(connection):
        return []
    return connection.execute(
        """
        SELECT version, name, checksum, applied_at
        FROM schema_migrations ORDER BY version
        """
    ).fetchall()


def _verify_backup(source: Path, destination: Path) -> None:
    source_connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    backup_connection = sqlite3.connect(f"file:{destination}?mode=ro", uri=True)
    try:
        source_integrity = _integrity_result(source_connection)
        backup_integrity = _integrity_result(backup_connection)
        if source_integrity != "ok":
            raise BackupError(f"source integrity check failed: {source_integrity}")
        if backup_integrity != "ok":
            raise BackupError(f"backup integrity check failed: {backup_integrity}")
        if _migration_rows(source_connection) != _migration_rows(backup_connection):
            raise BackupError("schema migration history differs in backup")
    finally:
        backup_connection.close()
        source_connection.close()


def create_verified_backup(
    source: str | Path,
    destination: str | Path,
) -> Path:
    """Create and verify a new SQLite backup, never overwriting a destination."""
    source_path = Path(source).expanduser().resolve()
    destination_path = Path(destination).expanduser().resolve()

    if not source_path.is_file():
        raise BackupError(f"source database does not exist: {source_path}")
    if source_path == destination_path:
        raise BackupError("source and destination must be different files")

    destination_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(
            destination_path,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
    except FileExistsError as exc:
        raise BackupError(f"backup destination already exists: {destination_path}") from exc
    except OSError as exc:
        raise BackupError(f"cannot reserve backup destination: {exc}") from exc
    else:
        os.close(descriptor)

    created_destination = True
    source_connection: sqlite3.Connection | None = None
    backup_connection: sqlite3.Connection | None = None
    try:
        source_connection = sqlite3.connect(
            f"file:{source_path}?mode=ro", uri=True, timeout=5.0
        )
        backup_connection = sqlite3.connect(destination_path, timeout=5.0)
        if _integrity_result(source_connection) != "ok":
            raise BackupError("source integrity check failed")
        source_connection.backup(backup_connection)
        backup_connection.close()
        backup_connection = None
        source_connection.close()
        source_connection = None

        _verify_backup(source_path, destination_path)
        created_destination = False
        return destination_path
    except BackupError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise BackupError(f"backup failed: {exc}") from exc
    finally:
        if backup_connection is not None:
            backup_connection.close()
        if source_connection is not None:
            source_connection.close()
        if created_destination:
            destination_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a verified SQLite backup")
    parser.add_argument("source", help="Existing SQLite database")
    parser.add_argument("destination", help="New backup path")
    arguments = parser.parse_args()
    try:
        result = create_verified_backup(arguments.source, arguments.destination)
    except BackupError as exc:
        parser.exit(1, f"backup error: {exc}\n")
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
