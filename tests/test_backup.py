import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from db.backup import BackupError, create_verified_backup
from db.migrate import run_migrations


class VerifiedBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.source = self.root / "agent.db"
        self.destination = self.root / "backups" / "agent.db"

    def tearDown(self):
        self.temp_dir.cleanup()

    def create_source(self) -> None:
        run_migrations(self.source)
        with sqlite3.connect(self.source) as connection:
            connection.execute(
                """
                INSERT INTO posts(topic, post_type, content_telegram, content_vk, status)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("Backup proof", "case", "Telegram body", "VK body", "pending"),
            )
            connection.commit()

    def test_backup_preserves_schema_versions_and_legacy_rows(self):
        self.create_source()

        result = create_verified_backup(self.source, self.destination)

        self.assertEqual(result, self.destination.resolve())
        with sqlite3.connect(self.source) as source_connection:
            source_versions = source_connection.execute(
                "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
            ).fetchall()
        with sqlite3.connect(self.destination) as backup_connection:
            backup_versions = backup_connection.execute(
                "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
            ).fetchall()
            backup_posts = backup_connection.execute(
                "SELECT topic, content_telegram, content_vk, status FROM posts"
            ).fetchall()

        self.assertEqual(backup_versions, source_versions)
        self.assertEqual(
            backup_posts,
            [("Backup proof", "Telegram body", "VK body", "pending")],
        )

    def test_missing_source_fails_without_creating_destination(self):
        with self.assertRaisesRegex(BackupError, "does not exist"):
            create_verified_backup(self.source, self.destination)

        self.assertFalse(self.destination.exists())

    def test_non_sqlite_source_fails_and_removes_incomplete_destination(self):
        self.source.write_text("not a sqlite database", encoding="utf-8")

        with self.assertRaisesRegex(BackupError, "backup failed"):
            create_verified_backup(self.source, self.destination)

        self.assertFalse(self.destination.exists())

    def test_existing_destination_is_not_overwritten(self):
        self.create_source()
        self.destination.parent.mkdir(parents=True)
        self.destination.write_bytes(b"existing-backup")

        with self.assertRaisesRegex(BackupError, "already exists"):
            create_verified_backup(self.source, self.destination)

        self.assertEqual(self.destination.read_bytes(), b"existing-backup")

    def test_verification_failure_removes_only_new_destination(self):
        self.create_source()
        unrelated = self.root / "keep.db"
        unrelated.write_bytes(b"keep")

        with patch(
            "db.backup._verify_backup",
            side_effect=BackupError("verification failed"),
        ):
            with self.assertRaisesRegex(BackupError, "verification failed"):
                create_verified_backup(self.source, self.destination)

        self.assertFalse(self.destination.exists())
        self.assertEqual(unrelated.read_bytes(), b"keep")


if __name__ == "__main__":
    unittest.main()
