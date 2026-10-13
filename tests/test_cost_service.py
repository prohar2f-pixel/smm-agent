import sqlite3
import tempfile
import unittest
from pathlib import Path

from db.content_repository import ContentRepository
from db.migrate import run_migrations
from domain.cost_service import CostService, ReconciliationRequiredError
from domain.errors import ConflictError, ValidationError


NOW = "2026-10-13T12:00:00+00:00"
HASH = "a" * 64


class CostServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "agent.db"
        run_migrations(self.path)
        with sqlite3.connect(self.path) as c:
            c.execute("PRAGMA foreign_keys=ON")
            c.execute(
                """INSERT INTO pilot_passports(id,name,status,timezone,evaluation_mode,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?)""", ("pilot", "P", "running", "UTC", "internal_efficiency", NOW, NOW),
            )
            c.execute(
                """INSERT INTO external_operations(id,command_id,pilot_id,operation_kind,
                provider_key,request_sha256,idempotency_key,status,billing_status,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                ("op", "cmd", "pilot", "llm", "openrouter", HASH, "cmd", "submitting", "pending", NOW, NOW),
            )
            c.execute(
                """INSERT INTO budget_reservations(id,command_id,operation_id,pilot_id,
                upper_bound_minor,currency,status,held_minor,created_at,updated_at)
                VALUES(?,?,?,?,?,?,'held',?,?,?)""",
                ("reservation", "cmd", "op", "pilot", 3000, "RUB", 3000, NOW, NOW),
            )
        self.service = CostService(ContentRepository(self.path))

    def tearDown(self):
        self.temp.cleanup()

    def charge(self, amount, evidence="bill-1", final=True):
        return self.service.record_charge(
            f"entry-{evidence}", "op", "reservation", "openrouter", evidence,
            amount, "RUB", NOW, final=final,
        )

    def test_final_charge_releases_unused_reserve(self):
        result = self.charge(2500)
        self.assertEqual((result.committed_minor, result.held_minor, result.status),
                         (2500, 0, "settled"))

    def test_partial_charge_keeps_remaining_exposure(self):
        result = self.charge(1000, final=False)
        self.assertEqual((result.committed_minor, result.held_minor, result.status),
                         (1000, 2000, "partially_settled"))

    def test_unknown_keeps_entire_remaining_reserve(self):
        result = self.service.mark_unknown("op", NOW)
        self.assertEqual((result.held_minor, result.status), (3000, "unknown"))
        with self.assertRaises(ReconciliationRequiredError):
            self.service.mark_no_charge("op", "guess", NOW)

    def test_overspend_is_recorded_and_opens_incident(self):
        result = self.charge(3500)
        self.assertEqual((result.committed_minor, result.held_minor, result.status),
                         (3500, 0, "overspent"))
        with sqlite3.connect(self.path) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM budget_incidents WHERE status='open'").fetchone()[0], 1)

    def test_duplicate_evidence_is_idempotent_but_contradiction_conflicts(self):
        first = self.charge(2500)
        again = self.charge(2500)
        self.assertEqual(first, again)
        with self.assertRaises(ConflictError):
            self.charge(2400)

    def test_confirmed_refund_reduces_net_once_and_cannot_exceed_charge(self):
        self.charge(2500)
        result = self.service.record_refund(
            "refund-entry", "op", "reservation", "openrouter", "refund-1",
            500, "RUB", NOW,
        )
        self.assertEqual(result.committed_minor, 2000)
        again = self.service.record_refund(
            "other-id", "op", "reservation", "openrouter", "refund-1",
            500, "RUB", NOW,
        )
        self.assertEqual(again.committed_minor, 2000)
        with self.assertRaises(ValidationError):
            self.service.record_refund(
                "too-much", "op", "reservation", "openrouter", "refund-2",
                2500, "RUB", NOW,
            )

    def test_confirmed_no_charge_releases_and_reconciliation_is_idempotent(self):
        released = self.service.reconcile("op", "no_charge", "provider:absent", NOW)
        self.assertEqual((released.held_minor, released.status), (0, "released"))
        again = self.service.reconcile("op", "no_charge", "provider:absent", NOW)
        self.assertEqual(again, released)

    def test_refund_rejects_cross_operation_provider_and_currency(self):
        self.charge(1000)
        for operation, provider, currency in (
            ("other", "openrouter", "RUB"), ("op", "other", "RUB"), ("op", "openrouter", "USD")
        ):
            with self.subTest(operation=operation, provider=provider, currency=currency):
                with self.assertRaises(ValidationError):
                    self.service.record_refund(
                        f"refund-{operation}-{provider}-{currency}", operation, "reservation",
                        provider, f"evidence-{operation}-{provider}-{currency}", 100,
                        currency, NOW,
                    )

    def test_no_charge_after_confirmed_charge_opens_incident(self):
        self.charge(1000)
        with self.assertRaises(ReconciliationRequiredError):
            self.service.reconcile("op", "no_charge", "provider:none", NOW)
        with sqlite3.connect(self.path) as c:
            self.assertEqual(c.execute(
                "SELECT kind,status FROM budget_incidents"
            ).fetchone(), ("contradictory_evidence", "open"))


if __name__ == "__main__":
    unittest.main()
