"""Deterministic technical checks run before human review."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class QAResult:
    passed: bool
    failures: tuple[str, ...]
    actual_sha256: str


class TechnicalQA:
    @staticmethod
    def inspect(data: bytes, *, expected_sha256: str, duration_sec: float,
                width: int, height: int, has_audio: bool,
                has_subtitles: bool) -> QAResult:
        actual = hashlib.sha256(data).hexdigest()
        failures: list[str] = []
        if actual != expected_sha256:
            failures.append("checksum")
        if not 30 <= duration_sec <= 45:
            failures.append("duration")
        if (width, height) != (1080, 1920):
            failures.append("dimensions")
        if not has_audio:
            failures.append("audio")
        if not has_subtitles:
            failures.append("subtitles")
        return QAResult(not failures, tuple(failures), actual)
