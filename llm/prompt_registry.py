"""Immutable, explicitly versioned prompt-template registry."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from db.content_repository import ContentRepository
from domain.errors import ConflictError, NotFoundError, ValidationError


SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


@dataclass(frozen=True)
class PromptTemplate:
    id: str
    name: str
    semantic_version: str
    system_template: str
    user_template: str
    output_schema: dict[str, Any]
    content_sha256: str
    created_by: str
    created_at: str


class PromptRegistry:
    def __init__(self, repository: ContentRepository):
        self.repository = repository

    @staticmethod
    def compute_hash(system_template: str, user_template: str, output_schema: dict[str, Any]) -> str:
        payload = {
            "system_template": system_template,
            "user_template": user_template,
            "output_schema": output_schema,
        }
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _validate(name: str, version: str, schema: dict[str, Any]) -> None:
        if not name.strip() or not SEMVER.fullmatch(version):
            raise ValidationError("prompt name and exact semantic version are required")
        if schema.get("type") != "object" or schema.get("additionalProperties") is not False:
            raise ValidationError("prompt output schema must be a closed root object")
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:
            raise ValidationError(f"invalid prompt output schema: {exc.message}") from exc

    def register(
        self,
        name: str,
        semantic_version: str,
        system_template: str,
        user_template: str,
        output_schema: dict[str, Any],
        created_by: str,
        created_at: str,
        *,
        template_id: str,
    ) -> PromptTemplate:
        self._validate(name, semantic_version, output_schema)
        schema_json = json.dumps(output_schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        content_hash = self.compute_hash(system_template, user_template, output_schema)
        try:
            with self.repository.transaction() as connection:
                connection.execute(
                    """INSERT INTO prompt_templates
                       (id,name,semantic_version,system_template,user_template,output_schema_json,
                        content_sha256,created_by,created_at)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (template_id, name, semantic_version, system_template, user_template, schema_json,
                     content_hash, created_by, created_at),
                )
        except sqlite3.IntegrityError as exc:
            raise ConflictError(f"prompt version already exists: {name}@{semantic_version}") from exc
        return self.get(name, semantic_version)

    def get(self, name: str, semantic_version: str) -> PromptTemplate:
        row = self.repository.fetchone(
            "SELECT * FROM prompt_templates WHERE name=? AND semantic_version=?",
            (name, semantic_version),
        )
        if row is None:
            raise NotFoundError(f"prompt not found: {name}@{semantic_version}")
        return PromptTemplate(
            id=row["id"], name=row["name"], semantic_version=row["semantic_version"],
            system_template=row["system_template"], user_template=row["user_template"],
            output_schema=json.loads(row["output_schema_json"]), content_sha256=row["content_sha256"],
            created_by=row["created_by"], created_at=row["created_at"],
        )
