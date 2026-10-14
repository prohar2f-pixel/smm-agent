"""Owner-only read/control commands for the production factory."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
import uuid

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.runtime import FactoryConfig, FactoryRuntime
from db.content_repository import ContentRepository
from deployment.preflight import validate_pilot
from domain.approval_service import ApprovalService
from domain.publish_service import PublishService
from reporting.pilot_report import PilotReportBuilder

router = Router()


def _context():
    config = FactoryConfig.from_env()
    return config, ContentRepository(config.db_path)


def _status_text() -> str:
    config, repository = _context()
    flags = config.status()
    version = repository.fetchone("SELECT MAX(version) AS version FROM schema_migrations")
    jobs = repository.fetchone(
        "SELECT COUNT(*) AS total,SUM(status='unknown_outcome') AS unknown FROM jobs"
    )
    publish = repository.fetchone(
        "SELECT COUNT(*) AS total,SUM(status='unknown_outcome') AS unknown FROM publish_jobs"
    )
    lines = [f"{key}: {'ON' if value else 'OFF'}" for key, value in flags.items()]
    lines.extend([
        f"schema: v{version['version']}",
        f"jobs: {jobs['total']} (unknown: {jobs['unknown'] or 0})",
        f"publish jobs: {publish['total']} (unknown: {publish['unknown'] or 0})",
    ])
    return "Контент-завод\n" + "\n".join(lines)


def _pilot_text() -> str:
    config, repository = _context()
    if not config.pilot_id:
        return "FACTORY_PILOT_ID не выбран. Производственные действия заблокированы."
    pilot = repository.fetchone(
        "SELECT id,name,status,target_count FROM pilot_passports WHERE id=?",
        (config.pilot_id,),
    )
    if pilot is None:
        return f"Пилот {config.pilot_id} не найден."
    errors = validate_pilot(repository, config.pilot_id)
    readiness = "готов" if not errors else "заблокирован: " + "; ".join(errors)
    return (f"Пилот {pilot['name']} ({pilot['id']})\n"
            f"Статус: {pilot['status']}\nЦель: {pilot['target_count']}\n"
            f"Preflight: {readiness}")


def _queue_text() -> str:
    _, repository = _context()
    rows = repository.fetchall(
        "SELECT kind,status,COUNT(*) AS count FROM jobs GROUP BY kind,status ORDER BY kind,status"
    )
    publishes = repository.fetchall(
        "SELECT platform,status,COUNT(*) AS count FROM publish_jobs GROUP BY platform,status ORDER BY platform,status"
    )
    lines = ["Очередь заданий"]
    lines.extend(f"- {row['kind']} / {row['status']}: {row['count']}" for row in rows)
    lines.append("Публикации")
    lines.extend(f"- {row['platform']} / {row['status']}: {row['count']}" for row in publishes)
    if not rows and not publishes:
        lines.append("- пусто")
    return "\n".join(lines)


def _reconcile_text() -> str:
    _, repository = _context()
    operations = repository.fetchall(
        """SELECT id,operation_kind,provider_key FROM external_operations
        WHERE status IN ('submitting','unknown_outcome') ORDER BY created_at,id LIMIT 20"""
    )
    if not operations:
        return "Неопределённых внешних операций нет."
    lines = ["Требуют ручной сверки с провайдером:"]
    lines.extend(f"- {row['id']} / {row['operation_kind']} / {row['provider_key']}"
                 for row in operations)
    lines.append("Автоматический повтор не выполнялся.")
    return "\n".join(lines)


def _report_text() -> str:
    config, repository = _context()
    if not config.pilot_id:
        return "FACTORY_PILOT_ID не выбран, отчёт не сформирован."
    report = PilotReportBuilder(repository).build(config.pilot_id)
    return report.markdown[:3900]


async def _answer(message: Message, builder):
    try:
        text = await asyncio.to_thread(builder)
    except Exception as exc:
        await message.answer(f"Контент-завод: безопасная ошибка ({type(exc).__name__})")
        return
    await message.answer(text)


def _json_argument(message: Message) -> dict:
    text = message.text or ""
    parts = text.split(maxsplit=1)
    if len(parts) != 2:
        raise ValueError("после команды нужен JSON")
    value = json.loads(parts[1])
    if not isinstance(value, dict):
        raise ValueError("аргумент должен быть JSON-объектом")
    return value


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@router.message(Command("factory_status"))
async def factory_status(message: Message):
    await _answer(message, _status_text)


@router.message(Command("factory_pilot"))
async def factory_pilot(message: Message):
    await _answer(message, _pilot_text)


@router.message(Command("factory_queue"))
async def factory_queue(message: Message):
    await _answer(message, _queue_text)


@router.message(Command("factory_reconcile"))
async def factory_reconcile(message: Message):
    await _answer(message, _reconcile_text)


@router.message(Command("factory_report"))
async def factory_report(message: Message):
    await _answer(message, _report_text)


@router.message(Command("factory_enqueue"))
async def factory_enqueue(message: Message):
    def enqueue():
        payload = _json_argument(message)
        project_id = str(payload.pop("project_id"))
        stage = str(payload.pop("stage", "preview"))
        runtime = FactoryRuntime(FactoryConfig.from_env())
        command_id = f"telegram:{message.chat.id}:{message.message_id}:factory_enqueue"
        job_id = str(uuid.uuid5(uuid.NAMESPACE_URL, command_id))
        row = runtime.enqueue_screen_demo(
            command_id=command_id, job_id=job_id, project_id=project_id,
            payload=payload, stage=stage, created_at=_now(),
        )
        return f"Render job {row.id}: {row.status}"
    await _answer(message, enqueue)


@router.message(Command("factory_approve"))
async def factory_approve(message: Message):
    def approve():
        payload = _json_argument(message)
        config, repository = _context()
        command_id = f"telegram:{message.chat.id}:{message.message_id}:factory_approve"
        payload.update({
            "decision_id": str(uuid.uuid5(uuid.NAMESPACE_URL, command_id)),
            "actor_id": str(config.admin_telegram_id), "created_at": _now(),
        })
        row = ApprovalService(repository).decide(**payload)
        return f"Approval {row['id']}: {row['stage']} / {row['status']}"
    await _answer(message, approve)


@router.message(Command("factory_publish_enqueue"))
async def factory_publish_enqueue(message: Message):
    def enqueue_publish():
        payload = _json_argument(message)
        config, repository = _context()
        command_id = f"telegram:{message.chat.id}:{message.message_id}:publish_enqueue"
        row = PublishService(
            repository, publishing_enabled=config.publishing_enabled,
            artifact_root=config.artifact_root,
        ).enqueue(
            job_id=str(uuid.uuid5(uuid.NAMESPACE_URL, command_id)),
            package_id=str(payload["package_id"]), platform=str(payload["platform"]),
            target_ref=str(payload["target_ref"]), created_at=_now(),
        )
        return (f"Publish job {row['id']}: {row['status']}. "
                "Отправка не выполнялась этой командой.")
    await _answer(message, enqueue_publish)
