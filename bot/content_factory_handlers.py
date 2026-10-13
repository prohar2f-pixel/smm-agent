"""Owner-only read/control commands for the production factory."""

from __future__ import annotations

import asyncio

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.runtime import FactoryConfig
from db.content_repository import ContentRepository
from deployment.preflight import validate_pilot
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
