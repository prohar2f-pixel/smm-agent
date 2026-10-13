import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from db.content_repository import ContentRepository
from db.migrate import run_migrations
from domain.campaign_service import CampaignService
from domain.budget_service import BudgetContext, BudgetService
from domain.cost_service import CostService, ReconciliationRequiredError
from domain.errors import ValidationError
from domain.idea_service import IdeaService
from domain.text_factory import TextFactory
from domain.readiness_service import ReadinessService
from llm.gateway import StructuredLlmGateway, TransportResponse
from llm.prompt_registry import PromptRegistry

NOW = "2026-10-13T12:00:00+00:00"


OUTPUT = {"master_text": "Master", "facts": ["fact"],
          "hypotheses": ["hypothesis"],
          "source_trace": [{"source_id": "source", "excerpt": "proof"}],
          "variants": {"telegram": "TG", "vk": "VK"}}
SCHEMA = {"$schema":"https://json-schema.org/draft/2020-12/schema", "type":"object",
          "properties": {"master_text":{"type":"string"},
                         "facts":{"type":"array","items":{"type":"string"}},
                         "hypotheses":{"type":"array","items":{"type":"string"}},
                         "source_trace":{"type":"array","items":{"type":"object",
                             "properties":{"source_id":{"type":"string"},"excerpt":{"type":"string"}},
                             "required":["source_id","excerpt"],"additionalProperties":False}},
                         "variants":{"type":"object","properties":{"telegram":{"type":"string"},"vk":{"type":"string"}},
                                     "required":["telegram","vk"],"additionalProperties":False}},
          "required":["master_text","facts","hypotheses","source_trace","variants"],
          "additionalProperties":False}


class FakeTransport:
    provider_key = "openrouter"
    def __init__(self, response=OUTPUT):
        self.response = response
        self.calls = []
    async def complete(self, **request):
        self.calls.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        return TransportResponse(json.dumps(self.response), 100, 50, 100, "RUB",
                                 "safe-model", 100, "evidence-1", "memory:1")


class TextFactoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "agent.db"
        run_migrations(self.path)
        self.repo = ContentRepository(self.path)
        with sqlite3.connect(self.path) as c:
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("INSERT INTO pilot_passports(id,name,status,timezone,roles_json,evaluation_mode,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", ("pilot","Pilot","running","UTC",'{"reviewer":["reviewer"]}',"internal_efficiency",NOW,NOW))
            c.execute("INSERT INTO sources(id,pilot_id,kind,original_ref,captured_at,content_sha256,rights_status,rights_evidence_ref,privacy_status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", ("source","pilot","text","note",NOW,"a"*64,"allowed","consent","clear",NOW,NOW))
            c.execute("INSERT INTO content_projects(id,pilot_id,title,status,owner_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", ("project","pilot","Demo","script_review","owner",NOW,NOW))
            c.execute("INSERT INTO content_project_sources(project_id,source_id,created_at) VALUES(?,?,?)", ("project","source",NOW))
            c.execute("INSERT INTO content_briefs(id,project_id,brief_no,audience,goal,offer,angle,cta_text,platform,target_ref,duration_min_sec,duration_max_sec,constraints_json,prompt_version,content_sha256,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("brief","project",1,"a","g","o","x","cta","telegram","target",30,45,"[]","1","b"*64,"owner",NOW))
            c.execute("INSERT INTO content_brief_sources(brief_id,source_id,created_at) VALUES(?,?,?)", ("brief","source",NOW))
            c.execute("INSERT INTO content_revisions(id,project_id,brief_id,revision_no,script_text,caption_text,timeline_json,prompt_versions_json,content_sha256,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", ("revision","project","brief",1,"s","c","[]","{}","c"*64,"owner",NOW))
            c.execute("INSERT INTO reviews(id,project_id,revision_id,stage,subject_sha256,assigned_to,status,decision_by,decision_at,checklist_json,created_at,checklist_version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", ("review","project","revision","script","c"*64,"reviewer","approved","reviewer",NOW,"{}",NOW,"1"))
            c.execute("UPDATE content_projects SET current_brief_id='brief',current_revision_id='revision' WHERE id='project'")
        self.ideas = IdeaService(self.repo)
        self.ideas.capture(idea_id="idea", pilot_id="pilot", source_id="source", angle="angle", role="education", proof_summary="proof", format="post", created_at=NOW, project_id="project")
        readiness = ReadinessService(self.repo)
        budgets = BudgetService(self.repo, readiness)
        for scope, ref in {"operation":"llm", "project":"project", "pilot":"pilot",
                           "day":"2026-10-13", "month":"2026-10"}.items():
            budgets.configure_limit(f"limit-{scope}", scope, ref, None, None,
                                    "RUB", 10000, None, "owner", NOW)
        registry = PromptRegistry(self.repo)
        registry.register("text_factory", "1.0.0", "Write", "{input_json}",
                          SCHEMA, "owner", NOW, template_id="text-prompt")
        self.gateway = StructuredLlmGateway(
            self.repo, registry, budgets, CostService(self.repo),
            pricing={"safe-model":{"currency":"RUB","input_per_million_minor":1000,
                                    "output_per_million_minor":1000,"max_input_tokens":1000,
                                    "max_output_tokens":1000,"exchange_basis":"provider"}},
            now=lambda: NOW,
        )
        self.context = BudgetContext("pilot","project","revision","llm",
                                     "2026-10-13","2026-10")

    def tearDown(self):
        self.temp.cleanup()

    def test_no_text_without_scored_idea_and_scoring_keeps_provenance(self):
        with self.assertRaises(ValidationError):
            asyncio.run(self.generate("o", FakeTransport()))
        scores = {"relevance": 3, "proof": 3, "novelty": 2, "fit": 3, "effort": 1}
        row = self.ideas.score("idea", scores, rubric_version="1.0.0", evidence={"proof": "source"}, at=NOW)
        self.assertEqual(row["total_score"], 12)
        self.assertEqual(json.loads(row["scoring_json"])["evidence"], {"proof": "source"})

    def test_14_day_campaign_has_unique_capacity_bound_slots(self):
        self.ideas.score("idea", {"relevance":3,"proof":3,"novelty":3,"fit":3,"effort":3}, rubric_version="1", evidence={}, at=NOW)
        campaigns = CampaignService(self.repo)
        campaigns.create(campaign_id="campaign", pilot_id="pilot", name="C", goal="G", start_day="2026-10-13", end_day="2026-10-26", capacity=1, formats_json="[]", created_at=NOW)
        campaigns.add_slot(slot_id="slot", campaign_id="campaign", publish_day="2026-10-13", slot_no=1, idea_id="idea", platform="telegram", created_at=NOW)
        with self.assertRaises(ValidationError):
            campaigns.add_slot(slot_id="slot2", campaign_id="campaign", publish_day="2026-10-14", slot_no=1, idea_id="idea", platform="vk", created_at=NOW)
        with self.assertRaises(ValidationError):
            campaigns.create(campaign_id="long", pilot_id="pilot", name="L", goal="G", start_day="2026-10-01", end_day="2026-10-20", capacity=2, formats_json="[]", created_at=NOW)

    def test_master_facts_hypotheses_trace_and_platform_variants_are_immutable(self):
        self.ideas.score("idea", {"relevance":3,"proof":3,"novelty":2,"fit":3,"effort":1}, rubric_version="1", evidence={}, at=NOW)
        transport = FakeTransport()
        digest = asyncio.run(self.generate("out", transport))
        self.assertEqual(len(digest), 64)
        variants = self.repo.fetchall("SELECT platform,text FROM text_variants ORDER BY platform")
        self.assertEqual([(r["platform"], r["text"]) for r in variants], [("telegram","TG"),("vk","VK")])
        self.assertEqual(asyncio.run(self.generate("out", transport)), digest)
        self.assertEqual(len(transport.calls), 1)

    def test_feature_gate_and_timeout_fail_without_output(self):
        self.ideas.score("idea", {"relevance":3,"proof":3,"novelty":2,"fit":3,"effort":1}, rubric_version="1", evidence={}, at=NOW)
        with self.assertRaises(ValidationError):
            asyncio.run(self.generate("disabled", FakeTransport(), enabled=False))
        transport = FakeTransport(TimeoutError("timeout"))
        with self.assertRaises(ReconciliationRequiredError):
            asyncio.run(self.generate("unknown", transport, command_id="unknown-run"))
        self.assertIsNone(self.repo.fetchone("SELECT 1 FROM text_outputs WHERE id='unknown'"))
        with self.assertRaises(ReconciliationRequiredError):
            asyncio.run(self.generate("unknown", transport, command_id="unknown-run"))
        self.assertEqual(len(transport.calls), 1)

    def test_budget_context_must_match_idea_project_and_revision(self):
        self.ideas.score("idea", {"relevance":3,"proof":3,"novelty":2,"fit":3,"effort":1}, rubric_version="1", evidence={}, at=NOW)
        wrong_project = BudgetContext("pilot", "other-project", "revision", "llm",
                                      "2026-10-13", "2026-10")
        with self.assertRaises(ValidationError):
            asyncio.run(TextFactory(self.repo, self.gateway, enabled=True).generate(
                output_id="wrong", command_id="wrong-run", idea_id="idea",
                created_by="u", created_at=NOW, prompt_version="1.0.0",
                model="safe-model", budget_context=wrong_project,
                upper_bound_minor=1000, currency="RUB", transport=FakeTransport(),
            ))

    def generate(self, output_id, transport, *, enabled=True, command_id="text-run"):
        return TextFactory(self.repo, self.gateway, enabled=enabled).generate(
            output_id=output_id, command_id=command_id, idea_id="idea",
            created_by="u", created_at=NOW, prompt_version="1.0.0",
            model="safe-model", budget_context=self.context, upper_bound_minor=1000,
            currency="RUB", transport=transport,
        )


if __name__ == "__main__":
    unittest.main()
