import os
from aiogram import Bot

CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")


async def publish_to_telegram(bot: Bot, text: str) -> int:
    msg = await bot.send_message(
        chat_id=CHANNEL_ID,
        text=text,
        parse_mode="HTML",
        disable_web_page_preview=False,
    )
    return msg.message_id


async def delete_from_telegram(bot: Bot, message_id: int):
    try:
        await bot.delete_message(chat_id=CHANNEL_ID, message_id=message_id)
    except Exception:
        pass
