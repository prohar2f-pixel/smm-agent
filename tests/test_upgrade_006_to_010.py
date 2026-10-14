import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from db.migrate import DEFAULT_MIGRATIONS_DIR, run_migrations


class UpgradeSixToTenTests(unittest.TestCase):
    def test_upgrade_preserves_existing_rows_and_adds_hardening(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_migrations = root / "old-migrations"
            old_migrations.mkdir()
            for path in sorted(DEFAULT_MIGRATIONS_DIR.glob("*.sql"))[:6]:
                shutil.copy2(path, old_migrations / path.name)
            database = root / "agent.db"
            self.assertEqual(run_migrations(database, old_migrations), list(range(1, 7)))
            with sqlite3.connect(database) as connection:
                connection.execute(
                    "INSERT INTO posts(topic,post_type,content_telegram,content_vk) VALUES(?,?,?,?)",
                    ("preserved", "case", "tg", "vk"),
                )
            self.assertEqual(run_migrations(database), list(range(7, 13)))
            with sqlite3.connect(database) as connection:
                self.assertEqual(connection.execute(
                    "SELECT topic FROM posts"
                ).fetchone()[0], "preserved")
                columns = {row[1] for row in connection.execute(
                    "PRAGMA table_info(approval_decisions)"
                )}
                self.assertIn("platform", columns)
                triggers = {row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger'"
                )}
                self.assertIn("immutable_publish_packages_update", triggers)
                self.assertEqual(connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                ).fetchall(), [(number,) for number in range(1, 13)])


if __name__ == "__main__":
    unittest.main()
