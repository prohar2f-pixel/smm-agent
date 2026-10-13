"""Adapter boundary for independently retryable platform publishing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class PublishRequest:
    idempotency_key: str
    target_ref: str
    caption_text: str
    assets_json: str


@dataclass(frozen=True)
class PublishResult:
    external_id: str
    evidence_ref: str


class DefinitivePublishError(RuntimeError):
    """Provider proved that no remote publication was accepted."""


class PublisherAdapter(Protocol):
    provider_key: str
    def send(self, request: PublishRequest) -> PublishResult: ...

    def reconcile(self, idempotency_key: str) -> PublishResult | None: ...
