from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from datetime import datetime, timedelta


def approval_kb(post_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Опубликовать", callback_data=f"approve:{post_id}"),
        InlineKeyboardButton(text="✏️ Переделать", callback_data=f"rewrite:{post_id}"),
    )
    builder.row(
        InlineKeyboardButton(text="🗑 Отклонить", callback_data=f"reject:{post_id}"),
        InlineKeyboardButton(text="📅 Перенести", callback_data=f"postpone:{post_id}"),
    )
    return builder.as_markup()


def postpone_kb(post_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    today = datetime.now()
    for i in range(1, 8):
        day = today + timedelta(days=i)
        label = day.strftime("%d.%m %a")
        builder.button(text=label, callback_data=f"postpone_date:{post_id}:{day.strftime('%Y-%m-%d')}")
    builder.adjust(3)
    return builder.as_markup()


def reaction_kb(post_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="👍 Мой стиль", callback_data=f"reaction:good:{post_id}"),
        InlineKeyboardButton(text="👎 Не моё", callback_data=f"reaction:bad:{post_id}"),
    )
    return builder.as_markup()


def plan_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Принять план", callback_data="plan:accept"),
        InlineKeyboardButton(text="🔄 Перегенерировать", callback_data="plan:regen"),
    )
    return builder.as_markup()


def onboarding_done_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Всё верно, начинаем!", callback_data="onboarding:confirm")
    builder.button(text="✏️ Изменить", callback_data="onboarding:edit")
    builder.adjust(1)
    return builder.as_markup()
