import sqlite3
import tempfile
import unittest
from pathlib import Path

from db.migrate import run_migrations


NOW = "2026-10-13T12:00:00+00:00"
HASH = "a" * 64


class ProductionSchemaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "agent.db"

    def tearDown(self):
        self.temp.cleanup()

    def connect(self):
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def test_migration_chain_reaches_version_seven(self):
        self.assertEqual(run_migrations(self.path), list(range(1, 11)))

    def test_production_tables_exist(self):
        run_migrations(self.path)
        expected = {
            "assets", "worker_reports", "qa_results", "approval_decisions",
            "publish_packages", "publish_jobs", "publish_attempts", "time_entries",
            "baselines", "metric_snapshots", "leads", "pilot_reports",
            "idea_cards", "campaigns", "learnings",
        }
        with self.connect() as connection:
            actual = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
        self.assertTrue(expected <= actual)

    def test_publish_platform_jobs_are_independent_but_unique_per_package(self):
        run_migrations(self.path)
        with self.connect() as connection:
            self._seed_project(connection)
            connection.execute(
                """INSERT INTO publish_packages(id,project_id,revision_id,package_sha256,
                   caption_text,assets_json,created_by,created_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                ("pkg", "project", "revision", HASH, "caption", "[]", "owner", NOW),
            )
            for platform in ("telegram", "vk"):
                connection.execute(
                    """INSERT INTO publish_jobs(id,package_id,platform,target_ref,status,
                       created_at,updated_at) VALUES(?,?,?,?,?,?,?)""",
                    (f"job-{platform}", "pkg", platform, "target", "draft", NOW, NOW),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """INSERT INTO publish_jobs(id,package_id,platform,target_ref,status,
                       created_at,updated_at) VALUES(?,?,?,?,?,?,?)""",
                    ("duplicate", "pkg", "telegram", "target", "draft", NOW, NOW),
                )

    def test_invalid_status_and_cross_project_asset_lineage_are_rejected(self):
        run_migrations(self.path)
        with self.connect() as connection:
            self._seed_project(connection)
            connection.execute(
                """INSERT INTO content_projects(id,pilot_id,title,status,owner_id,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?)""",
                ("other-project", "pilot", "Other", "captured", "owner", NOW, NOW),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """INSERT INTO assets(id,project_id,revision_id,storage_key,mime_type,
                       role,sha256,size_bytes,rights_status,privacy_status,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    ("asset", "other-project", "revision", "safe/file.png", "image/png",
                     "image", HASH, 10, "allowed", "clear", NOW),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """INSERT INTO publish_jobs(id,package_id,platform,target_ref,status,
                       created_at,updated_at) VALUES(?,?,?,?,?,?,?)""",
                    ("bad", "missing", "telegram", "target", "sent", NOW, NOW),
                )

    def test_evidence_records_are_append_only_shapes(self):
        run_migrations(self.path)
        immutable = ("worker_reports", "publish_attempts", "metric_snapshots",
                     "time_entries", "approval_decisions")
        with self.connect() as connection:
            for table in immutable:
                columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
                self.assertIn("created_at", columns, table)
                self.assertNotIn("updated_at", columns, table)

    @staticmethod
    def _seed_project(connection):
        connection.execute(
            """INSERT INTO pilot_passports(id,name,status,timezone,evaluation_mode,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?)""",
            ("pilot", "Pilot", "running", "UTC", "internal_efficiency", NOW, NOW),
        )
        connection.execute(
            """INSERT INTO content_projects(id,pilot_id,title,status,owner_id,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?)""",
            ("project", "pilot", "Demo", "captured", "owner", NOW, NOW),
        )
        connection.execute(
            """INSERT INTO content_briefs(id,project_id,brief_no,audience,goal,offer,angle,
               cta_text,platform,target_ref,duration_min_sec,duration_max_sec,constraints_json,
               prompt_version,content_sha256,created_by,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("brief", "project", 1, "a", "g", "o", "x", "cta", "tg", "target",
             30, 45, "[]", "1.0.0", "b" * 64, "owner", NOW),
        )
        connection.execute(
            """INSERT INTO content_revisions(id,project_id,brief_id,revision_no,script_text,
               caption_text,timeline_json,prompt_versions_json,content_sha256,created_by,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            ("revision", "project", "brief", 1, "s", "c", "[]", "{}", HASH, "owner", NOW),
        )


if __name__ == "__main__":
    unittest.main()
