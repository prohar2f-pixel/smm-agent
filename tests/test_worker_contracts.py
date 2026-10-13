import hashlib
import tempfile
import unittest
from pathlib import Path

from db.content_repository import ContentRepository
from db.migrate import run_migrations
from domain.errors import ConflictError, ValidationError
from domain.job_service import JobService
from workers.contracts import WorkerReport
from workers.report_service import WorkerReportService

NOW = "2026-10-13T12:00:00+00:00"
LATER = "2026-10-13T12:05:00+00:00"
HASH = "a" * 64


class WorkerContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "agent.db"
        run_migrations(self.path)
        self.repo = ContentRepository(self.path)
        jobs = JobService(self.repo)
        jobs.enqueue("job", "cmd", "screen_demo", None, None, None,
                     {"input_sha256": HASH}, NOW, NOW)
        self.claim = jobs.claim_next("worker", NOW, LATER)
        self.service = WorkerReportService(self.repo)

    def tearDown(self):
        self.temp.cleanup()

    def report(self, **overrides):
        values = dict(report_id="report", job_id="job", worker_id="worker",
                      fence_token=self.claim.fence_token, schema_version="1.0.0",
                      status="succeeded", input_sha256=HASH,
                      output_path="renders/preview.mp4", output_sha256="b" * 64,
                      duration_ms=1200, render_profile="preview-v1",
                      machine_qa={"ok": True}, error_code=None, created_at=NOW)
        values.update(overrides)
        return WorkerReport(**values)

    def test_valid_report_is_persisted_and_idempotent(self):
        first = self.service.accept(self.report(), NOW)
        second = self.service.accept(self.report(), NOW)
        self.assertEqual((first.id, second.id), ("report", "report"))
        self.assertEqual(self.service.job_service.get("job").status, "succeeded")

    def test_rejects_stale_fence_owner_expiry_or_wrong_input(self):
        for changes in ({"fence_token": 2}, {"worker_id": "other"},
                        {"input_sha256": "c" * 64}):
            with self.subTest(changes=changes), self.assertRaises(ConflictError):
                self.service.accept(self.report(**changes), NOW)
        with self.assertRaises(ConflictError):
            self.service.accept(self.report(), "2026-10-13T12:06:00+00:00")

    def test_contract_rejects_unsafe_path_and_missing_success_hash(self):
        for changes in ({"output_path": "../secret"}, {"output_sha256": None}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self.service.accept(self.report(**changes), NOW)

    def test_same_fence_with_mutated_report_conflicts(self):
        self.service.accept(self.report(), NOW)
        with self.assertRaises(ConflictError):
            self.service.accept(self.report(
                output_sha256=hashlib.sha256(b"x").hexdigest()), NOW)


if __name__ == "__main__":
    unittest.main()
