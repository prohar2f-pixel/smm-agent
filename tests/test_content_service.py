import sqlite3
import tempfile
import unittest
from pathlib import Path

from db.content_repository import ContentRepository
from db.migrate import run_migrations
from domain.content_service import ContentService, canonical_sha256
from domain.errors import ConflictError, ValidationError
from domain.source_service import SourceService


NOW = "2026-10-12T12:00:00+00:00"


class ContentServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "agent.db"
        run_migrations(self.path)
        with sqlite3.connect(self.path) as connection:
            for pilot in ("pilot-1", "pilot-2"):
                connection.execute(
                    """INSERT INTO pilot_passports(
                    id,name,status,timezone,evaluation_mode,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?)""",
                    (pilot, pilot, "draft", "UTC", "internal_efficiency", NOW, NOW),
                )
        repository = ContentRepository(self.path)
        self.sources = SourceService(repository)
        self.service = ContentService(repository, self.sources)
        self.add_source("source-1", "pilot-1")
        self.add_source("source-2", "pilot-1")
        self.add_source("source-other", "pilot-2")

    def tearDown(self):
        self.temp.cleanup()

    def add_source(self, source_id, pilot_id):
        self.sources.capture_source(source_id, pilot_id, "file", source_id, "a" * 64, NOW)
        self.sources.verify_source(source_id, 1, "allowed", "consent", "clear", "actor", NOW)

    def project(self):
        return self.service.create_project("project-1", "pilot-1", "Demo", "owner", ["source-1"], NOW)

    def brief(self):
        self.project()
        return self.service.create_brief(
            "brief-1", "project-1", 1, "founders", "leads", "audit", "proof",
            "Write us", "telegram", "channel", 30, 45, [], "brief-v1",
            ["source-1"], "owner", NOW,
        )

    def test_hash_is_canonical(self):
        self.assertEqual(canonical_sha256({"b": 2, "a": 1}), canonical_sha256({"a": 1, "b": 2}))

    def test_project_requires_usable_same_pilot_source(self):
        with self.assertRaises(ValidationError):
            self.service.create_project("p", "pilot-1", "Demo", "owner", [], NOW)
        with self.assertRaises(ValidationError):
            self.service.create_project("p", "pilot-1", "Demo", "owner", ["source-other"], NOW)

    def test_brief_requires_project_source_and_nonempty_cta(self):
        self.project()
        with self.assertRaises(ValidationError):
            self.service.create_brief("b", "project-1", 1, "a", "g", "o", "x", "", "tg", "c", 30, 45, [], "v", ["source-1"], "u", NOW)
        with self.assertRaises(ValidationError):
            self.service.create_brief("b", "project-1", 1, "a", "g", "o", "x", "cta", "tg", "c", 30, 45, [], "v", ["source-2"], "u", NOW)

    def test_numbers_are_unique(self):
        self.brief()
        with self.assertRaises(ConflictError):
            self.service.create_brief("brief-2", "project-1", 1, "a", "g", "o", "x", "cta", "tg", "c", 30, 45, [], "v", ["source-1"], "u", NOW)

    def test_revision_is_immutable_and_scoped_to_project(self):
        self.brief()
        revision = self.service.create_revision("revision-1", "project-1", "brief-1", 1, None, "Script", "Caption", [], {"script": "v1"}, "owner", NOW)
        self.assertEqual(revision.revision_no, 1)
        self.assertFalse(hasattr(self.service, "update_revision"))
        with self.assertRaises(ConflictError):
            self.service.create_revision("revision-2", "project-1", "brief-1", 1, None, "Other", "Caption", [], {}, "owner", NOW)

    def test_revision_rejects_cross_project_brief(self):
        self.brief()
        self.service.create_project("project-2", "pilot-1", "Other", "owner", ["source-1"], NOW)
        with self.assertRaises(ValidationError):
            self.service.create_revision("r", "project-2", "brief-1", 1, None, "S", "C", [], {}, "owner", NOW)


if __name__ == "__main__":
    unittest.main()
