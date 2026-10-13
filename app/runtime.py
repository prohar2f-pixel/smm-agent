"""Fail-closed dependency wiring for the content factory."""

from __future__ import annotations

import os
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
