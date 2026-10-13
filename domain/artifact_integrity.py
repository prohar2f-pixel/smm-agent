"""Filesystem integrity checks for persisted media assets."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from domain.errors import ConflictError


def verified_asset_path(root: str | Path, storage_key: str, expected_sha256: str) -> Path:
    base = Path(root).resolve()
    candidate = base / storage_key
    cursor = base
    for part in Path(storage_key).parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ConflictError("asset path contains a symlink")
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(base)
    except (FileNotFoundError, ValueError) as exc:
        raise ConflictError("asset is missing or outside artifact root") from exc
    descriptor = os.open(resolved, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
    finally:
        os.close(descriptor)
    if digest.hexdigest() != expected_sha256:
        raise ConflictError("persisted asset hash mismatch")
    return resolved
