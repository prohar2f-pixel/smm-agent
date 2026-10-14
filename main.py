import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from safe_logging import configure_safe_logging

load_dotenv()

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from app.runtime import FactoryConfig, FactoryRuntime
from bot.handlers import router
from bot.content_factory_handlers import (configure_publish_adapters,
                                          router as factory_router)
from bot.middlewares import AdminOnly
from scheduler import setup_scheduler
from publishing.telegram_pub import AiogramTelegramTransport, TelegramPublisherAdapter
from publishing.vk_pub import VkApiTransport, VkPublisherAdapter

configure_safe_logging()
logger = logging.getLogger(__name__)


async def main():
    config = FactoryConfig.from_env()
    runtime = FactoryRuntime(config)
    await asyncio.to_thread(runtime.startup)
    publish_service = runtime.publish_service()
    worker_runner = runtime.worker_runner(runtime.worker_handlers())
    logger.info("База данных и production runtime инициализированы: %s", config.status())

    async def recover_worker_leases():
        while True:
            try:
                now = datetime.now(timezone.utc).isoformat()
                await asyncio.to_thread(worker_runner.recover, now)
                lease_until = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
                await asyncio.to_thread(
                    worker_runner.run_once, "content-factory-main", now, lease_until,
                )
            except Exception:
                logger.exception("Content factory worker iteration failed")
            await asyncio.sleep(30)

    worker_task = (asyncio.create_task(recover_worker_leases())
                   if config.worker_enabled else None)
    # Keep the guarded service alive in the composition root. Adapters are
    # injected only by an explicitly authorized command path.
    _ = publish_service

    proxy = os.getenv("TELEGRAM_HTTP_PROXY")
    bot_options = {"session": AiohttpSession(proxy=proxy)} if proxy else {}
    bot = Bot(token=os.getenv("TELEGRAM_BOT_TOKEN"), **bot_options)
    event_loop = asyncio.get_running_loop()
    publish_adapters = {
        "telegram": TelegramPublisherAdapter(AiogramTelegramTransport(bot, event_loop)),
    }
    if os.getenv("VK_TOKEN"):
        publish_adapters["vk"] = VkPublisherAdapter(VkApiTransport(os.environ["VK_TOKEN"]))
    configure_publish_adapters(publish_adapters)

    async def consume_publish_jobs():
        while True:
            try:
                rows = runtime.repository.fetchall(
                    """SELECT id,platform FROM publish_jobs WHERE status='queued'
                    ORDER BY created_at,id LIMIT 10"""
                )
                for row in rows:
                    adapter = publish_adapters.get(row["platform"])
                    if adapter is None:
                        logger.error("No publish adapter for platform %s", row["platform"])
                        continue
                    await asyncio.to_thread(publish_service.execute, row["id"], adapter,
                                            datetime.now(timezone.utc).isoformat())
            except Exception:
                logger.exception("Content factory publish iteration failed")
            await asyncio.sleep(30)

    publish_task = (asyncio.create_task(consume_publish_jobs())
                    if config.publishing_enabled else None)
    dp = Dispatcher(storage=MemoryStorage())

    dp.message.middleware(AdminOnly())
    dp.callback_query.middleware(AdminOnly())
    dp.include_router(router)
    if config.control_plane_enabled:
        dp.include_router(factory_router)

    commands = [
        BotCommand(command="start", description="Начать / повторный онбординг"),
        BotCommand(command="plan", description="Сгенерировать план на неделю"),
        BotCommand(command="queue", description="Посты на согласование"),
        BotCommand(command="voice", description="Мой профиль стиля"),
        BotCommand(command="competitors", description="Список конкурентов"),
        BotCommand(command="add_tg", description="Добавить TG-канал конкурента"),
        BotCommand(command="add_vk", description="Добавить VK-группу конкурента"),
        BotCommand(command="dzen", description="Текст для Дзена"),
        BotCommand(command="instagram", description="Текст для Instagram"),
    ]
    if config.control_plane_enabled:
        commands.extend([
            BotCommand(command="factory_status", description="Статус контент-завода"),
            BotCommand(command="factory_pilot", description="Готовность пилота"),
            BotCommand(command="factory_queue", description="Очередь контент-завода"),
            BotCommand(command="factory_reconcile", description="Сверка неизвестного результата"),
            BotCommand(command="factory_report", description="Отчёт пилота"),
            BotCommand(command="factory_enqueue", description="Поставить screen-demo в очередь"),
            BotCommand(command="factory_approve", description="Точное решение по hash"),
            BotCommand(command="factory_qa", description="QA по точному asset"),
            BotCommand(command="factory_publish_enqueue", description="Подготовить publish job"),
            BotCommand(command="factory_manual_confirm", description="Подтвердить ручную публикацию"),
            BotCommand(command="factory_reconcile_confirm", description="Закрыть unknown по evidence"),
        ])
    await bot.set_my_commands(commands)

    scheduler = setup_scheduler(bot)
    scheduler.start()
    logger.info("Планировщик запущен.")

    logger.info("Бот запущен.")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        if worker_task:
            worker_task.cancel()
        if publish_task:
            publish_task.cancel()
        scheduler.shutdown()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
