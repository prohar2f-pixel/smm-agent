import tempfile
import unittest
from pathlib import Path

from db.content_repository import ContentRepository
from db.migrate import run_migrations
from domain.errors import ConflictError, NotFoundError, ValidationError
from llm.prompt_registry import PromptRegistry


NOW = "2026-10-13T12:00:00+00:00"
SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {"title": {"type": "string"}},
    "required": ["title"],
    "additionalProperties": False,
}


class PromptRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "agent.db"
        run_migrations(self.path)
        self.registry = PromptRegistry(ContentRepository(self.path))

    def tearDown(self):
        self.temp.cleanup()

    def test_register_and_get_exact_version_with_canonical_hash(self):
        prompt = self.registry.register(
            "writer", "1.2.3", "System", "Input: {input_json}", SCHEMA, "owner", NOW,
            template_id="prompt-1",
        )
        same_schema_reordered = {
            "required": ["title"], "additionalProperties": False,
            "properties": {"title": {"type": "string"}}, "type": "object",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        }
        other = self.registry.compute_hash("System", "Input: {input_json}", same_schema_reordered)
        self.assertEqual(prompt.content_sha256, other)
        self.assertEqual(self.registry.get("writer", "1.2.3"), prompt)

    def test_duplicate_or_mutated_version_is_rejected(self):
        self.registry.register("writer", "1.0.0", "S", "U", SCHEMA, "owner", NOW, template_id="p1")
        with self.assertRaises(ConflictError):
            self.registry.register("writer", "1.0.0", "Changed", "U", SCHEMA, "owner", NOW, template_id="p2")

    def test_invalid_semver_or_open_schema_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.registry.register("writer", "v1", "S", "U", SCHEMA, "owner", NOW, template_id="p")
        open_schema = {"type": "object", "properties": {}}
        with self.assertRaises(ValidationError):
            self.registry.register("writer", "1.0.0", "S", "U", open_schema, "owner", NOW, template_id="p")

    def test_missing_explicit_version_is_not_found(self):
        with self.assertRaises(NotFoundError):
            self.registry.get("writer", "latest")


if __name__ == "__main__":
    unittest.main()
