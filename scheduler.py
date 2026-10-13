import asyncio
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from monitoring.rss_fetcher import fetch_rss
from monitoring.telegram_monitor import monitor_telegram_competitors
from monitoring.vk_monitor import monitor_vk_competitors

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler = None


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
    return _scheduler


def setup_scheduler(bot=None):
    scheduler = get_scheduler()

    # Ежедневно в 08:00 — мониторинг RSS
    scheduler.add_job(
        fetch_rss,
        CronTrigger(hour=8, minute=0),
        id="rss_fetch",
        replace_existing=True,
    )

    # Ежедневно в 08:30 — мониторинг TG-конкурентов
    scheduler.add_job(
        monitor_telegram_competitors,
        CronTrigger(hour=8, minute=30),
        id="tg_monitor",
        replace_existing=True,
    )

    # Ежедневно в 08:45 — мониторинг VK-конкурентов
    scheduler.add_job(
        monitor_vk_competitors,
        CronTrigger(hour=8, minute=45),
        id="vk_monitor",
        replace_existing=True,
    )

    # Каждый понедельник в 09:00 — отправить напоминание о генерации плана
    if bot:
        from agent.memory import get_setting
        import os

        async def send_plan_reminder():
            admin_id = int(os.getenv("ADMIN_TELEGRAM_ID", "0"))
            if admin_id:
                await bot.send_message(
                    chat_id=admin_id,
                    text="📅 Новая неделя! Отправь /plan чтобы сгенерировать контент-план.",
                )

        scheduler.add_job(
            send_plan_reminder,
            CronTrigger(day_of_week="mon", hour=9, minute=0),
            id="plan_reminder",
            replace_existing=True,
        )

        # Каждые 12 часов — проверить посты с таймаутом
        async def check_timeouts():
            from agent.memory import get_pending_posts, update_post_status
            from datetime import datetime, timedelta

            posts = await get_pending_posts()
            for post in posts:
                created = datetime.fromisoformat(post["created_at"])
                if datetime.utcnow() - created > timedelta(hours=48):
                    await update_post_status(post["id"], "postponed")
                    await bot.send_message(
                        chat_id=admin_id,
                        text=f"⏰ Пост #{post['id']} «{post['topic'][:40]}» перенесён — нет ответа 48ч.",
                    )
                elif datetime.utcnow() - created > timedelta(hours=24):
                    await bot.send_message(
                        chat_id=admin_id,
                        text=f"⏰ Напоминание: пост #{post['id']} «{post['topic'][:40]}» ждёт согласования.",
                    )

        scheduler.add_job(
            check_timeouts,
            CronTrigger(hour="*/12"),
            id="timeout_check",
            replace_existing=True,
        )

        # Каждый час — публикация перенесённых постов у которых наступило время
        async def auto_publish():
            from agent.memory import get_due_posts, update_post_status
            from publishing.telegram_pub import publish_to_telegram
            from publishing.vk_pub import publish_to_vk

            posts = await get_due_posts()
            for post in posts:
                try:
                    tg_msg_id = await publish_to_telegram(bot, post["content_telegram"])
                    vk_post_id = await publish_to_vk(post["content_vk"])
                    await update_post_status(
                        post["id"], "published",
                        telegram_message_id=tg_msg_id,
                        vk_post_id=vk_post_id,
                    )
                    await bot.send_message(
                        chat_id=admin_id,
                        text=f"✅ Пост #{post['id']} «{post['topic'][:40]}» опубликован автоматически.",
                    )
                except Exception as e:
                    await bot.send_message(
                        chat_id=admin_id,
                        text=f"❌ Ошибка авто-публикации поста #{post['id']}: {e}",
                    )

        # Legacy auto-publication is off by default. The production factory uses
        # exact package approvals and PublishService instead of scheduled sends.
        if os.getenv("LEGACY_AUTO_PUBLISH_ENABLED", "false").lower() == "true":
            scheduler.add_job(
                auto_publish,
                CronTrigger(minute=0),
                id="auto_publish",
                replace_existing=True,
            )

    return scheduler
