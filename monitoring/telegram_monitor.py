import os
import json
from telethon import TelegramClient
from agent.memory import add_signal, get_setting_json

API_ID = os.getenv("TELETHON_API_ID")
API_HASH = os.getenv("TELETHON_API_HASH")
PHONE = os.getenv("TELETHON_PHONE")

_client: TelegramClient = None


def get_client() -> TelegramClient:
    global _client
    if _client is None:
        _client = TelegramClient("smm_monitor", int(API_ID), API_HASH)
    return _client


async def monitor_telegram_competitors():
    competitors = await get_setting_json("telegram_competitors", [])
    if not competitors:
        return

    client = get_client()
    await client.start(phone=PHONE)

    try:
        for channel in competitors:
            try:
                messages = await client.get_messages(channel, limit=20)
                for msg in messages:
                    if msg.text and len(msg.text) > 50:
                        summary = msg.text[:200].replace("\n", " ").strip()
                        await add_signal(
                            source=channel,
                            source_type="telegram",
                            summary=summary,
                            signal_type="topic",
                        )
            except Exception:
                pass
    finally:
        await client.disconnect()
