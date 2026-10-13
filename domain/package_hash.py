"""Canonical hashing for immutable publish packages."""

from __future__ import annotations

import hashlib
import json

from domain.errors import ValidationError


def canonical_package_hash(caption_text: str, assets_json: str) -> str:
    try:
        assets = json.loads(assets_json)
    except json.JSONDecodeError as exc:
        raise ValidationError("package assets must be valid JSON") from exc
    if not isinstance(assets, list) or any(not isinstance(item, str) for item in assets):
        raise ValidationError("package assets must be an array of asset ids")
    canonical = json.dumps(
        {"assets": assets, "caption_text": caption_text},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
