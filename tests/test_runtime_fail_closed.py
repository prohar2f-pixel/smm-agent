import tempfile
import unittest
import json
import sqlite3
from pathlib import Path

from app.runtime import FactoryConfig, FactoryRuntime
from db.content_repository import ContentRepository
from domain.errors import ValidationError
from domain.job_service import JobService
from workers.runner import WorkerRunner


class RuntimeFailClosedTests(unittest.TestCase):
    def test_legacy_publishers_are_explicitly_opt_in(self):
        scheduler_source = Path("scheduler.py").read_text()
        handler_source = Path("bot/handlers.py").read_text()
        self.assertIn('LEGACY_AUTO_PUBLISH_ENABLED", "false"', scheduler_source)
        self.assertIn('LEGACY_DIRECT_PUBLISH_ENABLED", "false"', handler_source)

    def test_missing_owner_is_rejected_and_flags_default_off(self):
        with self.assertRaises(ValueError):
            FactoryConfig.from_env({"DB_PATH": "/tmp/x.db"})
        config = FactoryConfig.from_env({"DB_PATH":"/tmp/x.db", "ADMIN_TELEGRAM_ID":"1"})
        self.assertEqual(config.status(), {"control_plane":False,"worker":False,
                                          "publishing":False,"paid_generation":False})

    def test_startup_applies_migrations_without_external_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "agent.db"
            config = FactoryConfig.from_env({"DB_PATH":str(path), "ADMIN_TELEGRAM_ID":"1"})
            applied = FactoryRuntime(config).startup()
            self.assertEqual(applied, list(range(1, 13)))

    def test_disabled_worker_claims_nothing_but_can_recover_safely(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "agent.db"
            config = FactoryConfig.from_env({"DB_PATH":str(path), "ADMIN_TELEGRAM_ID":"1"})
            FactoryRuntime(config).startup()
            jobs = JobService(ContentRepository(path))
            jobs.enqueue("j","c","screen_demo",None,None,None,{},"2026-10-13T12:00:00+00:00","2026-10-13T12:00:00+00:00")
            runner = WorkerRunner(jobs, {"screen_demo": lambda claim: claim}, enabled=False)
            self.assertIsNone(runner.run_once("w","2026-10-13T12:00:00+00:00","2026-10-13T12:05:00+00:00"))
            self.assertEqual(jobs.get("j").status, "queued")
            self.assertEqual(runner.recover("2026-10-13T12:00:00+00:00"), (0,0))

    def test_enabled_action_runtime_requires_configured_pilot_and_artifact_root(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "agent.db"
            config = FactoryConfig.from_env({"DB_PATH":str(path),
                                             "ADMIN_TELEGRAM_ID":"1",
                                             "FACTORY_WORKER_ENABLED":"true",
                                             "FACTORY_ARTIFACT_ROOT":str(Path(directory)/"artifacts")})
            with self.assertRaises(ValidationError):
                FactoryRuntime(config).startup()

    def test_enabled_worker_requires_registered_handlers(self):
        with tempfile.TemporaryDirectory() as directory:
            config = FactoryConfig(
                db_path=Path(directory) / "agent.db", admin_telegram_id=1,
                worker_enabled=True, artifact_root=Path(directory) / "artifacts",
            )
            with self.assertRaises(ValidationError):
                FactoryRuntime(config).worker_runner({})

    def test_main_composes_guarded_publish_and_worker_services(self):
        source = Path("main.py").read_text()
        self.assertIn("runtime.publish_service()", source)
        self.assertIn("runtime.worker_handlers()", source)
        self.assertIn("worker_runner.run_once", source)
        self.assertIn("if config.control_plane_enabled:", source)
        self.assertIn("dp.include_router(factory_router)", source)
        self.assertIn('os.getenv("TELEGRAM_HTTP_PROXY")', source)
        self.assertIn("AiohttpSession(proxy=proxy)", source)
        self.assertIn('command="factory_enqueue"', source)
        self.assertIn('command="factory_approve"', source)
        self.assertIn('command="factory_publish_enqueue"', source)

    def test_runtime_owns_text_factory_feature_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = FactoryRuntime(FactoryConfig(
                db_path=Path(directory) / "agent.db", admin_telegram_id=1,
                paid_generation_enabled=False,
            ))
            factory = runtime.text_factory(object())
            self.assertFalse(factory.enabled)

    def test_worker_failure_is_requeued_instead_of_leased_until_expiry(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "agent.db"
            config = FactoryConfig.from_env({"DB_PATH":str(path), "ADMIN_TELEGRAM_ID":"1"})
            FactoryRuntime(config).startup()
            jobs = JobService(ContentRepository(path))
            jobs.enqueue("j","c","screen_demo",None,None,None,{},
                         "2026-10-13T12:00:00+00:00","2026-10-13T12:00:00+00:00")

            def broken(_claim):
                raise RuntimeError("render failed")

            runner = WorkerRunner(jobs, {"screen_demo": broken}, enabled=True)
            result = runner.run_once("w","2026-10-13T12:00:00+00:00",
                                     "2026-10-13T12:05:00+00:00")
            self.assertEqual(result.status, "queued")
            self.assertEqual(result.attempt_count, 1)

    def test_control_plane_enqueues_complete_screen_demo_manifest_idempotently(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "agent.db"
            runtime = FactoryRuntime(FactoryConfig(
                db_path=path, admin_telegram_id=1, control_plane_enabled=True,
                pilot_id="pilot", artifact_root=Path(directory) / "artifacts",
            ))
            runtime.startup()
            now = "2026-10-13T12:00:00+00:00"
            with sqlite3.connect(path) as c:
                c.execute("INSERT INTO pilot_passports(id,name,status,timezone,evaluation_mode,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", ("pilot","Pilot","running","UTC","internal_efficiency",now,now))
                c.execute("INSERT INTO content_projects(id,pilot_id,title,status,owner_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", ("project","pilot","Demo","production_ready","owner",now,now))
                c.execute("INSERT INTO content_briefs(id,project_id,brief_no,audience,goal,offer,angle,cta_text,platform,target_ref,duration_min_sec,duration_max_sec,constraints_json,prompt_version,content_sha256,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("brief","project",1,"a","g","o","x","cta","tg","target",30,45,"[]","1","b"*64,"owner",now))
                c.execute("INSERT INTO content_revisions(id,project_id,brief_id,revision_no,script_text,caption_text,timeline_json,prompt_versions_json,content_sha256,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", ("revision","project","brief",1,"s","c","[]","{}","a"*64,"owner",now))
                c.execute("UPDATE content_projects SET current_revision_id='revision' WHERE id='project'")
            payload = json.loads(Path("tests/fixtures/screen_demo_manifest.json").read_text())
            for key in ("job_id", "fence_token", "revision_id", "input_sha256", "schema_version"):
                payload.pop(key, None)
            first = runtime.enqueue_screen_demo(
                command_id="cmd", job_id="job", project_id="project",
                payload=payload, stage="preview", created_at=now,
            )
            second = runtime.enqueue_screen_demo(
                command_id="cmd", job_id="other", project_id="project",
                payload=payload, stage="preview", created_at=now,
            )
            self.assertEqual((first.id, second.id), ("job", "job"))
            stored = runtime.repository.fetchone("SELECT payload_json FROM jobs WHERE id='job'")
            manifest = json.loads(stored["payload_json"])
            self.assertEqual(manifest["render_profile"], "screen-demo-preview-v1")
            self.assertEqual(manifest["asset_role"], "preview")


if __name__ == "__main__":
    unittest.main()
