"""Fail-closed dependency wiring for the content factory."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from db.content_repository import ContentRepository
from db.migrate import run_migrations


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
        return run_migrations(self.config.db_path)
