"""Typed records shared by the production vertical services."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


PublishPlatform = Literal["telegram", "vk", "manual"]
PublishStatus = Literal[
    "draft", "approved", "queued", "sending", "published", "failed",
    "unknown_outcome", "awaiting_manual", "cancelled",
]


@dataclass(frozen=True)
class AssetRecord:
    id: str
    project_id: str
    revision_id: str | None
    role: str
    storage_key: str
    sha256: str
    mime_type: str
    size_bytes: int


@dataclass(frozen=True)
class WorkerReportRecord:
    id: str
    job_id: str
    fence_token: int
    schema_version: str
    status: Literal["succeeded", "failed"]
    input_sha256: str
    output_sha256: str | None
    duration_ms: int


@dataclass(frozen=True)
class PublishJobRecord:
    id: str
    package_id: str
    platform: PublishPlatform
    target_ref: str
    status: PublishStatus
    row_version: int
