"""Strict JSON parsing and JSON Schema validation for model output."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError


class StructuredValidationError(ValueError):
    pass


class StructuredOutputValidator:
    _FENCE = re.compile(r"\A```json\s*\n(?P<body>[\s\S]*?)\n```\s*\Z", re.IGNORECASE)

    def parse(self, raw: str, schema: dict[str, Any]) -> dict[str, Any]:
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:
            raise StructuredValidationError(f"invalid schema: {exc.message}") from exc
        candidate = raw.strip()
        match = self._FENCE.fullmatch(candidate)
        if match:
            candidate = match.group("body")
        try:
            value = json.loads(candidate)
        except (json.JSONDecodeError, TypeError) as exc:
            raise StructuredValidationError("model output is not valid JSON") from exc
        if not isinstance(value, dict):
            raise StructuredValidationError("model output root must be an object")
        format_checker = FormatChecker()

        @format_checker.checks("date-time")
        def is_datetime(value: object) -> bool:
            if not isinstance(value, str):
                return False
            try:
                datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return False
            return "T" in value

        validator = Draft202012Validator(schema, format_checker=format_checker)
        errors = sorted(validator.iter_errors(value), key=lambda error: list(error.absolute_path))
        if errors:
            error = errors[0]
            path = ".".join(str(item) for item in error.absolute_path) or "$"
            raise StructuredValidationError(f"schema validation failed at {path}: {error.message}")
        return value
