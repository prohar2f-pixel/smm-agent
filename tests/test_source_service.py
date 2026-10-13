import sqlite3
import tempfile
import unittest
from pathlib import Path

from db.content_repository import ContentRepository
from db.migrate import run_migrations
from domain.errors import ConflictError, NotFoundError, ValidationError
from domain.source_service import SourceService


NOW = "2026-10-12T12:00:00+00:00"
HASH = "a" * 64


class SourceServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "agent.db"
        run_migrations(self.path)
        with sqlite3.connect(self.path) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            for pilot in ("pilot-1", "pilot-2"):
                connection.execute(
                    """INSERT INTO pilot_passports(
                       id,name,status,timezone,evaluation_mode,created_at,updated_at
                       ) VALUES(?,?,?,?,?,?,?)""",
                    (pilot, pilot, "draft", "UTC", "internal_efficiency", NOW, NOW),
                )
        self.service = SourceService(ContentRepository(self.path))

    def tearDown(self):
        self.temp.cleanup()

    def capture(self, source_id="source-1", pilot_id="pilot-1"):
        return self.service.capture_source(
            source_id=source_id, pilot_id=pilot_id, kind="file",
            original_ref="input.pdf", content_sha256=HASH, captured_at=NOW,
        )

    def test_capture_defaults_to_pending(self):
        source = self.capture()
        self.assertEqual((source.rights_status, source.privacy_status, source.row_version),
                         ("pending", "pending", 1))

    def test_capture_validates_hash_and_parent(self):
        with self.assertRaises(ValidationError):
            self.service.capture_source("x", "pilot-1", "file", "x", "bad", NOW)
        with self.assertRaises(NotFoundError):
            self.service.capture_source("x", "missing", "file", "x", HASH, NOW)

    def test_allowed_requires_evidence(self):
        self.capture()
        with self.assertRaises(ValidationError):
            self.service.verify_source("source-1", 1, "allowed", None, "clear", "actor", NOW)
        with self.assertRaises(ValidationError):
            self.service.verify_source("source-1", 1, "allowed", "   ", "clear", "actor", NOW)
        with self.assertRaises(ValidationError):
            self.service.verify_source("source-1", 1, "denied", None, "clear", "", NOW)

    def test_compare_and_swap_rejects_stale_version(self):
        self.capture()
        current = self.service.verify_source(
            "source-1", 1, "allowed", "consent:42", "clear", "actor", NOW
        )
        self.assertEqual(current.row_version, 2)
        with self.assertRaises(ConflictError):
            self.service.verify_source(
                "source-1", 1, "denied", None, "clear", "actor", NOW
            )

    def test_verify_missing_source_is_not_found(self):
        with self.assertRaises(NotFoundError):
            self.service.verify_source("missing", 1, "denied", None, "clear", "actor", NOW)

    def test_assert_usable_enforces_rights_privacy_and_pilot(self):
        self.capture("pending")
        with self.assertRaises(ValidationError):
            self.service.assert_usable(["pending"], "pilot-1")

        self.capture("private")
        self.service.verify_source("private", 1, "allowed", "consent", "redaction_required", "actor", NOW)
        with self.assertRaises(ValidationError):
            self.service.assert_usable(["private"], "pilot-1")

        self.capture("other", "pilot-2")
        self.service.verify_source("other", 1, "allowed", "consent", "clear", "actor", NOW)
        with self.assertRaises(ValidationError):
            self.service.assert_usable(["other"], "pilot-1")

    def test_assert_usable_returns_allowed_clear_sources(self):
        self.capture()
        self.service.verify_source("source-1", 1, "allowed", "consent", "clear", "actor", NOW)
        records = self.service.assert_usable(["source-1"], "pilot-1")
        self.assertEqual([item.id for item in records], ["source-1"])


if __name__ == "__main__":
    unittest.main()
