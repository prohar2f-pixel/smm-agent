import sqlite3
import tempfile
import unittest
from pathlib import Path

from db.content_repository import ContentRepository
from db.migrate import run_migrations
from domain.measurement_service import MeasurementService
from reporting.pilot_report import PilotReportBuilder
from domain.publish_service import PublishService

NOW = "2026-10-13T12:00:00+00:00"
CAPTURED = {"24h":"2026-10-14T12:00:00+00:00",
            "72h":"2026-10-16T12:00:00+00:00",
            "7d":"2026-10-20T12:00:00+00:00"}
QA_CODES = (
    "source_traceable", "rights_allowed", "privacy_clear",
    "claims_supported_or_labeled", "script_approved", "duration_in_profile",
    "media_readable", "profile_matches", "subtitles_match_voice",
    "text_legible_safe_zones", "audio_clear", "cta_single_and_working",
    "no_critical_artifacts", "package_hash_verified",
)


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
            c.execute("INSERT INTO pilot_passports(id,name,status,timezone,evaluation_mode,target_count,thresholds_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)", ("pilot","Pilot","observing","UTC","internal_efficiency",1,'{"required_metrics":["views"]}',NOW,NOW))
            c.execute("INSERT INTO content_projects(id,pilot_id,title,status,owner_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", ("project","pilot","Demo","observing","owner",NOW,NOW))
            c.execute("INSERT INTO sources(id,pilot_id,kind,original_ref,captured_at,content_sha256,rights_status,privacy_status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)", ("source","pilot","file","real-source",NOW,"f"*64,"allowed","clear",NOW,NOW))
            c.execute("INSERT INTO content_project_sources(project_id,source_id,created_at) VALUES(?,?,?)", ("project","source",NOW))
            c.execute("INSERT INTO content_briefs(id,project_id,brief_no,audience,goal,offer,angle,cta_text,platform,target_ref,duration_min_sec,duration_max_sec,constraints_json,prompt_version,content_sha256,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("brief","project",1,"a","g","o","x","cta","tg","target",30,45,"[]","1.0.0","b"*64,"owner",NOW))
            c.execute("INSERT INTO content_revisions(id,project_id,brief_id,revision_no,script_text,caption_text,timeline_json,prompt_versions_json,content_sha256,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", ("revision","project","brief",1,"s","c","[]","{}","c"*64,"owner",NOW))
            c.execute("INSERT INTO assets(id,project_id,revision_id,role,storage_key,sha256,mime_type,size_bytes,duration_ms,width,height,rights_status,privacy_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("master","project","revision","master","master.mp4","d"*64,"video/mp4",1,36000,1080,1920,"allowed","clear",NOW))
            c.execute("INSERT INTO approval_decisions(id,project_id,revision_id,stage,subject_sha256,status,actor_id,actor_role,checklist_version,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)", ("final","project","revision","final","d"*64,"approved","owner","owner","1",NOW))
            c.execute("INSERT INTO publish_packages(id,project_id,revision_id,package_sha256,caption_text,assets_json,created_by,created_at) VALUES(?,?,?,?,?,?,?,?)", ("pkg","project","revision",PublishService.package_hash("caption", '["master"]'),"caption",'["master"]',"owner",NOW))
            c.execute("INSERT INTO publish_jobs(id,package_id,platform,target_ref,status,external_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", ("job","pkg","telegram","target","published","post",NOW,NOW))
            c.execute("INSERT INTO baselines(id,pilot_id,kind,comparable_format,duration_sec,evidence_ref,created_by,created_at) VALUES(?,?,?,?,?,?,?,?)", ("base","pilot","manual","screen_demo",1000,"evidence","owner",NOW))
            for index, code in enumerate(QA_CODES):
                c.execute("INSERT INTO qa_results(id,project_id,revision_id,asset_id,stage,check_code,status,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?)", (f"qa-{index}","project","revision","master","final",code,"pass","reviewer",NOW))
        self.measure.add_time(entry_id="t", pilot_id="pilot", project_id="project", actor_id="u", role="editor", stage="production", started_at=NOW, stopped_at=NOW, active_sec=700, rate_minor_per_hour=1000, currency="RUB", created_at=NOW)

    def test_missing_windows_and_partial_costs_are_explicit(self):
        report = PilotReportBuilder(self.repo).build("pilot")
        self.assertEqual(report.decision, "insufficient_data")
        self.assertIn("job:24h:views, job:72h:views, job:7d:views", report.markdown)
        self.assertIn("полные", report.markdown)

    def test_measured_zero_is_not_missing_and_20_percent_target_is_success(self):
        for window in ("24h", "72h", "7d"):
            self.measure.add_metric(snapshot_id=window, publish_job_id="job", window=window, metric_name="views", value=0, measured=True, definition="platform views", source="api", captured_at=CAPTURED[window], created_at=CAPTURED[window])
        report = PilotReportBuilder(self.repo).build("pilot")
        self.assertEqual(report.decision, "success")
        self.assertIn("views: 0.0", report.markdown)
        self.assertEqual(report, PilotReportBuilder(self.repo).build("pilot"))

    def test_unknown_publish_outcome_blocks_success(self):
        with sqlite3.connect(self.path) as c:
            c.execute("UPDATE publish_jobs SET status='unknown_outcome' WHERE id='job'")
        for window in ("24h", "72h", "7d"):
            self.measure.add_metric(snapshot_id=window, publish_job_id="job", window=window, metric_name="views", value=1, measured=True, definition="views", source="api", captured_at=CAPTURED[window], created_at=CAPTURED[window])
        self.assertEqual(PilotReportBuilder(self.repo).build("pilot").decision, "insufficient_data")

    def test_zero_time_entries_can_never_be_reported_as_success(self):
        with sqlite3.connect(self.path) as c:
            c.execute("DELETE FROM time_entries WHERE pilot_id='pilot'")
        for window in ("24h", "72h", "7d"):
            self.measure.add_metric(
                snapshot_id=f"zero-time-{window}", publish_job_id="job", window=window,
                metric_name="views", value=1, measured=True, definition="views",
                source="api", captured_at=CAPTURED[window], created_at=CAPTURED[window],
            )
        report = PilotReportBuilder(self.repo).build("pilot")
        self.assertEqual(report.decision, "insufficient_data")
        self.assertIn("Трудозатраты: нет данных (частичные)", report.markdown)

    def test_each_published_job_needs_every_window_and_measured_null_is_rejected(self):
        with sqlite3.connect(self.path) as c:
            c.execute("UPDATE pilot_passports SET target_count=2 WHERE id='pilot'")
            c.execute("INSERT INTO publish_packages(id,project_id,revision_id,package_sha256,caption_text,assets_json,created_by,created_at) VALUES(?,?,?,?,?,?,?,?)", ("pkg2","project","revision","e"*64,"caption2","[]","owner",NOW))
            c.execute("INSERT INTO publish_jobs(id,package_id,platform,target_ref,status,external_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", ("job2","pkg2","telegram","target2","published","post2",NOW,NOW))
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("INSERT INTO metric_snapshots(id,publish_job_id,window,metric_name,value_numeric,is_measured,definition,source,captured_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)", ("bad","job","24h","views",None,1,"views","api",NOW,NOW))
        for job in ("job", "job2"):
            for window in ("24h", "72h", "7d"):
                if job == "job2" and window == "7d":
                    continue
                self.measure.add_metric(snapshot_id=f"{job}-{window}", publish_job_id=job,
                                        window=window, metric_name="views", value=1,
                                        measured=True, definition="views", source="api",
                                        captured_at=CAPTURED[window], created_at=CAPTURED[window])
        report = PilotReportBuilder(self.repo).build("pilot")
        self.assertEqual(report.decision, "insufficient_data")
        self.assertIn("job2:7d:views", report.markdown)

    def test_two_platform_jobs_for_one_package_do_not_satisfy_two_package_target(self):
        with sqlite3.connect(self.path) as c:
            c.execute("UPDATE pilot_passports SET target_count=2 WHERE id='pilot'")
            c.execute("INSERT INTO publish_jobs(id,package_id,platform,target_ref,status,external_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", ("job-vk","pkg","vk","target-vk","published","post-vk",NOW,NOW))
        for job in ("job", "job-vk"):
            for window in ("24h", "72h", "7d"):
                self.measure.add_metric(snapshot_id=f"{job}-{window}", publish_job_id=job,
                                        window=window, metric_name="views", value=1,
                                        measured=True, definition="views", source="api",
                                        captured_at=CAPTURED[window], created_at=CAPTURED[window])
        report = PilotReportBuilder(self.repo).build("pilot")
        self.assertEqual(report.decision, "insufficient_data")
        self.assertIn("Принято пакетов: 1 из 2", report.markdown)

    def test_metric_captured_before_window_matures_is_missing(self):
        for window in ("24h", "72h", "7d"):
            self.measure.add_metric(snapshot_id=f"early-{window}", publish_job_id="job",
                                    window=window, metric_name="views", value=1,
                                    measured=True, definition="views", source="api",
                                    captured_at=NOW, created_at=NOW)
        report = PilotReportBuilder(self.repo).build("pilot")
        self.assertEqual(report.decision, "insufficient_data")
        self.assertIn("job:24h:views", report.markdown)

    def test_out_of_profile_master_duration_cannot_be_accepted(self):
        with sqlite3.connect(self.path) as c:
            c.execute("DROP TRIGGER immutable_asset_evidence_update")
            c.execute("UPDATE assets SET duration_ms=20000 WHERE id='master'")
        for window in ("24h", "72h", "7d"):
            self.measure.add_metric(snapshot_id=f"duration-{window}", publish_job_id="job",
                                    window=window, metric_name="views", value=1,
                                    measured=True, definition="views", source="api",
                                    captured_at=CAPTURED[window], created_at=CAPTURED[window])
        self.assertEqual(PilotReportBuilder(self.repo).build("pilot").decision,
                         "insufficient_data")

    def test_any_failed_required_qa_blocks_package_acceptance(self):
        with sqlite3.connect(self.path) as c:
            c.execute("INSERT INTO qa_results(id,project_id,revision_id,asset_id,stage,check_code,status,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                      ("qa-failed","project","revision","master","final",
                       "audio_clear","fail","reviewer","2026-10-13T12:01:00+00:00"))
        for window in ("24h", "72h", "7d"):
            self.measure.add_metric(snapshot_id=f"qa-fail-{window}", publish_job_id="job",
                                    window=window, metric_name="views", value=1,
                                    measured=True, definition="views", source="api",
                                    captured_at=CAPTURED[window], created_at=CAPTURED[window])
        self.assertEqual(PilotReportBuilder(self.repo).build("pilot").decision,
                         "insufficient_data")


if __name__ == "__main__":
    unittest.main()
