import sqlite3
import tempfile
import unittest
from pathlib import Path

from db.content_repository import ContentRepository
from db.migrate import run_migrations
from domain.analytics_service import AnalyticsService
from domain.errors import ValidationError
from domain.measurement_service import MeasurementService

NOW = "2026-10-13T12:00:00+00:00"


class AnalyticsServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "agent.db"
        run_migrations(self.path)
        self.repo = ContentRepository(self.path)
        self._seed()
        self.service = AnalyticsService(self.repo)

    def tearDown(self):
        self.temp.cleanup()

    def _seed(self):
        with sqlite3.connect(self.path) as c:
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("INSERT INTO pilot_passports(id,name,status,timezone,evaluation_mode,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", ("p","P","observing","UTC","internal_efficiency",NOW,NOW))
            c.execute("INSERT INTO content_projects(id,pilot_id,title,status,owner_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", ("cp","p","D","observing","o",NOW,NOW))
            c.execute("INSERT INTO content_briefs(id,project_id,brief_no,audience,goal,offer,angle,cta_text,platform,target_ref,duration_min_sec,duration_max_sec,constraints_json,prompt_version,content_sha256,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("b","cp",1,"a","g","o","x","c","tg","t",30,45,"[]","1","b"*64,"o",NOW))
            c.execute("INSERT INTO content_revisions(id,project_id,brief_id,revision_no,script_text,caption_text,timeline_json,prompt_versions_json,content_sha256,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", ("r","cp","b",1,"s","c","[]","{}","c"*64,"o",NOW))
            for suffix in ("a","b"):
                c.execute("INSERT INTO publish_packages(id,project_id,revision_id,package_sha256,caption_text,assets_json,created_by,created_at) VALUES(?,?,?,?,?,?,?,?)", (f"pkg{suffix}","cp","r",suffix*64,"c","[]","o",NOW))
                c.execute("INSERT INTO publish_jobs(id,package_id,platform,target_ref,status,external_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", (f"job{suffix}",f"pkg{suffix}","telegram",f"t{suffix}","published",suffix,NOW,NOW))

    def candidates(self):
        return [{"publish_job_id":"joba","platform":"telegram","format":"post","window":"24h","metric_name":"views"},
                {"publish_job_id":"jobb","platform":"telegram","format":"post","window":"24h","metric_name":"views"}]

    def test_missing_metric_produces_hypothesis_not_fact(self):
        result = self.service.compare(learning_id="l1", pilot_id="p", candidates=self.candidates(), created_by="o", created_at=NOW)
        self.assertEqual((result.kind, result.recommendation), ("hypothesis", "test_next"))

    def test_incomparable_groups_are_rejected(self):
        candidates = self.candidates()
        candidates[1]["window"] = "72h"
        with self.assertRaises(ValidationError):
            self.service.compare(learning_id="l", pilot_id="p", candidates=candidates, created_by="o", created_at=NOW)

    def test_clear_winner_becomes_fact_and_repeat_recommendation(self):
        measure = MeasurementService(self.repo)
        for job, value in (("joba", 100), ("jobb", 50)):
            measure.add_metric(snapshot_id=job, publish_job_id=job, window="24h", metric_name="views", value=value, measured=True, definition="views", source="api", captured_at=NOW, created_at=NOW)
        result = self.service.compare(learning_id="l2", pilot_id="p", candidates=self.candidates(), created_by="o", created_at=NOW)
        self.assertEqual((result.winner_job_id, result.kind, result.recommendation), ("joba", "fact", "repeat"))


if __name__ == "__main__":
    unittest.main()
