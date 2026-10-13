import sqlite3
import tempfile
import unittest
from pathlib import Path

from db.migrate import run_migrations


NOW = "2026-10-12T12:00:00+00:00"
HASH_A = "a" * 64


class R1SchemaTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "agent.db"

    def tearDown(self):
        self.temp_dir.cleanup()

    def connect(self):
        connection = sqlite3.connect(self.db_path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def migrate_and_seed(self):
        run_migrations(self.db_path)
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO pilot_passports(
                    id, name, status, timezone, audience, offer, platform,
                    target_ref, publish_mode, roles_json, cta_text,
                    qualification_rules_json, thresholds_json,
                    working_calendar_json, labor_rates_json,
                    allocation_policy_json, evaluation_mode, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "pilot-1", "Pilot A", "draft", "Europe/Moscow",
                    "founders", "AI audit", "telegram", "channel-1", "manual",
                    "{}", "Write us", "{}", "{}", "{}", "{}", "{}",
                    "internal_efficiency", NOW, NOW,
                ),
            )
            connection.execute(
                """
                INSERT INTO sources(
                    id, pilot_id, kind, original_ref, captured_at,
                    content_sha256, rights_status, privacy_status,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "source-1", "pilot-1", "file", "screen.mp4", NOW,
                    HASH_A, "allowed", "clear", NOW, NOW,
                ),
            )
            connection.execute(
                """
                INSERT INTO content_projects(
                    id, pilot_id, title, status, owner_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("project-1", "pilot-1", "Screen demo", "captured", "owner-1", NOW, NOW),
            )
            connection.execute(
                "INSERT INTO content_project_sources(project_id, source_id, created_at) VALUES (?, ?, ?)",
                ("project-1", "source-1", NOW),
            )
            connection.execute(
                """
                INSERT INTO content_briefs(
                    id, project_id, brief_no, audience, goal, offer, angle,
                    cta_text, platform, target_ref, duration_min_sec,
                    duration_max_sec, constraints_json, prompt_version,
                    content_sha256, created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "brief-1", "project-1", 1, "founders", "lead", "AI audit",
                    "show proof", "Write us", "telegram", "channel-1", 30, 45,
                    "[]", "brief-v1", "b" * 64, "owner-1", NOW,
                ),
            )
            connection.execute(
                "INSERT INTO content_brief_sources(brief_id, source_id, created_at) VALUES (?, ?, ?)",
                ("brief-1", "source-1", NOW),
            )
            connection.execute(
                """
                INSERT INTO content_revisions(
                    id, project_id, brief_id, revision_no, script_text,
                    caption_text, timeline_json, prompt_versions_json,
                    content_sha256, created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "revision-1", "project-1", "brief-1", 1, "Script", "Caption",
                    "[]", "{}", "c" * 64, "owner-1", NOW,
                ),
            )
            connection.execute(
                """
                UPDATE content_projects
                SET current_brief_id = ?, current_revision_id = ?
                WHERE id = ?
                """,
                ("brief-1", "revision-1", "project-1"),
            )

    def test_migration_chain_includes_current_version(self):
        applied = run_migrations(self.db_path)

        self.assertEqual(applied, list(range(1, 12)))

    def test_foreign_keys_reject_missing_pilot_and_revision(self):
        run_migrations(self.db_path)
        with self.connect() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO content_projects(
                        id, pilot_id, title, status, owner_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    ("project-x", "missing", "X", "captured", "owner", NOW, NOW),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO claims(id, revision_id, text, location_json, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    ("claim-x", "missing", "Claim", "{}", "unverified", NOW),
                )

    def test_parent_scoped_numbers_and_links_are_unique(self):
        self.migrate_and_seed()
        with self.connect() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO content_project_sources(project_id, source_id, created_at) VALUES (?, ?, ?)",
                    ("project-1", "source-1", NOW),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO content_briefs(
                        id, project_id, brief_no, audience, goal, offer, angle,
                        cta_text, platform, target_ref, duration_min_sec,
                        duration_max_sec, constraints_json, prompt_version,
                        content_sha256, created_by, created_at
                    ) SELECT ?, project_id, brief_no, audience, goal, offer, angle,
                             cta_text, platform, target_ref, duration_min_sec,
                             duration_max_sec, constraints_json, prompt_version,
                             content_sha256, created_by, created_at
                      FROM content_briefs WHERE id = ?
                    """,
                    ("brief-duplicate", "brief-1"),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO content_revisions(
                        id, project_id, brief_id, revision_no, script_text,
                        caption_text, timeline_json, prompt_versions_json,
                        content_sha256, created_by, created_at
                    ) SELECT ?, project_id, brief_id, revision_no, script_text,
                             caption_text, timeline_json, prompt_versions_json,
                             content_sha256, created_by, created_at
                      FROM content_revisions WHERE id = ?
                    """,
                    ("revision-duplicate", "revision-1"),
                )

    def test_enums_and_row_versions_reject_invalid_values(self):
        run_migrations(self.db_path)
        with self.connect() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO pilot_passports(
                        id, name, status, timezone, roles_json,
                        qualification_rules_json, thresholds_json,
                        working_calendar_json, labor_rates_json,
                        allocation_policy_json, evaluation_mode,
                        created_at, updated_at, row_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "bad-pilot", "Bad", "flying", "UTC", "{}", "{}", "{}",
                        "{}", "{}", "{}", "internal_efficiency", NOW, NOW, 0,
                    ),
                )

        self.migrate_and_seed()
        with self.connect() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE sources SET privacy_status='public' WHERE id='source-1'"
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO claims(id, revision_id, text, location_json, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    ("claim-bad", "revision-1", "Claim", "{}", "certain", NOW),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO reviews(
                        id, project_id, revision_id, stage, subject_sha256,
                        assigned_to, status, checklist_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "review-bad", "project-1", "revision-1", "script",
                        "c" * 64, "reviewer", "maybe", "{}", NOW,
                    ),
                )

    def test_append_only_records_do_not_require_updated_at(self):
        run_migrations(self.db_path)
        immutable_tables = (
            "content_briefs",
            "content_revisions",
            "claims",
            "claim_evidence",
            "reviews",
        )
        with self.connect() as connection:
            columns = {
                table: {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
                for table in immutable_tables
            }

        for table in immutable_tables:
            self.assertTrue(columns[table], f"missing table: {table}")
            self.assertNotIn("updated_at", columns[table])

    def test_referenced_pilot_source_and_revision_cannot_be_deleted(self):
        self.migrate_and_seed()
        with self.connect() as connection:
            for table, record_id in (
                ("sources", "source-1"),
                ("content_revisions", "revision-1"),
                ("pilot_passports", "pilot-1"),
            ):
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(f"DELETE FROM {table} WHERE id = ?", (record_id,))

    def test_database_guards_reject_cross_scope_links(self):
        self.migrate_and_seed()
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO pilot_passports(
                id,name,status,timezone,evaluation_mode,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?)""",
                ("pilot-2", "Other", "draft", "UTC", "internal_efficiency", NOW, NOW),
            )
            connection.execute(
                """INSERT INTO sources(
                id,pilot_id,kind,original_ref,captured_at,content_sha256,
                rights_status,rights_evidence_ref,privacy_status,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                ("source-2", "pilot-2", "file", "other", NOW, "d" * 64,
                 "allowed", "consent", "clear", NOW, NOW),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO content_project_sources(project_id,source_id,created_at) VALUES(?,?,?)",
                    ("project-1", "source-2", NOW),
                )
            connection.execute(
                """INSERT INTO claims(id,revision_id,text,location_json,status,checked_by,checked_at,created_at)
                VALUES(?,?,?,?,?,?,?,?)""",
                ("claim-1", "revision-1", "Fact", "{}", "supported", "checker", NOW, NOW),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """INSERT INTO claim_evidence(
                    id,claim_id,source_id,excerpt,applicability_note,checked_by,checked_at,created_at
                    ) VALUES(?,?,?,?,?,?,?,?)""",
                    ("e-1", "claim-1", "source-2", "text", "note", "checker", NOW, NOW),
                )


if __name__ == "__main__":
    unittest.main()
