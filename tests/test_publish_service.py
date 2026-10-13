import sqlite3
import tempfile
import unittest
from pathlib import Path

from db.content_repository import ContentRepository
from db.migrate import run_migrations
from domain.errors import ConflictError, ValidationError
from domain.publish_service import PublishService
from publishing.base import PublishResult

NOW = "2026-10-13T12:00:00+00:00"
HASH = "a" * 64


class FakeAdapter:
    def __init__(self, outcome="ok"):
        self.outcome = outcome
        self.requests = []

    def send(self, request):
        self.requests.append(request)
        if self.outcome == "timeout":
            raise TimeoutError
        if self.outcome == "fail":
            raise RuntimeError("provider rejected")
        return PublishResult("external-1", "evidence://1")

    def reconcile(self, _key):
        return PublishResult("external-r", "evidence://r") if self.outcome == "reconcile" else None


class PublishServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "agent.db"
        run_migrations(self.path)
        self.repo = ContentRepository(self.path)
        self.service = PublishService(self.repo)
        self._seed()

    def tearDown(self):
        self.temp.cleanup()

    def _seed(self):
        with sqlite3.connect(self.path) as c:
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("INSERT INTO pilot_passports(id,name,status,timezone,evaluation_mode,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", ("pilot","Pilot","running","UTC","internal_efficiency",NOW,NOW))
            c.execute("INSERT INTO content_projects(id,pilot_id,title,status,owner_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", ("project","pilot","Demo","publish_ready","owner",NOW,NOW))
            c.execute("INSERT INTO content_briefs(id,project_id,brief_no,audience,goal,offer,angle,cta_text,platform,target_ref,duration_min_sec,duration_max_sec,constraints_json,prompt_version,content_sha256,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("brief","project",1,"a","g","o","x","cta","tg","target",30,45,"[]","1.0.0","b"*64,"owner",NOW))
            c.execute("INSERT INTO content_revisions(id,project_id,brief_id,revision_no,script_text,caption_text,timeline_json,prompt_versions_json,content_sha256,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", ("revision","project","brief",1,"s","c","[]","{}","c"*64,"owner",NOW))
            c.execute("INSERT INTO publish_packages(id,project_id,revision_id,package_sha256,caption_text,assets_json,created_by,created_at) VALUES(?,?,?,?,?,?,?,?)", ("pkg","project","revision",HASH,"caption","[]","owner",NOW))

    def approve(self, target):
        with sqlite3.connect(self.path) as c:
            c.execute("INSERT INTO approval_decisions(id,project_id,revision_id,stage,subject_sha256,target_ref,status,actor_id,actor_role,checklist_version,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (f"approval-{target}","project","revision","publish",HASH,target,"approved","owner","publisher","1",NOW))

    def test_missing_approval_is_blocked_and_platform_jobs_are_independent(self):
        with self.assertRaises(ValidationError):
            self.service.enqueue(job_id="j0", package_id="pkg", platform="telegram", target_ref="tg", created_at=NOW)
        self.approve("target")
        tg = self.service.enqueue(job_id="tg", package_id="pkg", platform="telegram", target_ref="target", created_at=NOW)
        vk = self.service.enqueue(job_id="vk", package_id="pkg", platform="vk", target_ref="target", created_at=NOW)
        self.assertEqual((tg["status"], vk["status"]), ("queued", "queued"))
        self.assertEqual(self.service.enqueue(job_id="other", package_id="pkg", platform="telegram", target_ref="target", created_at=NOW)["id"], "tg")

    def test_partial_success_does_not_publish_other_platform(self):
        self.approve("target")
        self.service.enqueue(job_id="tg", package_id="pkg", platform="telegram", target_ref="target", created_at=NOW)
        self.service.enqueue(job_id="vk", package_id="pkg", platform="vk", target_ref="target", created_at=NOW)
        self.assertEqual(self.service.execute("tg", FakeAdapter(), NOW)["status"], "published")
        self.assertEqual(self.service.execute("vk", FakeAdapter("fail"), NOW)["status"], "failed")

    def test_timeout_is_unknown_and_requires_reconciliation_not_retry(self):
        self.approve("target")
        self.service.enqueue(job_id="tg", package_id="pkg", platform="telegram", target_ref="target", created_at=NOW)
        self.assertEqual(self.service.execute("tg", FakeAdapter("timeout"), NOW)["status"], "unknown_outcome")
        with self.assertRaises(ConflictError):
            self.service.execute("tg", FakeAdapter(), NOW)
        self.assertEqual(self.service.reconcile("tg", FakeAdapter("reconcile"), NOW)["external_id"], "external-r")

    def test_manual_mode_requires_verified_evidence(self):
        self.approve("manual-target")
        self.service.enqueue(job_id="manual", package_id="pkg", platform="manual", target_ref="manual-target", created_at=NOW)
        with self.assertRaises(ValidationError):
            self.service.confirm_manual("manual", "", "", NOW)
        self.assertEqual(self.service.confirm_manual("manual", "post-1", "screenshot://1", NOW)["status"], "published")


if __name__ == "__main__":
    unittest.main()
