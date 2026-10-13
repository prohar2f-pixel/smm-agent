import hashlib
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def test_migration_cannot_commit_partial_schema_before_failure(self):
        self.write_migration(
            "001_escape.sql",
            """
            CREATE TABLE leaked_before_commit (id INTEGER);
            COMMIT;
            THIS IS NOT SQL;
            """,
        )

        with self.assertRaises(MigrationError):
            run_migrations(self.db_path, self.migrations_dir)

        with sqlite3.connect(self.db_path) as connection:
            leaked = connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type='table' AND name='leaked_before_commit'
                """
            ).fetchone()
            versions = connection.execute(
                "SELECT version FROM schema_migrations"
            ).fetchall()

        self.assertIsNone(leaked)
        self.assertEqual(versions, [])

    def test_concurrent_startup_serializes_history_check_and_application(self):
        self.write_migration(
            "001_first.sql", "CREATE TABLE first_table (id INTEGER);\n"
        )
        barrier = threading.Barrier(2)
        original_apply = __import__(
            "db.migrate", fromlist=["_apply_migration"]
        )._apply_migration
        results = []
        errors = []

        def synchronized_apply(connection, record):
            try:
                barrier.wait(timeout=1)
            except threading.BrokenBarrierError:
                pass
            return original_apply(connection, record)

        def migrate():
            try:
                results.append(run_migrations(self.db_path, self.migrations_dir))
            except Exception as exc:
                errors.append(exc)

        with patch("db.migrate._apply_migration", synchronized_apply):
            workers = [threading.Thread(target=migrate) for _ in range(2)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(timeout=10)

        self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertEqual(errors, [])
        self.assertEqual(sorted(results), [[], [1]])


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

        self.assertEqual(applied[0], 1)
        self.assertTrue(
            {
                "brand_voice",
                "posts",
                "topics_used",
                "competitor_signals",
                "settings",
            }.issubset(tables)
        )
        self.assertEqual(versions[0], (1,))

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

        self.assertEqual(applied[0], 1)
        self.assertEqual(saved, original)
        self.assertEqual(post_count, 1)
        self.assertEqual(settings_count, 0)


class R0ControlPlaneTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "agent.db"
        run_migrations(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_feature_flag_defaults_disabled_and_key_is_unique(self):
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                "INSERT INTO feature_flags(key, updated_by) VALUES (?, ?)",
                ("content_factory_r0", "test-owner"),
            )
            enabled, row_version = connection.execute(
                "SELECT enabled, row_version FROM feature_flags WHERE key = ?",
                ("content_factory_r0",),
            ).fetchone()
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO feature_flags(key, updated_by) VALUES (?, ?)",
                    ("content_factory_r0", "second-owner"),
                )

        self.assertEqual(enabled, 0)
        self.assertEqual(row_version, 1)

    def test_inbox_rejects_duplicate_producer_event(self):
        values = (
            "inbox-1",
            "telegram:bot-1",
            "update-77",
            "a" * 64,
            "2026-10-12T12:00:00+00:00",
        )
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO inbox_events(
                    id, producer, event_id, payload_sha256, received_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                values,
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO inbox_events(
                        id, producer, event_id, payload_sha256, received_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    ("inbox-2", *values[1:]),
                )

    def test_outbox_enforces_status_and_has_pending_delivery_index(self):
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO outbox_events(
                    id, event_type, aggregate_id, payload_json,
                    available_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "outbox-1",
                    "content.created",
                    "project-1",
                    "{}",
                    "2026-10-12T12:00:00+00:00",
                    "2026-10-12T12:00:00+00:00",
                    "2026-10-12T12:00:00+00:00",
                ),
            )
            status = connection.execute(
                "SELECT status FROM outbox_events WHERE id = 'outbox-1'"
            ).fetchone()[0]
            indexes = {
                row[1] for row in connection.execute("PRAGMA index_list(outbox_events)")
            }
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE outbox_events SET status = 'sent_again' WHERE id = 'outbox-1'"
                )

        self.assertEqual(status, "pending")
        self.assertIn("idx_outbox_pending", indexes)

    def test_callback_token_is_unique_versioned_and_consumed_once(self):
        values = (
            "opaque-token",
            "1001",
            "2002",
            "approve",
            "revision-1",
            1,
            "b" * 64,
            "2026-10-12T12:05:00+00:00",
            "2026-10-12T12:00:00+00:00",
        )
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO callback_actions(
                    token, user_id, chat_id, action, subject_id,
                    expected_row_version, subject_sha256, expires_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO callback_actions(
                        token, user_id, chat_id, action, subject_id,
                        expected_row_version, subject_sha256, expires_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO callback_actions(
                        token, user_id, chat_id, action, subject_id,
                        expected_row_version, subject_sha256, expires_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    ("bad-version", *values[1:5], 0, *values[6:]),
                )

            first = connection.execute(
                """
                UPDATE callback_actions
                SET consumed_at = ?, result_ref = ?
                WHERE token = ? AND consumed_at IS NULL
                """,
                ("2026-10-12T12:01:00+00:00", "review-7", "opaque-token"),
            ).rowcount
            second = connection.execute(
                """
                UPDATE callback_actions
                SET consumed_at = ?, result_ref = ?
                WHERE token = ? AND consumed_at IS NULL
                """,
                ("2026-10-12T12:02:00+00:00", "review-8", "opaque-token"),
            ).rowcount

        self.assertEqual(first, 1)
        self.assertEqual(second, 0)

    def test_audit_event_captures_actor_action_aggregate_and_correlation(self):
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO audit_events(
                    id, actor_id, actor_role, command_id, aggregate_type,
                    aggregate_id, action, before_status, after_status,
                    row_version, reason, correlation_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "audit-1",
                    "1001",
                    "owner",
                    "command-1",
                    "pilot",
                    "pilot-1",
                    "start",
                    "ready",
                    "running",
                    2,
                    "owner approved",
                    "correlation-1",
                    "2026-10-12T12:00:00+00:00",
                ),
            )
            saved = connection.execute(
                """
                SELECT actor_id, action, aggregate_type, aggregate_id,
                       correlation_id, row_version
                FROM audit_events WHERE id = 'audit-1'
                """
            ).fetchone()

        self.assertEqual(
            saved,
            ("1001", "start", "pilot", "pilot-1", "correlation-1", 2),
        )

    def test_failed_migration_rolls_back_schema_and_version_record(self):
        migration_dir = Path(self.temp_dir.name) / "broken_migrations"
        migration_dir.mkdir()
        (migration_dir / "001_stable.sql").write_text(
            "CREATE TABLE stable_table (id INTEGER);\n", encoding="utf-8"
        )
        (migration_dir / "002_broken.sql").write_text(
            "CREATE TABLE partial_table (id INTEGER);\nTHIS IS NOT SQL;\n",
            encoding="utf-8",
        )
        broken_db = Path(self.temp_dir.name) / "broken.db"

        with self.assertRaisesRegex(MigrationError, "002_broken"):
            run_migrations(broken_db, migration_dir)

        with sqlite3.connect(broken_db) as connection:
            versions = connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
            partial_exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='partial_table'"
            ).fetchone()

        self.assertEqual(versions, [(1,)])
        self.assertIsNone(partial_exists)


if __name__ == "__main__":
    unittest.main()
