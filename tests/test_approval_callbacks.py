import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.callback_service import CallbackService
from db.content_repository import ContentRepository
from db.migrate import run_migrations
from domain.approval_service import ApprovalService
from domain.errors import ConflictError, ValidationError
from domain.publish_service import PublishService

NOW = "2026-10-13T12:00:00+00:00"
LATER = "2026-10-13T12:05:00+00:00"
HASH = "a" * 64


class ApprovalCallbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "agent.db"
        run_migrations(self.path)
        self.repo = ContentRepository(self.path)
        self._seed()
        self.approvals = ApprovalService(self.repo)
        self.callbacks = CallbackService(self.repo)

    def tearDown(self):
        self.temp.cleanup()

    def _seed(self):
        with sqlite3.connect(self.path) as c:
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("INSERT INTO pilot_passports(id,name,status,timezone,evaluation_mode,roles_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", ("pilot","Pilot","running","UTC","internal_efficiency",'{"reviewer":["u"],"publisher":["publisher"]}',NOW,NOW))
            c.execute("INSERT INTO content_projects(id,pilot_id,title,status,owner_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", ("project","pilot","Demo","script_review","owner",NOW,NOW))
            c.execute("INSERT INTO content_briefs(id,project_id,brief_no,audience,goal,offer,angle,cta_text,platform,target_ref,duration_min_sec,duration_max_sec,constraints_json,prompt_version,content_sha256,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("brief","project",1,"a","g","o","x","cta","tg","target",30,45,"[]","1.0.0","b"*64,"owner",NOW))
            c.execute("INSERT INTO content_revisions(id,project_id,brief_id,revision_no,script_text,caption_text,timeline_json,prompt_versions_json,content_sha256,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", ("revision","project","brief",1,"s","c","[]","{}",HASH,"owner",NOW))
            c.execute("UPDATE content_projects SET current_revision_id='revision' WHERE id='project'")

    def test_script_decision_is_exact_hash_and_role_scoped(self):
        row = self.approvals.decide(decision_id="d1", project_id="project", revision_id="revision", stage="script", subject_sha256=HASH, status="approved", actor_id="u", actor_role="reviewer", checklist_version="1", created_at=NOW)
        self.assertEqual(row["status"], "approved")
        with self.assertRaises(ValidationError):
            self.approvals.decide(decision_id="d2", project_id="project", revision_id="revision", stage="publish", subject_sha256=HASH, status="approved", actor_id="u", actor_role="reviewer", checklist_version="1", created_at=NOW, target_ref="channel")
        with self.assertRaises(ConflictError):
            self.approvals.decide(decision_id="d3", project_id="project", revision_id="revision", stage="script", subject_sha256="c"*64, status="approved", actor_id="u", actor_role="reviewer", checklist_version="1", created_at=NOW)

    def test_caller_cannot_spoof_role_and_stale_revision_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.approvals.decide(decision_id="spoof", project_id="project",
                                  revision_id="revision", stage="script",
                                  subject_sha256=HASH, status="approved",
                                  actor_id="attacker", actor_role="reviewer",
                                  checklist_version="1", created_at=NOW)
        with sqlite3.connect(self.path) as c:
            c.execute("UPDATE content_projects SET current_revision_id=NULL WHERE id='project'")
        with self.assertRaises(ConflictError):
            self.approvals.decide(decision_id="stale", project_id="project",
                                  revision_id="revision", stage="script",
                                  subject_sha256=HASH, status="approved",
                                  actor_id="u", actor_role="reviewer",
                                  checklist_version="1", created_at=NOW)

    def test_publish_approval_recomputes_package_hash_server_side(self):
        package_hash = PublishService.package_hash("caption", "[]")
        with sqlite3.connect(self.path) as c:
            c.execute(
                "INSERT INTO assets(id,project_id,revision_id,role,storage_key,sha256,mime_type,size_bytes,rights_status,privacy_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                ("master", "project", "revision", "master", "master.mp4",
                 "d" * 64, "video/mp4", 1, "allowed", "clear", NOW),
            )
            c.execute(
                "INSERT INTO approval_decisions(id,project_id,revision_id,stage,subject_sha256,status,actor_id,actor_role,checklist_version,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("final", "project", "revision", "final", "d" * 64,
                 "approved", "owner", "owner", "1", NOW),
            )
            c.execute(
                "INSERT INTO publish_packages(id,project_id,revision_id,package_sha256,caption_text,assets_json,created_by,created_at) VALUES(?,?,?,?,?,?,?,?)",
                ("pkg", "project", "revision", package_hash, "caption", "[]", "owner", NOW),
            )
            c.execute("PRAGMA recursive_triggers=OFF")
            c.execute("DROP TRIGGER immutable_publish_packages_update")
            c.execute("UPDATE publish_packages SET caption_text='tampered' WHERE id='pkg'")
        with self.assertRaises(ConflictError):
            self.approvals.decide(
                decision_id="publish", project_id="project", revision_id="revision",
                stage="publish", subject_sha256=package_hash, status="approved",
                actor_id="publisher", actor_role="publisher", checklist_version="1",
                created_at=NOW, expires_at=LATER, platform="telegram",
                target_ref="channel",
            )

    def test_callback_deduplicates_update_and_blocks_replay_scope_and_expiry(self):
        token = self.callbacks.issue(user_id="u", chat_id="c", action="approve", subject_id="revision", row_version=1, subject_sha256=HASH, expires_at=LATER, created_at=NOW)
        first = self.callbacks.consume(producer="telegram", event_id="e1", token=token, user_id="u", chat_id="c", subject_sha256=HASH, row_version=1, now=NOW)
        self.assertEqual(first, self.callbacks.consume(producer="telegram", event_id="e1", token=token, user_id="u", chat_id="c", subject_sha256=HASH, row_version=1, now=NOW))
        with self.assertRaises(ConflictError):
            self.callbacks.consume(producer="telegram", event_id="e1", token=token,
                                   user_id="attacker", chat_id="c",
                                   subject_sha256=HASH, row_version=1, now=NOW)
        with self.assertRaises(ConflictError):
            self.callbacks.consume(producer="telegram", event_id="e2", token=token, user_id="u", chat_id="c", subject_sha256=HASH, row_version=1, now=NOW)
        expired = self.callbacks.issue(user_id="u", chat_id="c", action="approve", subject_id="revision", row_version=1, subject_sha256=HASH, expires_at=NOW, created_at=NOW)
        with self.assertRaises(ConflictError):
            self.callbacks.consume(producer="telegram", event_id="e3", token=expired, user_id="u", chat_id="c", subject_sha256=HASH, row_version=1, now=LATER)

    def test_callback_decision_audit_and_outbox_commit_atomically(self):
        token = self.callbacks.issue(user_id="u", chat_id="c", action="approve",
                                     subject_id="revision", row_version=1,
                                     subject_sha256=HASH, expires_at=LATER,
                                     created_at=NOW)
        invalid = dict(decision_id="atomic", project_id="project",
                       revision_id="revision", stage="script",
                       subject_sha256=HASH, status="approved",
                       actor_id="attacker", actor_role="reviewer",
                       checklist_version="1", created_at=NOW)
        with self.assertRaises(ValidationError):
            self.callbacks.consume_and_decide(
                approval_service=self.approvals, producer="telegram", event_id="atomic-event",
                token=token, user_id="u", chat_id="c", subject_sha256=HASH,
                row_version=1, now=NOW, decision=invalid, correlation_id="corr",
            )
        with sqlite3.connect(self.path) as c:
            self.assertIsNone(c.execute(
                "SELECT consumed_at FROM callback_actions WHERE token=?", (token,),
            ).fetchone()[0])
            self.assertEqual(c.execute(
                "SELECT COUNT(*) FROM inbox_events WHERE event_id='atomic-event'",
            ).fetchone()[0], 0)
        valid = dict(invalid, actor_id="u")
        result = self.callbacks.consume_and_decide(
            approval_service=self.approvals, producer="telegram", event_id="atomic-event",
            token=token, user_id="u", chat_id="c", subject_sha256=HASH,
            row_version=1, now=NOW, decision=valid, correlation_id="corr",
        )
        self.assertEqual(result, "approval:atomic")
        with sqlite3.connect(self.path) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM approval_decisions WHERE id='atomic'").fetchone()[0], 1)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM audit_events WHERE correlation_id='corr'").fetchone()[0], 1)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM outbox_events WHERE aggregate_id='atomic'").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
