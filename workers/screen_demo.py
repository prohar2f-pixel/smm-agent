"""Pure planning for the first 30-45 second screen-demo format."""

from __future__ import annotations

from copy import deepcopy

from domain.errors import ValidationError
from workers.contracts import SHA256, WorkerManifest, safe_relative_path


class ScreenDemoPlanner:
    def plan(self, source: dict, *, stage: str,
             approved_preview_sha256: str | None = None) -> WorkerManifest:
        payload = deepcopy(source)
        if stage not in ("preview", "master"):
            raise ValidationError("stage must be preview or master")
        duration = payload.get("duration_sec")
        if not isinstance(duration, (int, float)) or not 30 <= duration <= 45:
            raise ValidationError("screen demo duration must be 30-45 seconds")
        for key in ("audio_path", "subtitles_path"):
            if not payload.get(key):
                raise ValidationError(f"{key} is required")
            safe_relative_path(payload[key])
        clips = payload.get("clips")
        if not isinstance(clips, list) or not clips:
            raise ValidationError("at least one source clip is required")
        for clip in clips:
            safe_relative_path(clip.get("path"))
            if not SHA256.fullmatch(str(clip.get("sha256", ""))):
                raise ValidationError("every clip requires an exact sha256")
            if clip.get("end_sec", 0) <= clip.get("start_sec", 0):
                raise ValidationError("clip end must follow start")
        if payload.get("width", 0) > 1920 or payload.get("height", 0) > 1920:
            raise ValidationError("frame dimensions exceed worker cap")
        if payload.get("fps", 0) not in range(1, 31):
            raise ValidationError("fps exceeds worker cap")
        if payload.get("max_memory_mb", 0) > 4096:
            raise ValidationError("memory request exceeds worker cap")
        if stage == "master" and not SHA256.fullmatch(approved_preview_sha256 or ""):
            raise ValidationError("master requires exact approved preview hash")
        output_path = payload["output_path"].replace("preview", stage)
        payload["stage"] = stage
        payload["approved_preview_sha256"] = approved_preview_sha256
        manifest = WorkerManifest(
            schema_version=payload["schema_version"], job_id=payload["job_id"],
            revision_id=payload["revision_id"], input_sha256=payload["input_sha256"],
            output_path=output_path, render_profile=f"screen-demo-{stage}-v1",
            payload=payload,
        )
        manifest.validate()
        return manifest
