import asyncio
import logging
import os
from dotenv import load_dotenv

load_dotenv()

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from agent.memory import init_db
from bot.handlers import router
from bot.middlewares import AdminOnly
from scheduler import setup_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main():
    await init_db()
    logger.info("База данных инициализирована.")

    bot = Bot(token=os.getenv("TELEGRAM_BOT_TOKEN"))
    dp = Dispatcher(storage=MemoryStorage())

    dp.message.middleware(AdminOnly())
    dp.callback_query.middleware(AdminOnly())
    dp.include_router(router)

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
