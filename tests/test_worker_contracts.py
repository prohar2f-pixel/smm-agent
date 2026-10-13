import hashlib
import tempfile
import unittest
from pathlib import Path

from db.content_repository import ContentRepository
from db.migrate import run_migrations
from domain.errors import ConflictError, ValidationError
from domain.job_service import JobService
from workers.contracts import WorkerReport
from workers.report_service import ArtifactVerifier, WorkerReportService

NOW = "2026-10-13T12:00:00+00:00"
LATER = "2026-10-13T12:05:00+00:00"
HASH = "a" * 64


class WorkerContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "agent.db"
        run_migrations(self.path)
        self.repo = ContentRepository(self.path)
        self.root = Path(self.temp.name) / "artifacts"
        (self.root / "renders").mkdir(parents=True)
        self.data = b"verified rendered video"
        (self.root / "renders" / "preview.mp4").write_bytes(self.data)
        self.output_hash = hashlib.sha256(self.data).hexdigest()
        self._seed_project()
        jobs = JobService(self.repo)
        jobs.enqueue("job", "cmd", "screen_demo", "pilot", "project", "revision",
                     {"input_sha256": HASH, "output_path": "renders/preview.mp4",
                      "render_profile": "preview-v1", "asset_role": "preview",
                      "mime_type": "video/mp4", "duration_ms": 1200,
                      "width": 1080, "height": 1920}, NOW, NOW)
        self.claim = jobs.claim_next("worker", NOW, LATER)
        self.verifier = ArtifactVerifier(
            self.root, lambda _path: {"mime_type": "video/mp4", "duration_ms": 1200,
                                      "width": 1080, "height": 1920},
        )
        self.service = WorkerReportService(self.repo, self.verifier)

    def tearDown(self):
        self.temp.cleanup()

    def report(self, **overrides):
        values = dict(report_id="report", job_id="job", worker_id="worker",
                      fence_token=self.claim.fence_token, schema_version="1.0.0",
                      status="succeeded", input_sha256=HASH,
                      output_path="renders/preview.mp4", output_sha256=self.output_hash,
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

    def test_nonexistent_symlink_and_metadata_mismatch_are_rejected(self):
        with self.assertRaises(ConflictError):
            self.verifier.verify(
                self.report(output_path="renders/missing.mp4"),
                {"output_path":"renders/missing.mp4", "render_profile":"preview-v1"},
            )
        link = self.root / "renders" / "linked.mp4"
        link.symlink_to(self.root / "renders" / "preview.mp4")
        with self.assertRaises(ConflictError):
            self.verifier.verify(
                self.report(output_path="renders/linked.mp4"),
                {"output_path":"renders/linked.mp4", "render_profile":"preview-v1"},
            )
        verifier = ArtifactVerifier(
            self.root, lambda _path: {"mime_type": "video/mp4", "duration_ms": 999,
                                      "width": 1080, "height": 1920},
        )
        with self.assertRaises(ConflictError):
            WorkerReportService(self.repo, verifier).accept(self.report(), NOW)

    def _seed_project(self):
        import sqlite3
        with sqlite3.connect(self.path) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute(
                "INSERT INTO pilot_passports(id,name,status,timezone,evaluation_mode,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                ("pilot", "Pilot", "running", "UTC", "internal_efficiency", NOW, NOW),
            )
            connection.execute(
                "INSERT INTO content_projects(id,pilot_id,title,status,owner_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                ("project", "pilot", "Demo", "production_ready", "owner", NOW, NOW),
            )
            connection.execute(
                """INSERT INTO content_briefs(id,project_id,brief_no,audience,goal,offer,angle,
                cta_text,platform,target_ref,duration_min_sec,duration_max_sec,constraints_json,
                prompt_version,content_sha256,created_by,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("brief", "project", 1, "a", "g", "o", "x", "cta", "tg", "target",
                 30, 45, "[]", "1", "b" * 64, "owner", NOW),
            )
            connection.execute(
                """INSERT INTO content_revisions(id,project_id,brief_id,revision_no,script_text,
                caption_text,timeline_json,prompt_versions_json,content_sha256,created_by,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                ("revision", "project", "brief", 1, "s", "c", "[]", "{}", HASH,
                 "owner", NOW),
            )


if __name__ == "__main__":
    unittest.main()
