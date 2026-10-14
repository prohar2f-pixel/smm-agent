import os
from typing import TYPE_CHECKING, Callable

from publishing.base import PublishRequest, PublishResult

if TYPE_CHECKING:
    from aiogram import Bot

CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")


class TelegramPublisherAdapter:
    """Telegram boundary fed only with bytes from verified open handles."""

    platform = "telegram"
    provider_key = "telegram-bot-api"

    def __init__(self, transport: Callable[..., dict], reconcile_transport=None):
        self.transport = transport
        self.reconcile_transport = reconcile_transport

    def send(self, request: PublishRequest) -> PublishResult:
        assets = []
        for asset in request.assets:
            asset.rewind()
            assets.append({
                "asset_id": asset.asset_id, "sha256": asset.sha256,
                "mime_type": asset.mime_type, "size_bytes": asset.size_bytes,
                "bytes": asset.read(),
            })
        response = self.transport(
            target_ref=request.target_ref, caption_text=request.caption_text,
            assets=tuple(assets), idempotency_key=request.idempotency_key,
        )
        return PublishResult(str(response.get("external_id", "")),
                             str(response.get("evidence_ref", "")))

    def reconcile(self, idempotency_key: str) -> PublishResult | None:
        if self.reconcile_transport is None:
            return None
        response = self.reconcile_transport(idempotency_key=idempotency_key)
        if response is None:
            return None
        return PublishResult(str(response.get("external_id", "")),
                             str(response.get("evidence_ref", "")))


async def publish_to_telegram(bot: "Bot", text: str) -> int:
    """Legacy direct publisher. New production flow uses PublishService guards."""
    msg = await bot.send_message(
        chat_id=CHANNEL_ID,
        text=text,
        parse_mode="HTML",
        disable_web_page_preview=False,
    )
    return msg.message_id


async def delete_from_telegram(bot: "Bot", message_id: int):
    try:
        await bot.delete_message(chat_id=CHANNEL_ID, message_id=message_id)
    except Exception:
        pass
