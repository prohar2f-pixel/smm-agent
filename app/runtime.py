"""Fail-closed dependency wiring for the content factory."""

from __future__ import annotations

import os
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from db.content_repository import ContentRepository
from db.migrate import run_migrations
from deployment.preflight import validate_pilot
from domain.errors import ValidationError
from domain.job_service import JobService
from domain.publish_service import PublishService
from domain.text_factory import TextFactory
from workers.runner import WorkerRunner
from workers.report_service import ArtifactVerifier, WorkerReportService
from workers.screen_demo import FfmpegScreenDemoRenderer, ScreenDemoJobHandler
from workers.screen_demo import ScreenDemoPlanner
from workers.contracts import canonical_manifest_hash


def _enabled(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class FactoryConfig:
    db_path: Path
    admin_telegram_id: int
    control_plane_enabled: bool = False
    worker_enabled: bool = False
    publishing_enabled: bool = False
    paid_generation_enabled: bool = False
    pilot_id: str | None = None
    artifact_root: Path | None = None

    @classmethod
    def from_env(cls, env=None):
        env = env or os.environ
        admin = env.get("ADMIN_TELEGRAM_ID", "0")
        if not admin.isdigit() or int(admin) <= 0:
            raise ValueError("ADMIN_TELEGRAM_ID must be a positive integer")
        db_path = Path(env.get("DB_PATH", "./data/agent.db"))
        return cls(
            db_path=db_path, admin_telegram_id=int(admin),
            control_plane_enabled=_enabled(env.get("FACTORY_CONTROL_PLANE_ENABLED")),
            worker_enabled=_enabled(env.get("FACTORY_WORKER_ENABLED")),
            publishing_enabled=_enabled(env.get("FACTORY_PUBLISHING_ENABLED")),
            paid_generation_enabled=_enabled(env.get("FACTORY_PAID_GENERATION_ENABLED")),
            pilot_id=env.get("FACTORY_PILOT_ID") or None,
            artifact_root=Path(env["FACTORY_ARTIFACT_ROOT"])
            if env.get("FACTORY_ARTIFACT_ROOT") else None,
        )

    def status(self) -> dict:
        return {"control_plane": self.control_plane_enabled,
                "worker": self.worker_enabled,
                "publishing": self.publishing_enabled,
                "paid_generation": self.paid_generation_enabled}


class FactoryRuntime:
    def __init__(self, config: FactoryConfig):
        self.config = config
        self.repository = ContentRepository(config.db_path)

    def startup(self) -> list[int]:
        if self.config.artifact_root is not None:
            root = self.config.artifact_root.resolve()
            database = self.config.db_path.resolve()
            if database == root or root in database.parents:
                raise ValidationError("FACTORY_ARTIFACT_ROOT cannot contain DB_PATH")
        self.config.db_path.parent.mkdir(parents=True, exist_ok=True)
        applied = run_migrations(self.config.db_path)
        if (self.config.worker_enabled or self.config.publishing_enabled
                or self.config.paid_generation_enabled):
            if not self.config.pilot_id:
                raise ValidationError("enabled production actions require FACTORY_PILOT_ID")
            errors = validate_pilot(self.repository, self.config.pilot_id)
            if errors:
                raise ValidationError("pilot preflight failed: " + "; ".join(errors))
        return applied

    def publish_service(self) -> PublishService:
        return PublishService(
            self.repository, publishing_enabled=self.config.publishing_enabled,
            artifact_root=self.config.artifact_root,
        )

    def text_factory(self, gateway) -> TextFactory:
        return TextFactory(
            self.repository, gateway, enabled=self.config.paid_generation_enabled,
        )

    def worker_runner(self, handlers: dict) -> WorkerRunner:
        if self.config.worker_enabled and self.config.artifact_root is None:
            raise ValidationError("enabled worker requires FACTORY_ARTIFACT_ROOT")
        if self.config.worker_enabled and not handlers:
            raise ValidationError("enabled worker requires registered handlers")
        return WorkerRunner(JobService(self.repository), handlers,
                            enabled=self.config.worker_enabled)

    def worker_handlers(self) -> dict:
        if self.config.artifact_root is None:
            return {}
        self.config.artifact_root.mkdir(parents=True, exist_ok=True)
        renderer = FfmpegScreenDemoRenderer(self.config.artifact_root)
        verifier = ArtifactVerifier(self.config.artifact_root, renderer.probe)
        reports = WorkerReportService(self.repository, verifier)
        return {
            "screen_demo": ScreenDemoJobHandler(
                self.repository, self.config.artifact_root, renderer, reports,
            ),
        }

    def enqueue_screen_demo(self, *, command_id: str, job_id: str, project_id: str,
                            payload: dict, stage: str, created_at: str):
        if not self.config.control_plane_enabled:
            raise ValidationError("factory control plane is disabled")
        project = self.repository.fetchone(
            """SELECT pilot_id,current_revision_id,status FROM content_projects
            WHERE id=?""", (project_id,),
        )
        if (project is None or not project["current_revision_id"]
                or (self.config.pilot_id and project["pilot_id"] != self.config.pilot_id)
                or project["status"] not in ("production_ready", "preview_review", "accepted")):
            raise ValidationError("project is not eligible for screen-demo rendering")
        source = dict(payload)
        source["output_path"] = f"jobs/{job_id}/{stage}.mp4"
        source.update({
            "schema_version": "1.0.0", "job_id": job_id, "fence_token": 1,
            "revision_id": project["current_revision_id"],
            "input_sha256": "0" * 64,
        })
        if stage == "master":
            approved_hash = source.get("approved_preview_sha256")
            preview = self.repository.fetchone(
                """SELECT a.sha256,ad.status FROM assets a JOIN approval_decisions ad
                ON ad.project_id=a.project_id AND ad.revision_id=a.revision_id
                AND ad.subject_sha256=a.sha256
                WHERE a.project_id=? AND a.revision_id=? AND a.role='preview'
                AND ad.stage='preview'
                ORDER BY ad.created_at DESC,ad.id DESC LIMIT 1""",
                (project_id, project["current_revision_id"]),
            )
            if (preview is None or preview["status"] != "approved"
                    or preview["sha256"] != approved_hash):
                raise ValidationError("master requires the exact approved preview asset")
        manifest = ScreenDemoPlanner().plan(
            source, stage=stage,
            approved_preview_sha256=source.get("approved_preview_sha256"),
        )
        job_payload = dict(manifest.payload)
        for transient in ("job_id", "fence_token"):
            job_payload.pop(transient, None)
        job_payload.update({
            "output_path": manifest.output_path,
            "render_profile": manifest.render_profile,
            "asset_role": "preview" if stage == "preview" else "master",
            "mime_type": "video/mp4",
            "duration_ms": round(float(source["duration_sec"]) * 1000),
        })
        job_payload["input_sha256"] = canonical_manifest_hash(job_payload)
        return JobService(self.repository).enqueue(
            job_id, command_id, "screen_demo", project["pilot_id"], project_id,
            project["current_revision_id"], job_payload, created_at, created_at,
        )
