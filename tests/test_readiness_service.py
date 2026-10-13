import sqlite3
import tempfile
import unittest
from pathlib import Path

from db.content_repository import ContentRepository
from db.migrate import run_migrations
from domain.content_service import ContentService
from domain.errors import ValidationError
from domain.readiness_service import ReadinessService
from domain.source_service import SourceService


NOW = "2026-10-12T12:00:00+00:00"


class ReadinessServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "agent.db"
        run_migrations(self.path)
        with sqlite3.connect(self.path) as connection:
            for pilot in ("pilot-1", "pilot-2"):
                connection.execute(
                    """INSERT INTO pilot_passports(
                    id,name,status,timezone,evaluation_mode,roles_json,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?)""",
                    (pilot, pilot, "draft", "UTC", "internal_efficiency",
                     '{"reviewer":["reviewer","checker"]}', NOW, NOW),
                )
        repository = ContentRepository(self.path)
        sources = SourceService(repository)
        for source_id, pilot_id in (("source-1", "pilot-1"), ("source-other", "pilot-2")):
            sources.capture_source(source_id, pilot_id, "file", source_id, "a" * 64, NOW)
            sources.verify_source(source_id, 1, "allowed", "consent", "clear", "actor", NOW)
        content = ContentService(repository, sources)
        content.create_project("project-1", "pilot-1", "Demo", "owner", ["source-1"], NOW)
        content.create_brief("brief-1", "project-1", 1, "a", "g", "o", "x", "cta", "tg", "c", 30, 45, [], "v", ["source-1"], "owner", NOW, 1)
        self.revision = content.create_revision("revision-1", "project-1", "brief-1", 1, None, "Script", "Caption", [], {}, "owner", NOW, 2)
        self.service = ReadinessService(repository)

    def tearDown(self):
        self.temp.cleanup()

    def approve(self, revision_id="revision-1", digest=None):
        self.service.add_review("review-1", "project-1", revision_id, "script", digest or self.revision.content_sha256, "reviewer", "approved", "reviewer", NOW, NOW, "script-v1")

    def test_supported_claim_needs_evidence(self):
        self.service.add_claim("claim-1", "revision-1", "Fact", {}, "supported", None, NOW, "checker", NOW)
        self.approve()
        self.assertIn("CLAIM_EVIDENCE_MISSING", self.service.check_production_ready("project-1", "revision-1").blockers)

    def test_hypothesis_needs_visible_label(self):
        self.service.add_claim("claim-1", "revision-1", "Maybe", {}, "hypothesis", None, NOW)
        self.approve()
        self.assertIn("HYPOTHESIS_LABEL_MISSING", self.service.check_production_ready("project-1", "revision-1").blockers)

    def test_disputed_claim_and_hash_mismatch_block(self):
        self.service.add_claim("claim-1", "revision-1", "Old", {}, "disputed", None, NOW)
        self.approve(digest="f" * 64)
        result = self.service.check_production_ready("project-1", "revision-1")
        self.assertEqual(result.blockers, ("CLAIM_BLOCKED", "SCRIPT_APPROVAL_MISSING", "SUBJECT_HASH_MISMATCH"))

    def test_parent_approval_does_not_approve_child(self):
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """INSERT INTO content_revisions(id,project_id,brief_id,revision_no,parent_revision_id,
                script_text,caption_text,timeline_json,prompt_versions_json,content_sha256,created_by,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("revision-2", "project-1", "brief-1", 2, "revision-1", "S2", "C2", "[]", "{}", "d" * 64, "owner", NOW),
            )
        self.approve()
        self.assertIn("SCRIPT_APPROVAL_MISSING", self.service.check_production_ready("project-1", "revision-2").blockers)

    def test_cross_pilot_evidence_is_rejected_and_reported(self):
        self.service.add_claim("claim-1", "revision-1", "Fact", {}, "supported", None, NOW, "checker", NOW)
        with self.assertRaises(ValidationError):
            self.service.add_evidence("e-1", "claim-1", "source-other", "text", None, None, None, "note", "checker", NOW, NOW)

    def test_revoked_source_blocks_after_project_creation(self):
        self.approve()
        with sqlite3.connect(self.path) as connection:
            connection.execute("UPDATE sources SET rights_status='revoked' WHERE id='source-1'")
        self.assertIn("SOURCE_NOT_ALLOWED", self.service.check_production_ready("project-1", "revision-1").blockers)

    def test_fully_supported_revision_is_ready(self):
        self.service.add_claim("claim-1", "revision-1", "Fact", {}, "supported", None, NOW, "checker", NOW)
        self.service.add_evidence("e-1", "claim-1", "source-1", "text", None, None, None, "supports", "checker", NOW, NOW)
        self.service.add_claim("claim-2", "revision-1", "Estimate", {}, "hypothesis", "caption:1", NOW)
        self.approve()
        result = self.service.check_production_ready("project-1", "revision-1")
        self.assertTrue(result.ready)
        self.assertEqual(result.blockers, ())

    def test_supported_claim_requires_human_check_and_unexpired_check(self):
        with self.assertRaises(ValidationError):
            self.service.add_claim("unchecked", "revision-1", "Fact", {}, "supported", None, NOW)
        self.service.add_claim(
            "expired", "revision-1", "Fact", {}, "supported", None, NOW,
            "checker", NOW, "2026-10-12T11:00:00+00:00",
        )
        self.service.add_evidence("e-expired", "expired", "source-1", "text", None, None, None, "note", "checker", NOW, NOW)
        self.approve()
        self.assertIn(
            "CLAIM_BLOCKED",
            self.service.check_production_ready("project-1", "revision-1", NOW).blockers,
        )

    def test_pending_evidence_source_and_actorless_approval_are_rejected(self):
        repository = self.service.repository
        sources = SourceService(repository)
        sources.capture_source("pending", "pilot-1", "file", "pending", "b" * 64, NOW)
        sources.capture_source("unlinked", "pilot-1", "file", "unlinked", "c" * 64, NOW)
        sources.verify_source("unlinked", 1, "allowed", "consent", "clear", "actor", NOW)
        self.service.add_claim("claim-1", "revision-1", "Fact", {}, "supported", None, NOW, "checker", NOW)
        with self.assertRaises(ValidationError):
            self.service.add_evidence("e", "claim-1", "pending", "text", None, None, None, "note", "checker", NOW, NOW)
        with self.assertRaises(ValidationError):
            self.service.add_evidence("e", "claim-1", "unlinked", "text", None, None, None, "note", "checker", NOW, NOW)
        with self.assertRaises(ValidationError):
            self.service.add_review("r", "project-1", "revision-1", "script", self.revision.content_sha256, "reviewer", "approved", None, None, NOW, "script-v1")

    def test_latest_invalidation_supersedes_approval(self):
        self.approve()
        self.service.add_review(
            "review-2", "project-1", "revision-1", "script",
            self.revision.content_sha256, "reviewer", "invalidated",
            "reviewer", "2026-10-12T12:01:00+00:00",
            "2026-10-12T12:01:00+00:00", "script-v1",
        )
        result = self.service.check_production_ready("project-1", "revision-1")
        self.assertIn("SCRIPT_APPROVAL_MISSING", result.blockers)


if __name__ == "__main__":
    unittest.main()
