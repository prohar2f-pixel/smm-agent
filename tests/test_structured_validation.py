import unittest

from llm.validators import StructuredOutputValidator, StructuredValidationError


SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "created_at": {"type": "string", "format": "date-time"},
    },
    "required": ["title", "created_at"],
    "additionalProperties": False,
}


class StructuredValidationTests(unittest.TestCase):
    def setUp(self):
        self.validator = StructuredOutputValidator()

    def test_valid_plain_and_single_json_fence_are_accepted(self):
        expected = {"title": "Demo", "created_at": "2026-10-13T12:00:00Z"}
        self.assertEqual(self.validator.parse('{"title":"Demo","created_at":"2026-10-13T12:00:00Z"}', SCHEMA), expected)
        self.assertEqual(self.validator.parse('```json\n{"title":"Demo","created_at":"2026-10-13T12:00:00Z"}\n```', SCHEMA), expected)

    def test_malformed_array_missing_extra_and_bad_format_are_rejected(self):
        invalid = (
            "not json",
            "[]",
            '{"title":"Demo"}',
            '{"title":"Demo","created_at":"2026-10-13T12:00:00Z","extra":1}',
            '{"title":"Demo","created_at":"yesterday"}',
        )
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(StructuredValidationError):
                self.validator.parse(raw, SCHEMA)

    def test_invalid_schema_is_rejected(self):
        with self.assertRaises(StructuredValidationError):
            self.validator.parse("{}", {"type": "unknown"})


if __name__ == "__main__":
    unittest.main()
