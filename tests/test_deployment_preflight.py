import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.runtime import FactoryRuntime, FactoryConfig
from db.content_repository import ContentRepository
from deployment.preflight import backup_restore_drill, validate_environment, validate_pilot

NOW = "2026-10-13T12:00:00+00:00"


class DeploymentPreflightTests(unittest.TestCase):
    def safe_env(self):
        return {"ADMIN_TELEGRAM_ID":"1", "DB_PATH":"/data/agent.db",
                "TELEGRAM_BOT_TOKEN":"123:real-token", "OPENROUTER_API_KEY":"sk-real",
                "FACTORY_CONTROL_PLANE_ENABLED":"true",
                "FACTORY_WORKER_ENABLED":"true",
                "FACTORY_PUBLISHING_ENABLED":"false",
                "FACTORY_PAID_GENERATION_ENABLED":"false",
                "FACTORY_PILOT_ID":"pilot", "FACTORY_ARTIFACT_ROOT":"/data/artifacts",
                "FACTORY_BACKUP_DIR":"/data/backups"}

    def test_missing_placeholder_and_unsafe_flag_combinations_fail(self):
        self.assertFalse(validate_environment({}).ok)
        env = self.safe_env(); env["TELEGRAM_BOT_TOKEN"] = "change-me"
        self.assertFalse(validate_environment(env).ok)
        env = self.safe_env(); env["FACTORY_CONTROL_PLANE_ENABLED"] = "false"; env["FACTORY_PUBLISHING_ENABLED"] = "true"
        self.assertFalse(validate_environment(env).ok)
        env = self.safe_env(); env["LEGACY_AUTO_PUBLISH_ENABLED"] = "true"
        self.assertFalse(validate_environment(env).ok)

    def test_health_only_needs_no_provider_secrets_and_all_actions_off(self):
        env = {"ADMIN_TELEGRAM_ID":"1", "DB_PATH":"/tmp/health.db", "FACTORY_HEALTH_ONLY":"true"}
        result = validate_environment(env)
        self.assertTrue(result.ok)
        self.assertEqual(result.mode, "health-only")
        env["FACTORY_WORKER_ENABLED"] = "true"
        self.assertFalse(validate_environment(env).ok)

    def test_incomplete_pilot_fails_and_backup_restore_is_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "agent.db"
            FactoryRuntime(FactoryConfig(path, 1)).startup()
            with sqlite3.connect(path) as c:
                c.execute("INSERT INTO pilot_passports(id,name,status,timezone,evaluation_mode,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", ("p","P","draft","UTC","internal_efficiency",NOW,NOW))
            errors = validate_pilot(ContentRepository(path), "p")
            self.assertIn("pilot is not active", errors)
            drill = backup_restore_drill(path, Path(directory))
            self.assertEqual(drill["integrity"], "ok")
            self.assertEqual(drill["versions"], list(range(1,11)))


if __name__ == "__main__":
    unittest.main()
