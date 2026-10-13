import sqlite3
import tempfile
import unittest
from pathlib import Path

from db.content_repository import ContentRepository
from db.migrate import run_migrations
from domain.measurement_service import MeasurementService
from reporting.pilot_report import PilotReportBuilder

NOW = "2026-10-13T12:00:00+00:00"


class PilotReportingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "agent.db"
        run_migrations(self.path)
        self.repo = ContentRepository(self.path)
        self.measure = MeasurementService(self.repo)
        self._seed()

    def tearDown(self):
        self.temp.cleanup()

    def _seed(self):
        with sqlite3.connect(self.path) as c:
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("INSERT INTO pilot_passports(id,name,status,timezone,evaluation_mode,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", ("pilot","Pilot","observing","UTC","internal_efficiency",NOW,NOW))
            c.execute("INSERT INTO content_projects(id,pilot_id,title,status,owner_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", ("project","pilot","Demo","observing","owner",NOW,NOW))
            c.execute("INSERT INTO content_briefs(id,project_id,brief_no,audience,goal,offer,angle,cta_text,platform,target_ref,duration_min_sec,duration_max_sec,constraints_json,prompt_version,content_sha256,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("brief","project",1,"a","g","o","x","cta","tg","target",30,45,"[]","1.0.0","b"*64,"owner",NOW))
            c.execute("INSERT INTO content_revisions(id,project_id,brief_id,revision_no,script_text,caption_text,timeline_json,prompt_versions_json,content_sha256,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", ("revision","project","brief",1,"s","c","[]","{}","c"*64,"owner",NOW))
            c.execute("INSERT INTO publish_packages(id,project_id,revision_id,package_sha256,caption_text,assets_json,created_by,created_at) VALUES(?,?,?,?,?,?,?,?)", ("pkg","project","revision","a"*64,"caption","[]","owner",NOW))
            c.execute("INSERT INTO publish_jobs(id,package_id,platform,target_ref,status,external_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", ("job","pkg","telegram","target","published","post",NOW,NOW))
            c.execute("INSERT INTO baselines(id,pilot_id,kind,comparable_format,duration_sec,evidence_ref,created_by,created_at) VALUES(?,?,?,?,?,?,?,?)", ("base","pilot","manual","screen_demo",1000,"evidence","owner",NOW))
        self.measure.add_time(entry_id="t", pilot_id="pilot", project_id="project", actor_id="u", role="editor", stage="production", started_at=NOW, stopped_at=NOW, active_sec=700, rate_minor_per_hour=None, currency=None, created_at=NOW)

    def test_missing_windows_and_partial_costs_are_explicit(self):
        report = PilotReportBuilder(self.repo).build("pilot")
        self.assertEqual(report.decision, "insufficient_data")
        self.assertIn("24h, 72h, 7d", report.markdown)
        self.assertIn("частичные", report.markdown)

    def test_measured_zero_is_not_missing_and_20_percent_target_is_success(self):
        for window in ("24h", "72h", "7d"):
            self.measure.add_metric(snapshot_id=window, publish_job_id="job", window=window, metric_name="views", value=0, measured=True, definition="platform views", source="api", captured_at=NOW, created_at=NOW)
        report = PilotReportBuilder(self.repo).build("pilot")
        self.assertEqual(report.decision, "success")
        self.assertIn("views: 0.0", report.markdown)
        self.assertEqual(report, PilotReportBuilder(self.repo).build("pilot"))

    def test_unknown_publish_outcome_blocks_success(self):
        with sqlite3.connect(self.path) as c:
            c.execute("UPDATE publish_jobs SET status='unknown_outcome' WHERE id='job'")
        for window in ("24h", "72h", "7d"):
            self.measure.add_metric(snapshot_id=window, publish_job_id="job", window=window, metric_name="views", value=1, measured=True, definition="views", source="api", captured_at=NOW, created_at=NOW)
        self.assertEqual(PilotReportBuilder(self.repo).build("pilot").decision, "insufficient_data")


if __name__ == "__main__":
    unittest.main()
