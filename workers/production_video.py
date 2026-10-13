"""Provider-neutral production video manifest planning."""

from __future__ import annotations

from domain.errors import ValidationError
from workers.contracts import SHA256, safe_relative_path


class ProductionVideoPlanner:
    def plan(self, *, stage: str, segments: list[dict], output_path: str,
             approved_preview_sha256: str | None = None) -> dict:
        if stage not in ("preview", "master") or not segments:
            raise ValidationError("production video requires stage and segments")
        if stage == "master" and not SHA256.fullmatch(approved_preview_sha256 or ""):
            raise ValidationError("master requires approved preview hash")
        safe_relative_path(output_path)
        for segment in segments:
            safe_relative_path(segment.get("path"))
            if segment.get("adapter") not in ("screen", "avatar", "broll"):
                raise ValidationError("unknown replaceable video adapter")
        return {"stage": stage, "segments": segments, "output_path": output_path,
                "approved_preview_sha256": approved_preview_sha256}
