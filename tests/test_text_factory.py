import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from db.content_repository import ContentRepository
from db.migrate import run_migrations
from domain.campaign_service import CampaignService
from domain.errors import ValidationError
from domain.idea_service import IdeaService
from domain.text_factory import TextFactory

NOW = "2026-10-13T12:00:00+00:00"


class FakeGateway:
    def generate(self, payload):
        return {"master_text": "Master", "facts": ["fact"],
                "hypotheses": ["hypothesis"],
                "source_trace": [{"source_id": payload["source_id"], "excerpt": "proof"}],
                "variants": {"telegram": "TG", "vk": "VK"}}


class TextFactoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "agent.db"
        run_migrations(self.path)
        self.repo = ContentRepository(self.path)
        with sqlite3.connect(self.path) as c:
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("INSERT INTO pilot_passports(id,name,status,timezone,evaluation_mode,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", ("pilot","Pilot","running","UTC","internal_efficiency",NOW,NOW))
            c.execute("INSERT INTO sources(id,pilot_id,kind,original_ref,captured_at,content_sha256,rights_status,rights_evidence_ref,privacy_status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", ("source","pilot","text","note",NOW,"a"*64,"allowed","consent","clear",NOW,NOW))
        self.ideas = IdeaService(self.repo)
        self.ideas.capture(idea_id="idea", pilot_id="pilot", source_id="source", angle="angle", role="education", proof_summary="proof", format="post", created_at=NOW)

    def tearDown(self):
        self.temp.cleanup()

    def test_no_text_without_scored_idea_and_scoring_keeps_provenance(self):
        with self.assertRaises(ValidationError):
            TextFactory(self.repo, FakeGateway()).generate(output_id="o", idea_id="idea", created_by="u", created_at=NOW)
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
        digest = TextFactory(self.repo, FakeGateway()).generate(output_id="out", idea_id="idea", created_by="u", created_at=NOW)
        self.assertEqual(len(digest), 64)
        variants = self.repo.fetchall("SELECT platform,text FROM text_variants ORDER BY platform")
        self.assertEqual([(r["platform"], r["text"]) for r in variants], [("telegram","TG"),("vk","VK")])


if __name__ == "__main__":
    unittest.main()
