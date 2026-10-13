import hashlib
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
LATER = "2026-10-13T13:00:00+00:00"
HASH = PublishService.package_hash("caption", '["master"]')
MASTER_BYTES = b"master"
MASTER_HASH = hashlib.sha256(MASTER_BYTES).hexdigest()


class FakeAdapter:
    platform = "telegram"
    provider_key = "fake"
    def __init__(self, outcome="ok", platform="telegram"):
        self.outcome = outcome
        self.platform = platform
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
        self.artifacts = Path(self.temp.name) / "artifacts"
        self.artifacts.mkdir()
        (self.artifacts / "master.mp4").write_bytes(MASTER_BYTES)
        self.service = PublishService(
            self.repo, publishing_enabled=True, artifact_root=self.artifacts,
        )
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
            c.execute("UPDATE content_projects SET current_revision_id='revision' WHERE id='project'")
            c.execute("INSERT INTO assets(id,project_id,revision_id,role,storage_key,sha256,mime_type,size_bytes,rights_status,privacy_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", ("master","project","revision","master","master.mp4",MASTER_HASH,"video/mp4",1,"allowed","clear",NOW))
            c.execute("INSERT INTO qa_results(id,project_id,revision_id,asset_id,stage,check_code,status,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?)", ("qa","project","revision","master","technical","media","pass","worker",NOW))
            c.execute("INSERT INTO approval_decisions(id,project_id,revision_id,stage,subject_sha256,status,actor_id,actor_role,checklist_version,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)", ("final","project","revision","final",MASTER_HASH,"approved","owner","owner","1",NOW))
            c.execute("INSERT INTO publish_packages(id,project_id,revision_id,package_sha256,caption_text,assets_json,created_by,created_at) VALUES(?,?,?,?,?,?,?,?)", ("pkg","project","revision",HASH,"caption",'["master"]',"owner",NOW))

    def approve(self, target, platform="telegram"):
        with sqlite3.connect(self.path) as c:
            c.execute("INSERT INTO approval_decisions(id,project_id,revision_id,stage,subject_sha256,platform,target_ref,status,actor_id,actor_role,checklist_version,expires_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (f"approval-{platform}-{target}","project","revision","publish",HASH,platform,target,"approved","owner","publisher","1",LATER,NOW))

    def test_missing_approval_is_blocked_and_platform_jobs_are_independent(self):
        with self.assertRaises(ValidationError):
            self.service.enqueue(job_id="j0", package_id="pkg", platform="telegram", target_ref="tg", created_at=NOW)
        self.approve("target")
        self.approve("target", "vk")
        tg = self.service.enqueue(job_id="tg", package_id="pkg", platform="telegram", target_ref="target", created_at=NOW)
        vk = self.service.enqueue(job_id="vk", package_id="pkg", platform="vk", target_ref="target", created_at=NOW)
        self.assertEqual((tg["status"], vk["status"]), ("queued", "queued"))
        self.assertEqual(self.service.enqueue(job_id="other", package_id="pkg", platform="telegram", target_ref="target", created_at=NOW)["id"], "tg")

    def test_partial_success_does_not_publish_other_platform(self):
        self.approve("target")
        self.approve("target", "vk")
        self.service.enqueue(job_id="tg", package_id="pkg", platform="telegram", target_ref="target", created_at=NOW)
        self.service.enqueue(job_id="vk", package_id="pkg", platform="vk", target_ref="target", created_at=NOW)
        self.assertEqual(self.service.execute("tg", FakeAdapter(), NOW)["status"], "published")
        self.assertEqual(self.service.execute("vk", FakeAdapter("fail", "vk"), NOW)["status"], "unknown_outcome")

    def test_timeout_is_unknown_and_requires_reconciliation_not_retry(self):
        self.approve("target")
        self.service.enqueue(job_id="tg", package_id="pkg", platform="telegram", target_ref="target", created_at=NOW)
        self.assertEqual(self.service.execute("tg", FakeAdapter("timeout"), NOW)["status"], "unknown_outcome")
        with self.assertRaises(ConflictError):
            self.service.execute("tg", FakeAdapter(), NOW)
        self.assertEqual(self.service.reconcile("tg", FakeAdapter("reconcile"), NOW)["external_id"], "external-r")

    def test_manual_mode_requires_verified_evidence(self):
        self.approve("manual-target", "manual")
        self.service.enqueue(job_id="manual", package_id="pkg", platform="manual", target_ref="manual-target", created_at=NOW)
        with self.assertRaises(ValidationError):
            self.service.confirm_manual("manual", "", "", NOW)
        self.assertEqual(self.service.confirm_manual("manual", "post-1", "screenshot://1", NOW)["status"], "published")

    def test_later_invalidation_blocks_enqueue_and_already_queued_send(self):
        self.approve("target")
        self.service.enqueue(job_id="tg", package_id="pkg", platform="telegram", target_ref="target", created_at=NOW)
        with sqlite3.connect(self.path) as c:
            c.execute("INSERT INTO approval_decisions(id,project_id,revision_id,stage,subject_sha256,platform,target_ref,status,actor_id,actor_role,checklist_version,expires_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", ("invalid","project","revision","publish",HASH,"telegram","target","invalidated","owner","publisher","1",LATER,"2026-10-13T12:01:00+00:00"))
        with self.assertRaises(ValidationError):
            self.service.execute("tg", FakeAdapter(), NOW)
        with self.assertRaises(ValidationError):
            self.service.enqueue(job_id="vk", package_id="pkg", platform="vk", target_ref="target", created_at=NOW)

    def test_approved_master_must_be_inside_exact_package(self):
        other_hash = PublishService.package_hash("caption", "[]")
        with sqlite3.connect(self.path) as c:
            c.execute(
                "INSERT INTO publish_packages(id,project_id,revision_id,package_sha256,caption_text,assets_json,created_by,created_at) VALUES(?,?,?,?,?,?,?,?)",
                ("pkg-empty", "project", "revision", other_hash, "caption", "[]", "owner", NOW),
            )
            c.execute(
                "INSERT INTO approval_decisions(id,project_id,revision_id,stage,subject_sha256,platform,target_ref,status,actor_id,actor_role,checklist_version,expires_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("approval-empty", "project", "revision", "publish", other_hash,
                 "telegram", "target", "approved", "owner", "publisher", "1", LATER, NOW),
            )
        self.service.enqueue(
            job_id="empty", package_id="pkg-empty", platform="telegram",
            target_ref="target", created_at=NOW,
        )
        with self.assertRaises(ValidationError):
            self.service.execute("empty", FakeAdapter(), NOW)

    def test_send_intent_is_unknown_before_adapter_and_manual_cannot_confirm_api_job(self):
        self.approve("target")
        self.service.enqueue(job_id="tg", package_id="pkg", platform="telegram", target_ref="target", created_at=NOW)

        service = self.service
        class InspectingAdapter(FakeAdapter):
            def send(self, request):
                self.assert_status = service.get("tg")["status"]
                raise TimeoutError

        adapter = InspectingAdapter("timeout")
        self.service.execute("tg", adapter, NOW)
        self.assertEqual(adapter.assert_status, "unknown_outcome")
        with self.assertRaises(ConflictError):
            self.service.confirm_manual("tg", "post", "evidence", NOW)

    def test_external_boundary_is_disabled_by_default(self):
        self.approve("target")
        guarded = PublishService(self.repo, artifact_root=self.artifacts)
        guarded.enqueue(job_id="tg", package_id="pkg", platform="telegram",
                        target_ref="target", created_at=NOW)
        with self.assertRaises(ValidationError):
            guarded.execute("tg", FakeAdapter(), NOW)

    def test_expiry_is_compared_as_an_instant_and_adapter_matches_platform(self):
        with sqlite3.connect(self.path) as c:
            c.execute(
                "INSERT INTO approval_decisions(id,project_id,revision_id,stage,subject_sha256,platform,target_ref,status,actor_id,actor_role,checklist_version,expires_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("offset-expired", "project", "revision", "publish", HASH,
                 "telegram", "expired", "approved", "owner", "publisher", "1",
                 "2026-10-13T13:00:00+03:00", "2026-10-13T09:00:00+00:00"),
            )
        with self.assertRaises(ValidationError):
            self.service.enqueue(
                job_id="expired", package_id="pkg", platform="telegram",
                target_ref="expired", created_at=NOW,
            )
        self.approve("target", "vk")
        self.service.enqueue(
            job_id="vk-mismatch", package_id="pkg", platform="vk",
            target_ref="target", created_at=NOW,
        )
        with self.assertRaises(ValidationError):
            self.service.execute("vk-mismatch", FakeAdapter(platform="telegram"), NOW)

    def test_asset_bytes_are_rechecked_at_send_boundary(self):
        self.approve("target")
        self.service.enqueue(
            job_id="tampered", package_id="pkg", platform="telegram",
            target_ref="target", created_at=NOW,
        )
        (self.artifacts / "master.mp4").write_bytes(b"tampered")
        with self.assertRaises(ConflictError):
            self.service.execute("tampered", FakeAdapter(), NOW)


if __name__ == "__main__":
    unittest.main()
