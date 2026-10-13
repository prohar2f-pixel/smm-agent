import os
import logging
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from agent.memory import (
    get_post, update_post_status, get_pending_posts,
    add_topic_used, create_post, get_setting_json, set_setting_json,
)
from agent.style_engine import (
    is_onboarding_done, get_current_question_index, save_answer,
    finish_onboarding, get_brand_voice_prompt, update_brand_voice_from_feedback,
    ONBOARDING_QUESTIONS,
)
from agent.content_planner import generate_weekly_plan, format_plan_message
from agent.post_generator import generate_post, rewrite_post
from publishing.telegram_pub import publish_to_telegram
from publishing.vk_pub import publish_to_vk
from bot.keyboards import (
    approval_kb, postpone_kb, reaction_kb, plan_kb, onboarding_done_kb,
)

logger = logging.getLogger(__name__)
router = Router()

ADMIN_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "0"))


class RewriteState(StatesGroup):
    waiting_note = State()


class OnboardingState(StatesGroup):
    answering = State()


# ── /start ────────────────────────────────────────────────────────────────────

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    if not await is_onboarding_done():
        await state.set_state(OnboardingState.answering)
        await state.update_data(step=0)
        await message.answer(
            "👋 Привет! Я твой SMM-агент.\n\n"
            "Сначала познакомимся — я задам 7 коротких вопросов, "
            "чтобы научиться писать в твоём стиле.\n\n"
            + ONBOARDING_QUESTIONS[0]
        )
    else:
        await message.answer(
            "👋 Привет! Я готов к работе.\n\n"
            "Команды:\n"
            "/plan — план на неделю\n"
            "/queue — посты на согласование\n"
            "/voice — твой стиль\n"
            "/competitors — управление конкурентами\n"
            "/settings — настройки"
        )


@router.message(OnboardingState.answering)
async def onboarding_answer(message: Message, state: FSMContext):
    data = await state.get_data()
    step = data.get("step", 0)

    await save_answer(step, message.text)

    if step < len(ONBOARDING_QUESTIONS) - 1:
        await state.update_data(step=step + 1)
        await message.answer(ONBOARDING_QUESTIONS[step + 1])
    else:
        await message.answer("⏳ Анализирую твои ответы и создаю профиль стиля...")
        try:
            prompt = await finish_onboarding()
        except Exception as e:
            await state.clear()
            await message.answer(f"❌ Ошибка при создании профиля: {e}")
            return
        await state.clear()
        await message.answer(
            f"✅ Готово! Вот твой профиль стиля:\n\n"
            f"<code>{prompt[:800]}</code>\n\n"
            "Теперь напиши /plan чтобы сгенерировать план на неделю.",
            parse_mode="HTML",
            reply_markup=onboarding_done_kb(),
        )


@router.callback_query(F.data == "onboarding:confirm")
async def onboarding_confirm(callback: CallbackQuery):
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Отлично! Напиши /plan для генерации первого плана.")


# ── /plan ─────────────────────────────────────────────────────────────────────

@router.message(Command("plan"))
async def cmd_plan(message: Message, state: FSMContext):
    await message.answer("⏳ Генерирую план на неделю...")
    plan = await generate_weekly_plan()
    text = format_plan_message(plan)
    await state.update_data(current_plan=plan)
    await message.answer(text, parse_mode="HTML", reply_markup=plan_kb())


@router.callback_query(F.data == "plan:accept")
async def plan_accept(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    plan = data.get("current_plan", [])

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("✅ План принят! Генерирую посты...")

    for item in plan:
        tg, vk = await generate_post(
            topic=item["topic"],
            post_type=item["type"],
            angle=item.get("angle", ""),
        )
        post_id = await create_post(
            topic=item["topic"],
            post_type=item["type"],
            content_telegram=tg,
            content_vk=vk,
        )
        await add_topic_used(item["topic"], item.get("angle", ""))
        await _send_post_for_approval(callback.message.bot, post_id, tg, item)

    await callback.message.answer("📬 Все посты отправлены на согласование.")


@router.callback_query(F.data == "plan:regen")
async def plan_regen(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("🔄 Перегенерирую...")
    plan = await generate_weekly_plan()
    text = format_plan_message(plan)
    await state.update_data(current_plan=plan)
    await callback.message.answer(text, parse_mode="HTML", reply_markup=plan_kb())


# ── /queue ────────────────────────────────────────────────────────────────────

@router.message(Command("queue"))
async def cmd_queue(message: Message):
    posts = await get_pending_posts()
    if not posts:
        await message.answer("📭 Нет постов на согласование.")
        return
    for post in posts[:5]:
        await _send_post_for_approval(message.bot, post["id"], post["content_telegram"], post)


# ── Кнопки согласования ───────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("approve:"))
async def approve_post(callback: CallbackQuery):
    if os.getenv("LEGACY_DIRECT_PUBLISH_ENABLED", "false").lower() != "true":
        await callback.answer("Прямая публикация отключена. Используй production approval.", show_alert=True)
        return
    post_id = int(callback.data.split(":")[1])
    post = await get_post(post_id)
    if not post:
        await callback.answer("Пост не найден.")
        return

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("📤 Публикую...")

    try:
        tg_msg_id = await publish_to_telegram(callback.message.bot, post["content_telegram"])
        vk_post_id = await publish_to_vk(post["content_vk"])
        await update_post_status(
            post_id, "published",
            telegram_message_id=tg_msg_id,
            vk_post_id=vk_post_id,
        )
        await callback.message.answer(
            f"✅ Пост опубликован в Telegram и VK!\n\n"
            f"Через 2 часа оцени результат:",
            reply_markup=reaction_kb(post_id),
        )
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка публикации: {e}")


@router.callback_query(F.data.startswith("reject:"))
async def reject_post(callback: CallbackQuery):
    post_id = int(callback.data.split(":")[1])
    await update_post_status(post_id, "rejected")
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("🗑 Пост отклонён.")
    await callback.message.answer(f"Пост #{post_id} отклонён.")


@router.callback_query(F.data.startswith("postpone:"))
async def postpone_post(callback: CallbackQuery):
    post_id = int(callback.data.split(":")[1])
    await callback.message.edit_reply_markup(reply_markup=postpone_kb(post_id))
    await callback.answer()


@router.callback_query(F.data.startswith("postpone_date:"))
async def postpone_date(callback: CallbackQuery):
    _, post_id, date_str = callback.data.split(":")
    from datetime import datetime
    scheduled = datetime.strptime(date_str, "%Y-%m-%d")
    await update_post_status(int(post_id), "postponed", scheduled_at=scheduled)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer(f"📅 Перенесено на {date_str}")


@router.callback_query(F.data.startswith("rewrite:"))
async def rewrite_post_cb(callback: CallbackQuery, state: FSMContext):
    post_id = int(callback.data.split(":")[1])
    post = await get_post(post_id)
    if not post:
        await callback.answer("Пост не найден.")
        return

    rewrite_count = post.get("reaction") or 0
    if str(rewrite_count).isdigit() and int(rewrite_count) >= 2:
        await callback.answer()
        await callback.message.answer(
            "✋ Уже 2 итерации переделки. Напиши текст сам и отправь мне — я оформлю под платформы."
        )
        return

    await state.set_state(RewriteState.waiting_note)
    await state.update_data(post_id=post_id, rewrite_count=int(rewrite_count or 0) + 1)
    await callback.answer()
    await callback.message.answer("✏️ Что исправить? Напиши одним сообщением.")


@router.message(RewriteState.waiting_note)
async def rewrite_note_received(message: Message, state: FSMContext):
    data = await state.get_data()
    post_id = data["post_id"]
    post = await get_post(post_id)

    await message.answer("⏳ Переделываю...")
    tg, vk = await rewrite_post(
        post_id=post_id,
        original_tg=post["content_telegram"],
        original_vk=post["content_vk"],
        note=message.text,
    )
    await update_post_status(
        post_id, "pending",
        content_telegram=tg,
        content_vk=vk,
        reaction=str(data["rewrite_count"]),
    )
    await state.clear()
    await _send_post_for_approval(message.bot, post_id, tg, post)


# ── Реакция на пост ───────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("reaction:"))
async def post_reaction(callback: CallbackQuery):
    _, reaction, post_id = callback.data.split(":")
    post = await get_post(int(post_id))
    if post:
        await update_brand_voice_from_feedback(post["content_telegram"], reaction)
        await update_post_status(int(post_id), "published", reaction=reaction)
    await callback.message.edit_reply_markup(reply_markup=None)
    label = "👍 Записано как хороший пример стиля!" if reaction == "good" else "👎 Записано как антипример."
    await callback.answer(label)


# ── /voice ────────────────────────────────────────────────────────────────────

@router.message(Command("voice"))
async def cmd_voice(message: Message):
    prompt = await get_brand_voice_prompt()
    await message.answer(
        f"🎭 Твой профиль стиля:\n\n<code>{prompt[:1000]}</code>\n\n"
        "Чтобы изменить — напиши /start для повторного онбординга.",
        parse_mode="HTML",
    )


# ── /competitors ──────────────────────────────────────────────────────────────

@router.message(Command("competitors"))
async def cmd_competitors(message: Message):
    tg = await get_setting_json("telegram_competitors", [])
    vk = await get_setting_json("vk_competitors", [])
    text = (
        "📊 <b>Конкуренты для мониторинга</b>\n\n"
        f"<b>Telegram:</b> {', '.join(tg) or 'нет'}\n"
        f"<b>VK:</b> {', '.join(str(g) for g in vk) or 'нет'}\n\n"
        "Добавить TG: /add_tg @channel\n"
        "Добавить VK: /add_vk GROUP_ID"
    )
    await message.answer(text, parse_mode="HTML")


@router.message(Command("add_tg"))
async def add_tg_competitor(message: Message):
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Использование: /add_tg @channel")
        return
    channel = parts[1]
    competitors = await get_setting_json("telegram_competitors", [])
    if channel not in competitors:
        competitors.append(channel)
        await set_setting_json("telegram_competitors", competitors)
        await message.answer(f"✅ {channel} добавлен в мониторинг.")
    else:
        await message.answer("Уже в списке.")


@router.message(Command("add_vk"))
async def add_vk_competitor(message: Message):
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Использование: /add_vk GROUP_ID")
        return
    group_id = parts[1]
    competitors = await get_setting_json("vk_competitors", [])
    if group_id not in competitors:
        competitors.append(group_id)
        await set_setting_json("vk_competitors", competitors)
        await message.answer(f"✅ VK-группа {group_id} добавлена.")
    else:
        await message.answer("Уже в списке.")


# ── /dzen и /instagram ────────────────────────────────────────────────────────

@router.message(Command("dzen"))
async def cmd_dzen(message: Message):
    posts = await get_pending_posts()
    if not posts:
        await message.answer("Нет готовых постов. Сначала /plan")
        return
    post = posts[0]
    await message.answer(
        f"📝 Текст для Дзена (скопируй и опубликуй вручную):\n\n{post['content_vk']}"
    )


@router.message(Command("instagram"))
async def cmd_instagram(message: Message):
    posts = await get_pending_posts()
    if not posts:
        await message.answer("Нет готовых постов. Сначала /plan")
        return
    post = posts[0]
    await message.answer(
        f"📸 Текст для Instagram (скопируй и опубликуй вручную):\n\n{post['content_telegram']}"
    )


# ── Вспомогательные функции ───────────────────────────────────────────────────

async def _send_post_for_approval(bot: Bot, post_id: int, content: str, post: dict):
    post_type_labels = {
        "polish": "🎓 Польза", "case": "💼 Кейс",
        "personal": "💬 Личное", "trend": "🔥 Тренд", "selling": "💰 Продающий",
    }
    type_label = post_type_labels.get(post.get("post_type", ""), "📝")
    topic = post.get("topic", "")

    header = f"📝 <b>{type_label}</b> | {topic}\n{'─' * 30}\n\n"
    footer = f"\n{'─' * 30}\n📤 Публикация: Telegram + VK"

    await bot.send_message(
        chat_id=ADMIN_ID,
        text=header + content + footer,
        parse_mode="HTML",
        reply_markup=approval_kb(post_id),
    )
