"""Adapter boundary for independently retryable platform publishing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import BinaryIO, Protocol


@dataclass(frozen=True)
class VerifiedAssetHandle:
    asset_id: str
    sha256: str
    mime_type: str
    size_bytes: int
    stream: BinaryIO

    @property
    def closed(self) -> bool:
        return self.stream.closed

    def read(self, size: int = -1) -> bytes:
        return self.stream.read(size)

    def rewind(self) -> None:
        self.stream.seek(0)

    def close(self) -> None:
        self.stream.close()


@dataclass(frozen=True)
class PublishRequest:
    idempotency_key: str
    target_ref: str
    caption_text: str
    assets_json: str
    assets: tuple[VerifiedAssetHandle, ...] = ()

    def evidence_payload(self) -> dict:
        return {
            "idempotency_key": self.idempotency_key,
            "target_ref": self.target_ref,
            "caption_text": self.caption_text,
            "assets_json": self.assets_json,
            "assets": [
                {"asset_id": asset.asset_id, "sha256": asset.sha256,
                 "mime_type": asset.mime_type, "size_bytes": asset.size_bytes}
                for asset in self.assets
            ],
        }


@dataclass(frozen=True)
class PublishResult:
    external_id: str
    evidence_ref: str

    def validate(self) -> None:
        if not self.external_id.strip() or not self.evidence_ref.strip():
            raise ValueError("publish result requires external id and evidence")


class DefinitivePublishError(RuntimeError):
    """Provider proved that no remote publication was accepted."""


class PublisherAdapter(Protocol):
    platform: str
    provider_key: str
    def send(self, request: PublishRequest) -> PublishResult: ...

    def reconcile(self, idempotency_key: str) -> PublishResult | None: ...
