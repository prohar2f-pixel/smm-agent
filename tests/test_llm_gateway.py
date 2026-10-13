import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path

from db.content_repository import ContentRepository
from db.migrate import run_migrations
from domain.budget_service import BudgetContext, BudgetService
from domain.cost_service import CostService, ReconciliationRequiredError
from domain.errors import ConflictError, ValidationError
from domain.readiness_service import ReadinessService
from llm.gateway import StructuredLlmGateway, TransportResponse
from llm.prompt_registry import PromptRegistry


NOW = "2026-10-13T12:00:00+00:00"
HASH = "a" * 64
SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {"title": {"type": "string"}},
    "required": ["title"],
    "additionalProperties": False,
}


class FakeTransport:
    provider_key = "openrouter"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def complete(self, **request):
        self.calls.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def response(text, suffix="1", cost=200, model="model-safe"):
    return TransportResponse(
        text=text, input_tokens=100, output_tokens=25, cost_minor=cost,
        currency="RUB", model=model, latency_ms=120,
        evidence_id=f"evidence-{suffix}", output_ref=f"memory:{suffix}",
    )


class LlmGatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "agent.db"
        run_migrations(self.path)
        self._seed_ready_revision()
        self.repository = ContentRepository(self.path)
        readiness = ReadinessService(self.repository)
        self.budgets = BudgetService(self.repository, readiness)
        self.costs = CostService(self.repository)
        self.registry = PromptRegistry(self.repository)
        self.registry.register(
            "writer", "1.0.0", "System fixed", "Input: {input_json}",
            SCHEMA, "owner", NOW, template_id="prompt-1",
        )
        self.context = BudgetContext(
            "pilot-1", "project-1", "revision-1", "llm", "2026-10-13", "2026-10",
        )
        self._configure_limits()
        self.gateway = StructuredLlmGateway(
            self.repository, self.registry, self.budgets, self.costs,
            pricing={"model-safe": {"currency": "RUB"}}, now=lambda: NOW,
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

    def _configure_limits(self):
        scopes = {"operation": "llm", "project": "project-1", "pilot": "pilot-1",
                  "day": "2026-10-13", "month": "2026-10"}
        for scope, ref in scopes.items():
            self.budgets.configure_limit(
                f"limit-{scope}", scope, ref, None, None, "RUB", 10000, None, "owner", NOW,
            )

    def run_gateway(self, transport, run_id="run-1", **changes):
        values = dict(
            run_id=run_id, purpose="script", prompt_name="writer", prompt_version="1.0.0",
            model="model-safe", input_data={"topic": "automation"},
            budget_context=self.context, upper_bound_minor=1000, currency="RUB",
            transport=transport,
        )
        values.update(changes)
        return asyncio.run(self.gateway.run(**values))

    def test_fail_closed_before_provider_for_price_limit_and_upper_bound(self):
        transport = FakeTransport([response('{"title":"ok"}')])
        self.gateway.pricing = {}
        with self.assertRaises(ValidationError):
            self.run_gateway(transport)
        self.gateway.pricing = {"model-safe": {"currency": "RUB"}}
        with sqlite3.connect(self.path) as c:
            c.execute("DELETE FROM budget_limits WHERE scope_type='day'")
        with self.assertRaises(ValidationError):
            self.run_gateway(transport, run_id="run-2")
        with self.assertRaises(ValidationError):
            self.run_gateway(transport, run_id="run-3", upper_bound_minor=None)
        self.assertEqual(transport.calls, [])

    def test_source_input_cannot_override_control_plane_and_hash_is_canonical(self):
        transport = FakeTransport([response('{"title":"ok"}')])
        malicious = {"model": "evil", "prompt_name": "evil", "proxy": "off",
                     "budget": 999999, "target": "other", "topic": "safe"}
        result = self.run_gateway(transport, input_data=malicious)
        call = transport.calls[0]
        self.assertEqual((call["model"], call["system"], call["attempt"]),
                         ("model-safe", "System fixed", 1))
        self.assertIn('"model":"evil"', call["user"])
        with sqlite3.connect(self.path) as c:
            row = c.execute("SELECT prompt_version,input_sha256 FROM prompt_runs WHERE id=?", (result.run_id,)).fetchone()
        self.assertEqual(row[0], "1.0.0")
        self.assertEqual(row[1], self.gateway.canonical_input_hash(malicious))

    def test_valid_first_response_persists_usage_cost_and_is_idempotent(self):
        transport = FakeTransport([response('{"title":"ok"}', cost=275)])
        first = self.run_gateway(transport)
        again = self.run_gateway(transport)
        self.assertEqual(first, again)
        self.assertEqual((first.status, first.output, len(transport.calls)), ("valid", {"title": "ok"}, 1))
        with sqlite3.connect(self.path) as c:
            row = c.execute(
                "SELECT attempt_count,input_tokens,output_tokens,latency_ms,cost_minor,model,error_code FROM prompt_runs"
            ).fetchone()
        self.assertEqual(row, (1, 100, 25, 120, 275, "model-safe", None))

    def test_confirmed_zero_cost_releases_reservation(self):
        result = self.run_gateway(FakeTransport([response('{"title":"ok"}', cost=0)]))
        self.assertEqual(result.cost_minor, 0)
        with sqlite3.connect(self.path) as c:
            reservation = c.execute(
                "SELECT status,held_minor FROM budget_reservations"
            ).fetchone()
        self.assertEqual(reservation, ("released", 0))

    def test_one_fixed_repair_can_succeed(self):
        transport = FakeTransport([response("bad", "1", 100), response('{"title":"fixed"}', "2", 150)])
        result = self.run_gateway(transport)
        self.assertEqual((result.status, result.output, len(transport.calls)),
                         ("valid", {"title": "fixed"}, 2))
        self.assertEqual(transport.calls[1]["attempt"], 2)
        self.assertIn("Return only JSON matching the schema", transport.calls[1]["user"])

    def test_second_invalid_is_final_and_cannot_link(self):
        transport = FakeTransport([response("bad", "1"), response("still bad", "2")])
        result = self.run_gateway(transport)
        self.assertEqual((result.status, len(transport.calls), result.error_code),
                         ("invalid", 2, "structured_output_invalid"))
        with self.assertRaises(ValidationError):
            self.gateway.link_revision("link-1", "revision-1", result.run_id, NOW)

    def test_timeout_marks_unknown_preserves_reserve_and_duplicate_needs_reconciliation(self):
        transport = FakeTransport([TimeoutError("provider timeout")])
        with self.assertRaises(ReconciliationRequiredError):
            self.run_gateway(transport)
        with sqlite3.connect(self.path) as c:
            operation = c.execute("SELECT status,error_code FROM external_operations").fetchone()
            reservation = c.execute("SELECT status,held_minor FROM budget_reservations").fetchone()
        self.assertEqual(operation, ("unknown_outcome", "transport_timeout"))
        self.assertEqual(reservation, ("unknown", 1000))
        with self.assertRaises(ReconciliationRequiredError):
            self.run_gateway(transport)
        self.assertEqual(len(transport.calls), 1)

    def test_valid_run_links_once_only_to_reserved_revision(self):
        result = self.run_gateway(FakeTransport([response('{"title":"ok"}')]))
        self.gateway.link_revision("link-1", "revision-1", result.run_id, NOW)
        with self.assertRaises(ConflictError):
            self.gateway.link_revision("link-2", "revision-1", result.run_id, NOW)
        with self.assertRaises(ValidationError):
            self.gateway.link_revision("link-3", "different", result.run_id, NOW)


if __name__ == "__main__":
    unittest.main()
