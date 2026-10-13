import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from db.content_repository import ContentRepository
from db.migrate import run_migrations
from domain.budget_service import (
    BudgetConfigurationError, BudgetContext, BudgetService, BudgetUnavailableError,
)
from domain.errors import ConflictError, ValidationError
from domain.cost_service import CostService
from domain.readiness_service import ReadinessService


NOW = "2026-10-13T12:00:00+00:00"
HASH = "a" * 64


class BudgetReservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "agent.db"
        run_migrations(self.path)
        self._seed_ready_revision()
        repository = ContentRepository(self.path)
        self.service = BudgetService(repository, ReadinessService(repository))
        self.context = BudgetContext(
            pilot_id="pilot-1", project_id="project-1", revision_id="revision-1",
            operation_ref="llm", day_ref="2026-10-13", month_ref="2026-10",
        )

    def tearDown(self):
        self.temp.cleanup()

    def _seed_ready_revision(self):
        with sqlite3.connect(self.path) as c:
            c.execute("PRAGMA foreign_keys=ON")
            c.execute(
                """INSERT INTO pilot_passports(id,name,status,timezone,roles_json,
                evaluation_mode,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)""",
                ("pilot-1", "Pilot", "running", "UTC", '{"reviewer":["reviewer"]}',
                 "internal_efficiency", NOW, NOW),
            )
            c.execute(
                """INSERT INTO sources(id,pilot_id,kind,original_ref,captured_at,
                content_sha256,rights_status,rights_evidence_ref,privacy_status,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                ("source-1", "pilot-1", "file", "input", NOW, HASH, "allowed", "consent", "clear", NOW, NOW),
            )
            c.execute(
                """INSERT INTO content_projects(id,pilot_id,title,status,owner_id,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?)""", ("project-1", "pilot-1", "Demo", "script_review", "owner", NOW, NOW),
            )
            c.execute("INSERT INTO content_project_sources VALUES(?,?,?)", ("project-1", "source-1", NOW))
            c.execute(
                """INSERT INTO content_briefs(id,project_id,brief_no,audience,goal,offer,angle,
                cta_text,platform,target_ref,duration_min_sec,duration_max_sec,constraints_json,
                prompt_version,content_sha256,created_by,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("brief-1", "project-1", 1, "a", "g", "o", "x", "cta", "tg", "target",
                 30, 45, "[]", "v1", "b" * 64, "owner", NOW),
            )
            c.execute("INSERT INTO content_brief_sources VALUES(?,?,?)", ("brief-1", "source-1", NOW))
            c.execute(
                """INSERT INTO content_revisions(id,project_id,brief_id,revision_no,script_text,
                caption_text,timeline_json,prompt_versions_json,content_sha256,created_by,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                ("revision-1", "project-1", "brief-1", 1, "S", "C", "[]", "{}", "c" * 64, "owner", NOW),
            )
            c.execute(
                """INSERT INTO reviews(id,project_id,revision_id,stage,subject_sha256,assigned_to,
                status,decision_by,decision_at,checklist_json,created_at,checklist_version)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("review-1", "project-1", "revision-1", "script", "c" * 64, "reviewer",
                 "approved", "reviewer", NOW, "{}", NOW, "script-v1"),
            )
            c.execute("UPDATE content_projects SET current_brief_id=?,current_revision_id=? WHERE id=?",
                      ("brief-1", "revision-1", "project-1"))

    def configure(self, amount=10000):
        values = {
            "operation": "llm", "project": "project-1", "pilot": "pilot-1",
            "day": "2026-10-13", "month": "2026-10",
        }
        for scope, ref in values.items():
            self.service.configure_limit(
                f"limit-{scope}", scope, ref, None, None, "RUB", amount,
                None, "owner", NOW,
            )

    def reserve(self, suffix="1", amount=3000):
        return self.service.reserve(
            f"cmd-{suffix}", f"reservation-{suffix}", f"operation-{suffix}",
            f"job-{suffix}", self.context, amount, "RUB", HASH,
            "openrouter", NOW,
        )

    def test_availability_and_reserve_use_full_exposure_for_every_scope(self):
        self.configure()
        self.reserve(amount=3000)
        balances = self.service.availability(self.context, "RUB", NOW)
        self.assertEqual({key: value["available"] for key, value in balances.items()},
                         {key: 7000 for key in ("operation", "project", "pilot", "day", "month")})

    def test_partial_settlement_counts_charge_plus_remaining_hold_once(self):
        self.configure()
        self.reserve(amount=3000)
        CostService(ContentRepository(self.path)).record_charge(
            "entry-1", "operation-1", "reservation-1", "openrouter", "bill-1",
            1000, "RUB", NOW, final=False,
        )
        balances = self.service.availability(self.context, "RUB", NOW)
        self.assertEqual(
            {key: (value["committed"], value["outstanding"], value["available"])
             for key, value in balances.items()},
            {key: (1000, 2000, 7000)
             for key in ("operation", "project", "pilot", "day", "month")},
        )

    def test_concurrent_reserves_allow_only_one_when_available_is_5000(self):
        self.configure(5000)
        barrier = threading.Barrier(2)
        results = []

        def reserve(suffix):
            barrier.wait()
            try:
                self.reserve(suffix, 3000)
                results.append("ok")
            except BudgetUnavailableError:
                results.append("blocked")

        threads = [threading.Thread(target=reserve, args=(str(n),)) for n in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertCountEqual(results, ["ok", "blocked"])

    def test_command_is_idempotent_and_payload_conflict_fails(self):
        self.configure()
        first = self.reserve()
        again = self.reserve()
        self.assertEqual(first.id, again.id)
        with self.assertRaises(ConflictError):
            self.service.reserve("cmd-1", "different", "other-op", "other-job",
                                 self.context, 4000, "RUB", HASH, "openrouter", NOW)

    def test_missing_or_zero_configuration_and_invalid_amount_fail_closed(self):
        with self.assertRaises(BudgetConfigurationError):
            self.reserve()
        with self.assertRaises(ValidationError):
            self.service.configure_limit("bad", "pilot", "pilot-1", None, None, "RUB", 0, None, "owner", NOW)
        self.configure()
        with self.assertRaises(ValidationError):
            self.reserve(amount=0)

    def test_stale_limit_update_and_currency_mismatch_are_rejected(self):
        self.configure()
        with self.assertRaises(ConflictError):
            self.service.configure_limit("limit-pilot", "pilot", "pilot-1", None, None,
                                         "RUB", 9000, 9, "owner", NOW)
        with self.assertRaises(BudgetConfigurationError):
            self.service.reserve("cmd-x", "r-x", "op-x", "j-x", self.context,
                                 100, "USD", HASH, "openrouter", NOW)

    def test_expired_limit_is_not_used(self):
        self.configure()
        with sqlite3.connect(self.path) as c:
            c.execute(
                "UPDATE budget_limits SET window_end=? WHERE id='limit-day'",
                ("2026-10-13T11:59:00+00:00",),
            )
        with self.assertRaises(BudgetConfigurationError):
            self.reserve()

    def test_stopped_or_not_ready_pilot_rolls_back_every_intent_row(self):
        self.configure()
        with sqlite3.connect(self.path) as c:
            c.execute("UPDATE pilot_passports SET status='stopped' WHERE id='pilot-1'")
        with self.assertRaises(BudgetUnavailableError):
            self.reserve()
        with sqlite3.connect(self.path) as c:
            counts = [c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                      for table in ("external_operations", "budget_reservations", "jobs", "outbox_events")]
        self.assertEqual(counts, [0, 0, 0, 0])

    def test_rights_revocation_inside_reservation_guard_rolls_back(self):
        self.configure()
        with sqlite3.connect(self.path) as c:
            c.execute("UPDATE sources SET rights_status='revoked' WHERE id='source-1'")
        with self.assertRaises(BudgetUnavailableError):
            self.reserve()
        with sqlite3.connect(self.path) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0], 0)

    def test_cross_pilot_lineage_and_untrusted_calendar_scope_are_rejected(self):
        with sqlite3.connect(self.path) as c:
            c.execute(
                """INSERT INTO pilot_passports(id,name,status,timezone,evaluation_mode,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?)""",
                ("pilot-2", "P2", "running", "UTC", "internal_efficiency", NOW, NOW),
            )
        self.configure()
        self.service.configure_limit(
            "limit-pilot-2", "pilot", "pilot-2", None, None, "RUB", 10000, None, "owner", NOW,
        )
        wrong_pilot = BudgetContext(
            "pilot-2", "project-1", "revision-1", "llm", "2026-10-13", "2026-10"
        )
        with self.assertRaises(BudgetUnavailableError):
            self.service.reserve("cross", "cross-r", "cross-op", "cross-job", wrong_pilot,
                                 100, "RUB", HASH, "openrouter", NOW)
        wrong_day = BudgetContext(
            "pilot-1", "project-1", "revision-1", "llm", "2099-01-01", "2099-01"
        )
        with self.assertRaises(BudgetUnavailableError):
            self.service.reserve("date", "date-r", "date-op", "date-job", wrong_day,
                                 100, "RUB", HASH, "openrouter", NOW)


if __name__ == "__main__":
    unittest.main()
