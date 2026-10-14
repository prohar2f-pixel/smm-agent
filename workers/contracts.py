"""Versioned, strict messages returned by untrusted media workers."""

from __future__ import annotations

import re
import hashlib
import json
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Literal

from domain.errors import ValidationError

SHA256 = re.compile(r"^[0-9a-f]{64}$")
SEMVER = re.compile(r"^[1-9][0-9]*\.[0-9]+\.[0-9]+$")


def canonical_manifest_hash(payload: dict[str, Any]) -> str:
    stable = {key: value for key, value in payload.items()
              if key not in {"input_sha256", "job_id", "fence_token"}}
    encoded = json.dumps(stable, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _hash(value: str | None, name: str, required: bool = True) -> None:
    if value is None and not required:
        return
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        raise ValidationError(f"{name} must be a lowercase sha256")


def safe_relative_path(value: str | None) -> None:
    if value is None:
        return
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not value or "\\" in value:
        raise ValidationError("output path must be a safe relative POSIX path")


@dataclass(frozen=True)
class WorkerManifest:
    schema_version: str
    job_id: str
    fence_token: int
    revision_id: str
    input_sha256: str
    output_path: str
    render_profile: str
    payload: dict[str, Any]

    def validate(self) -> None:
        if not SEMVER.fullmatch(self.schema_version):
            raise ValidationError("unsupported manifest schema version")
        if self.fence_token < 1:
            raise ValidationError("manifest requires a fence token")
        _hash(self.input_sha256, "input_sha256")
        safe_relative_path(self.output_path)


@dataclass(frozen=True)
class WorkerReport:
    report_id: str
    job_id: str
    worker_id: str
    fence_token: int
    schema_version: str
    status: Literal["succeeded", "failed"]
    input_sha256: str
    output_path: str | None
    output_sha256: str | None
    duration_ms: int
    render_profile: str | None
    machine_qa: dict[str, Any]
    error_code: str | None
    created_at: str

    def validate(self) -> None:
        if self.status not in ("succeeded", "failed"):
            raise ValidationError("invalid worker report status")
        if not SEMVER.fullmatch(self.schema_version):
            raise ValidationError("unsupported report schema version")
        if self.fence_token < 1 or self.duration_ms < 0:
            raise ValidationError("invalid fence token or duration")
        _hash(self.input_sha256, "input_sha256")
        safe_relative_path(self.output_path)
        _hash(self.output_sha256, "output_sha256", required=self.status == "succeeded")
        if self.status == "succeeded" and (not self.output_path or not self.render_profile):
            raise ValidationError("successful report requires output and render profile")
        if self.status == "failed" and not self.error_code:
            raise ValidationError("failed report requires error_code")
