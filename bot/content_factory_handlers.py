"""Owner-only read/control commands for the production factory."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.runtime import FactoryConfig

router = Router()


@router.message(Command("factory_status"))
async def factory_status(message: Message):
    try:
        config = FactoryConfig.from_env()
        flags = config.status()
        lines = [f"{key}: {'ON' if value else 'OFF'}" for key, value in flags.items()]
        await message.answer("Контент-завод\n" + "\n".join(lines))
    except ValueError as exc:
        await message.answer(f"Контент-завод: configuration error ({exc})")


@router.message(Command("factory_queue"))
async def factory_queue(message: Message):
    await message.answer("Очередь доступна через production DB; внешние действия здесь не запускаются.")


@router.message(Command("factory_reconcile"))
async def factory_reconcile(message: Message):
    await message.answer("Reconcile запускается только для конкретного unknown_outcome задания.")


@router.message(Command("factory_report"))
async def factory_report(message: Message):
    await message.answer("Отчёт формируется только из зафиксированных production-данных.")
