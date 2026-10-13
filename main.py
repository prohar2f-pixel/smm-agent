import asyncio
import logging
import os
from dotenv import load_dotenv
from safe_logging import configure_safe_logging

load_dotenv()

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from agent.memory import init_db
from bot.handlers import router
from bot.content_factory_handlers import router as factory_router
from bot.middlewares import AdminOnly
from scheduler import setup_scheduler

configure_safe_logging()
logger = logging.getLogger(__name__)


async def main():
    await init_db()
    logger.info("База данных инициализирована.")

    bot = Bot(token=os.getenv("TELEGRAM_BOT_TOKEN"))
    dp = Dispatcher(storage=MemoryStorage())

    dp.message.middleware(AdminOnly())
    dp.callback_query.middleware(AdminOnly())
    dp.include_router(router)
    dp.include_router(factory_router)

    await bot.set_my_commands([
        BotCommand(command="start", description="Начать / повторный онбординг"),
        BotCommand(command="plan", description="Сгенерировать план на неделю"),
        BotCommand(command="queue", description="Посты на согласование"),
        BotCommand(command="voice", description="Мой профиль стиля"),
        BotCommand(command="competitors", description="Список конкурентов"),
        BotCommand(command="add_tg", description="Добавить TG-канал конкурента"),
        BotCommand(command="add_vk", description="Добавить VK-группу конкурента"),
        BotCommand(command="dzen", description="Текст для Дзена"),
        BotCommand(command="instagram", description="Текст для Instagram"),
        BotCommand(command="factory_status", description="Статус контент-завода"),
        BotCommand(command="factory_queue", description="Очередь контент-завода"),
        BotCommand(command="factory_reconcile", description="Сверка неизвестного результата"),
        BotCommand(command="factory_report", description="Отчёт пилота"),
    ])

    scheduler = setup_scheduler(bot)
    scheduler.start()
    logger.info("Планировщик запущен.")

    logger.info("Бот запущен.")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        scheduler.shutdown()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
