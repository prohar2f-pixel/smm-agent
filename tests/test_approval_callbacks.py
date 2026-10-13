import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.callback_service import CallbackService
from db.content_repository import ContentRepository
from db.migrate import run_migrations
from domain.approval_service import ApprovalService
from domain.errors import ConflictError, ValidationError

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
            c.execute("INSERT INTO pilot_passports(id,name,status,timezone,evaluation_mode,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", ("pilot","Pilot","running","UTC","internal_efficiency",NOW,NOW))
            c.execute("INSERT INTO content_projects(id,pilot_id,title,status,owner_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", ("project","pilot","Demo","script_review","owner",NOW,NOW))
            c.execute("INSERT INTO content_briefs(id,project_id,brief_no,audience,goal,offer,angle,cta_text,platform,target_ref,duration_min_sec,duration_max_sec,constraints_json,prompt_version,content_sha256,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("brief","project",1,"a","g","o","x","cta","tg","target",30,45,"[]","1.0.0","b"*64,"owner",NOW))
            c.execute("INSERT INTO content_revisions(id,project_id,brief_id,revision_no,script_text,caption_text,timeline_json,prompt_versions_json,content_sha256,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", ("revision","project","brief",1,"s","c","[]","{}",HASH,"owner",NOW))

    def test_script_decision_is_exact_hash_and_role_scoped(self):
        row = self.approvals.decide(decision_id="d1", project_id="project", revision_id="revision", stage="script", subject_sha256=HASH, status="approved", actor_id="u", actor_role="reviewer", checklist_version="1", created_at=NOW)
        self.assertEqual(row["status"], "approved")
        with self.assertRaises(ValidationError):
            self.approvals.decide(decision_id="d2", project_id="project", revision_id="revision", stage="publish", subject_sha256=HASH, status="approved", actor_id="u", actor_role="reviewer", checklist_version="1", created_at=NOW, target_ref="channel")
        with self.assertRaises(ConflictError):
            self.approvals.decide(decision_id="d3", project_id="project", revision_id="revision", stage="script", subject_sha256="c"*64, status="approved", actor_id="u", actor_role="reviewer", checklist_version="1", created_at=NOW)

    def test_callback_deduplicates_update_and_blocks_replay_scope_and_expiry(self):
        token = self.callbacks.issue(user_id="u", chat_id="c", action="approve", subject_id="revision", row_version=1, subject_sha256=HASH, expires_at=LATER, created_at=NOW)
        first = self.callbacks.consume(producer="telegram", event_id="e1", token=token, user_id="u", chat_id="c", subject_sha256=HASH, row_version=1, now=NOW)
        self.assertEqual(first, self.callbacks.consume(producer="telegram", event_id="e1", token=token, user_id="u", chat_id="c", subject_sha256=HASH, row_version=1, now=NOW))
        with self.assertRaises(ConflictError):
            self.callbacks.consume(producer="telegram", event_id="e2", token=token, user_id="u", chat_id="c", subject_sha256=HASH, row_version=1, now=NOW)
        expired = self.callbacks.issue(user_id="u", chat_id="c", action="approve", subject_id="revision", row_version=1, subject_sha256=HASH, expires_at=NOW, created_at=NOW)
        with self.assertRaises(ConflictError):
            self.callbacks.consume(producer="telegram", event_id="e3", token=expired, user_id="u", chat_id="c", subject_sha256=HASH, row_version=1, now=LATER)


if __name__ == "__main__":
    unittest.main()
