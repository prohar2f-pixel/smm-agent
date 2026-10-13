import tempfile
import unittest
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
            self.assertEqual(applied, list(range(1, 12)))

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
        self.assertIn("runtime.worker_runner({})", source)
        self.assertIn("worker_runner.run_once", source)
        self.assertIn("if config.control_plane_enabled:", source)
        self.assertIn("dp.include_router(factory_router)", source)
        self.assertIn('os.getenv("TELEGRAM_HTTP_PROXY")', source)
        self.assertIn("AiohttpSession(proxy=proxy)", source)

    def test_runtime_owns_text_factory_feature_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = FactoryRuntime(FactoryConfig(
                db_path=Path(directory) / "agent.db", admin_telegram_id=1,
                paid_generation_enabled=False,
            ))
            factory = runtime.text_factory(object())
            self.assertFalse(factory.enabled)


if __name__ == "__main__":
    unittest.main()
