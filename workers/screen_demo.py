"""Pure planning for the first 30-45 second screen-demo format."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

from domain.errors import ValidationError
from domain.qa_service import TechnicalQA
from workers.contracts import SHA256, WorkerManifest, WorkerReport, safe_relative_path


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
            hash_key = key.replace("_path", "_sha256")
            if not SHA256.fullmatch(str(payload.get(hash_key, ""))):
                raise ValidationError(f"{hash_key} is required")
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
            fence_token=payload["fence_token"],
            revision_id=payload["revision_id"], input_sha256=payload["input_sha256"],
            output_path=output_path, render_profile=f"screen-demo-{stage}-v1",
            payload=payload,
        )
        manifest.validate()
        return manifest


def _rooted(root: Path, relative: str, *, must_exist: bool) -> Path:
    safe_relative_path(relative)
    root = root.resolve()
    candidate = root / relative
    cursor = root
    for part in Path(relative).parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValidationError("media path contains a symlink")
    try:
        resolved = candidate.resolve(strict=must_exist)
        resolved.relative_to(root)
    except (FileNotFoundError, ValueError) as exc:
        raise ValidationError("media path is missing or outside artifact root") from exc
    return resolved


class FfmpegScreenDemoRenderer:
    """Local, network-free renderer using argument arrays and bounded runtime."""

    def __init__(self, artifact_root: str | Path, *, timeout_sec: int = 300):
        self.root = Path(artifact_root)
        self.timeout_sec = timeout_sec

    def render(self, manifest: WorkerManifest) -> dict:
        payload = manifest.payload
        clip = payload["clips"][0]
        clip_path = _rooted(self.root, clip["path"], must_exist=True)
        if hashlib.sha256(clip_path.read_bytes()).hexdigest() != clip["sha256"]:
            raise ValidationError("source clip hash mismatch")
        audio_path = _rooted(self.root, payload["audio_path"], must_exist=True)
        subtitles_path = _rooted(self.root, payload["subtitles_path"], must_exist=True)
        for path, hash_key in ((audio_path, "audio_sha256"),
                               (subtitles_path, "subtitles_sha256")):
            if hashlib.sha256(path.read_bytes()).hexdigest() != payload[hash_key]:
                raise ValidationError(f"{hash_key} mismatch")
        output = _rooted(self.root, manifest.output_path, must_exist=False)
        output.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(suffix=".mp4", dir=output.parent)
        os.close(descriptor)
        temporary = Path(temporary_name)
        subtitle_filter = str(subtitles_path).replace("'", "\\'").replace(":", "\\:")
        command = (
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-ss", str(clip["start_sec"]), "-i", str(clip_path), "-i", str(audio_path),
            "-t", str(payload["duration_sec"]), "-map", "0:v:0", "-map", "1:a:0",
            "-vf", (f"scale={payload['width']}:{payload['height']}:force_original_aspect_ratio=decrease,"
                    f"pad={payload['width']}:{payload['height']}:(ow-iw)/2:(oh-ih)/2,"
                    f"subtitles='{subtitle_filter}'"),
            "-r", str(payload["fps"]), "-c:v", "libx264", "-preset", "medium",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart",
            str(temporary),
        )
        try:
            subprocess.run(command, check=True, timeout=self.timeout_sec,
                           stdin=subprocess.DEVNULL, capture_output=True)
            os.replace(temporary, output)
        finally:
            temporary.unlink(missing_ok=True)
        return self.probe(output)

    def probe(self, path: Path) -> dict:
        command = (
            "ffprobe", "-v", "error", "-show_streams", "-show_format",
            "-of", "json", str(path),
        )
        completed = subprocess.run(command, check=True, timeout=30,
                                   stdin=subprocess.DEVNULL, capture_output=True, text=True)
        data = json.loads(completed.stdout)
        streams = data.get("streams", [])
        video = next((item for item in streams if item.get("codec_type") == "video"), {})
        duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0)
        return {
            "mime_type": "video/mp4", "duration_ms": round(duration * 1000),
            "width": int(video.get("width") or 0), "height": int(video.get("height") or 0),
            "has_audio": any(item.get("codec_type") == "audio" for item in streams),
            "has_subtitles": True,
        }


class ScreenDemoJobHandler:
    def __init__(self, repository, artifact_root, renderer, report_service, *, now=None):
        self.repository = repository
        self.root = Path(artifact_root)
        self.renderer = renderer
        self.report_service = report_service
        self.now = now or (lambda: datetime.now(timezone.utc).isoformat())
        self.planner = ScreenDemoPlanner()

    def __call__(self, claim):
        row = self.repository.fetchone(
            "SELECT payload_json,revision_id FROM jobs WHERE id=?", (claim.id,),
        )
        if row is None:
            raise ValidationError("claimed render job disappeared")
        payload = json.loads(row["payload_json"])
        payload.update({"job_id": claim.id, "fence_token": claim.fence_token,
                        "revision_id": row["revision_id"]})
        stage = payload.get("stage", "preview")
        manifest = self.planner.plan(
            payload, stage=stage,
            approved_preview_sha256=payload.get("approved_preview_sha256"),
        )
        metadata = self.renderer.render(manifest)
        output = _rooted(self.root, manifest.output_path, must_exist=True)
        data = output.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        qa = TechnicalQA.inspect(
            data, expected_sha256=digest,
            duration_sec=metadata["duration_ms"] / 1000,
            width=metadata["width"], height=metadata["height"],
            has_audio=metadata["has_audio"], has_subtitles=metadata["has_subtitles"],
        )
        if not qa.passed:
            raise ValidationError("technical QA failed: " + ",".join(qa.failures))
        created_at = self.now()
        report = WorkerReport(
            report_id=f"{claim.id}:{claim.fence_token}", job_id=claim.id,
            worker_id=claim.lease_owner, fence_token=claim.fence_token,
            schema_version=manifest.schema_version, status="succeeded",
            input_sha256=manifest.input_sha256, output_path=manifest.output_path,
            output_sha256=digest, duration_ms=metadata["duration_ms"],
            render_profile=manifest.render_profile,
            machine_qa={"checks": ["checksum", "duration", "dimensions", "audio", "subtitles"]},
            error_code=None, created_at=created_at,
        )
        return self.report_service.accept(report, created_at)
