"""Offline-only carousel production contract."""

from __future__ import annotations

from dataclasses import dataclass

from domain.errors import ValidationError
from workers.contracts import safe_relative_path


@dataclass(frozen=True)
class CarouselManifest:
    slides: tuple[dict, ...]
    output_path: str
    network_enabled: bool = False


class CarouselPlanner:
    def plan(self, slides: list[dict], output_path: str, *, network_enabled=False):
        if network_enabled:
            raise ValidationError("carousel worker network must remain disabled")
        if len(slides) < 2:
            raise ValidationError("carousel requires at least two slides")
        safe_relative_path(output_path)
        for slide in slides:
            safe_relative_path(slide.get("asset_path"))
        avatar_positions = [index for index, slide in enumerate(slides) if slide.get("avatar")]
        if avatar_positions and avatar_positions != [0, len(slides) - 1]:
            raise ValidationError("avatar is allowed only on first and last slides")
        return CarouselManifest(tuple(slides), output_path)
