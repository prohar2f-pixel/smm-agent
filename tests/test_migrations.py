import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from db.migrate import MigrationError, discover_migrations, run_migrations


class MigrationRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "agent.db"
        self.migrations_dir = self.root / "migrations"
        self.migrations_dir.mkdir()

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_migration(self, name: str, sql: str) -> Path:
        path = self.migrations_dir / name
        path.write_text(sql, encoding="utf-8")
        return path

    def test_discover_migrations_orders_by_numeric_version_and_hashes_bytes(self):
        second = self.write_migration(
            "002_second.sql", "CREATE TABLE second_table (id INTEGER);\n"
        )
        first = self.write_migration(
            "001_first.sql", "CREATE TABLE first_table (id INTEGER);\n"
        )
        (self.migrations_dir / "README.md").write_text("ignored", encoding="utf-8")

        records = discover_migrations(self.migrations_dir)

        self.assertEqual([record.version for record in records], [1, 2])
        self.assertEqual([record.name for record in records], ["first", "second"])
        self.assertEqual(
            records[0].checksum,
            hashlib.sha256(first.read_bytes()).hexdigest(),
        )
        self.assertEqual(records[1].path, second)

    def test_run_migrations_applies_each_version_once(self):
        self.write_migration(
            "001_first.sql", "CREATE TABLE first_table (value TEXT NOT NULL);\n"
        )
        self.write_migration(
            "002_second.sql", "CREATE TABLE second_table (value INTEGER NOT NULL);\n"
        )

        first_run = run_migrations(self.db_path, self.migrations_dir)
        second_run = run_migrations(self.db_path, self.migrations_dir)

        self.assertEqual(first_run, [1, 2])
        self.assertEqual(second_run, [])
        with sqlite3.connect(self.db_path) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            versions = connection.execute(
                "SELECT version, name FROM schema_migrations ORDER BY version"
            ).fetchall()
        self.assertIn("first_table", tables)
        self.assertIn("second_table", tables)
        self.assertEqual(versions, [(1, "first"), (2, "second")])

    def test_changed_applied_migration_fails_before_new_version_runs(self):
        first = self.write_migration(
            "001_first.sql", "CREATE TABLE first_table (id INTEGER);\n"
        )
        run_migrations(self.db_path, self.migrations_dir)
        first.write_text("CREATE TABLE changed_table (id INTEGER);\n", encoding="utf-8")
        self.write_migration(
            "002_second.sql", "CREATE TABLE second_table (id INTEGER);\n"
        )

        with self.assertRaisesRegex(MigrationError, "checksum"):
            run_migrations(self.db_path, self.migrations_dir)

        with sqlite3.connect(self.db_path) as connection:
            second_exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='second_table'"
            ).fetchone()
        self.assertIsNone(second_exists)

    def test_duplicate_versions_are_rejected(self):
        self.write_migration("001_first.sql", "SELECT 1;\n")
        self.write_migration("001_duplicate.sql", "SELECT 2;\n")

        with self.assertRaisesRegex(MigrationError, "Duplicate migration version 1"):
            discover_migrations(self.migrations_dir)

    def test_malformed_sql_filename_is_rejected(self):
        self.write_migration("first.sql", "SELECT 1;\n")

        with self.assertRaisesRegex(MigrationError, "Malformed migration filename"):
            discover_migrations(self.migrations_dir)


class LegacyBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "agent.db"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_empty_database_gains_legacy_schema_and_version_one(self):
        applied = run_migrations(self.db_path)

        with sqlite3.connect(self.db_path) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            versions = connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()

        self.assertEqual(applied, [1])
        self.assertTrue(
            {
                "brand_voice",
                "posts",
                "topics_used",
                "competitor_signals",
                "settings",
            }.issubset(tables)
        )
        self.assertEqual(versions, [(1,)])

    def test_existing_legacy_post_survives_schema_adoption_unchanged(self):
        original = (
            41,
            "Демонстрация проекта",
            "case",
            "Текст Telegram",
            "Текст VK",
            "published",
            "2026-10-12T09:00:00+00:00",
            "2026-10-12T09:05:00+00:00",
            7001,
            9001,
            "good",
            "2026-10-12 08:00:00",
        )
        with sqlite3.connect(self.db_path) as connection:
            connection.executescript(
                """
                CREATE TABLE posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic TEXT,
                    post_type TEXT,
                    content_telegram TEXT,
                    content_vk TEXT,
                    status TEXT DEFAULT 'draft',
                    scheduled_at DATETIME,
                    published_at DATETIME,
                    telegram_message_id INTEGER,
                    vk_post_id INTEGER,
                    reaction TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            connection.execute(
                """
                INSERT INTO posts(
                    id, topic, post_type, content_telegram, content_vk, status,
                    scheduled_at, published_at, telegram_message_id, vk_post_id,
                    reaction, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                original,
            )
            connection.commit()

        applied = run_migrations(self.db_path)

        with sqlite3.connect(self.db_path) as connection:
            saved = connection.execute(
                """
                SELECT id, topic, post_type, content_telegram, content_vk, status,
                       scheduled_at, published_at, telegram_message_id, vk_post_id,
                       reaction, created_at
                FROM posts WHERE id = 41
                """
            ).fetchone()
            post_count = connection.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
            settings_count = connection.execute(
                "SELECT COUNT(*) FROM settings"
            ).fetchone()[0]

        self.assertEqual(applied, [1])
        self.assertEqual(saved, original)
        self.assertEqual(post_count, 1)
        self.assertEqual(settings_count, 0)


if __name__ == "__main__":
    unittest.main()
