"""Spend-gated contracts for episodic generative video."""

from __future__ import annotations

from dataclasses import dataclass

from domain.errors import ValidationError
from workers.contracts import SHA256


@dataclass(frozen=True)
class GenerationTask:
    shot_id: str
    aspect_ratio: str
    prompt: str
    animatic_sha256: str
    cost_upper_bound_minor: int
    currency: str


class GenerativeVideoPlanner:
    def plan_shot(self, *, feature_enabled: bool, shot_id: str, prompt: str,
                  approved_animatic_sha256: str | None,
                  cost_upper_bound_minor: int, currency: str) -> tuple[GenerationTask, ...]:
        if not feature_enabled:
            raise ValidationError("generative video feature is disabled")
        if not SHA256.fullmatch(approved_animatic_sha256 or ""):
            raise ValidationError("paid generation requires approved animatic")
        if cost_upper_bound_minor <= 0 or len(currency) != 3:
            raise ValidationError("paid generation requires a bounded budget")
        return tuple(GenerationTask(shot_id, ratio, prompt, approved_animatic_sha256,
                                    cost_upper_bound_minor, currency)
                     for ratio in ("16:9", "9:16"))

    @staticmethod
    def validate_attempt(*, cost_minor: int, currency: str, cost_evidence_ref: str):
        if cost_minor < 0 or len(currency) != 3 or not cost_evidence_ref:
            raise ValidationError("generation attempt requires cost evidence")
