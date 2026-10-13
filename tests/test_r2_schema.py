import sqlite3
import tempfile
import unittest
from pathlib import Path

from db.migrate import run_migrations


NOW = "2026-10-13T12:00:00+00:00"
HASH = "a" * 64


class R2SchemaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "agent.db"

    def tearDown(self):
        self.temp.cleanup()

    def connect(self):
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def test_migration_chain_reaches_version_five(self):
        self.assertEqual(run_migrations(self.path), [1, 2, 3, 4, 5])

    def test_r2_tables_and_claim_indexes_exist(self):
        run_migrations(self.path)
        expected = {
            "jobs", "external_operations", "budget_limits",
            "budget_reservations", "budget_reservation_scopes",
            "cost_entries", "budget_incidents", "prompt_templates",
            "prompt_runs", "revision_prompt_runs",
        }
        with self.connect() as connection:
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            indexes = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )}
        self.assertTrue(expected <= tables)
        self.assertTrue({"idx_jobs_claimable", "idx_jobs_expired", "idx_operations_reconcile"} <= indexes)

    def test_invalid_status_money_hash_and_version_fail_closed(self):
        run_migrations(self.path)
        with self.connect() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """INSERT INTO jobs(id,command_id,kind,payload_json,payload_sha256,status,
                    available_at,created_at,updated_at,row_version) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    ("j", "c", "llm", "{}", HASH, "lost", NOW, NOW, NOW, 1),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """INSERT INTO budget_limits(id,scope_type,scope_ref,currency,
                    limit_minor,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)""",
                    ("l", "pilot", "p", "RUB", 0, "active", NOW, NOW),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """INSERT INTO prompt_templates(id,name,semantic_version,system_template,
                    user_template,output_schema_json,content_sha256,created_by,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?)""",
                    ("p", "writer", "1.0.0", "s", "u", "{}", "bad", "owner", NOW),
                )

    def test_command_and_provider_evidence_are_unique(self):
        run_migrations(self.path)
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO external_operations(id,command_id,operation_kind,provider_key,
                request_sha256,idempotency_key,status,billing_status,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                ("op", "cmd", "llm", "openrouter", HASH, "idem", "intent", "pending", NOW, NOW),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """INSERT INTO external_operations(id,command_id,operation_kind,provider_key,
                    request_sha256,idempotency_key,status,billing_status,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    ("op2", "cmd", "llm", "openrouter", HASH, "idem2", "intent", "pending", NOW, NOW),
                )
            connection.execute(
                """INSERT INTO cost_entries(id,operation_id,provider,evidence_id,kind,
                amount_minor,currency,occurred_at,evidence_ref,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                ("cost", "op", "openrouter", "bill-1", "charge", 100, "RUB", NOW, "ref", NOW),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """INSERT INTO cost_entries(id,operation_id,provider,evidence_id,kind,
                    amount_minor,currency,occurred_at,evidence_ref,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    ("cost2", "op", "openrouter", "bill-1", "charge", 100, "RUB", NOW, "ref", NOW),
                )

    def test_costs_and_prompt_runs_are_immutable_shapes(self):
        run_migrations(self.path)
        with self.connect() as connection:
            columns = {
                table: {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
                for table in ("cost_entries", "prompt_runs", "revision_prompt_runs")
            }
        for table, names in columns.items():
            self.assertTrue(names, table)
            self.assertNotIn("updated_at", names)


if __name__ == "__main__":
    unittest.main()
