import os
from agent.memory import get_recent_topics, get_recent_signals
from agent.style_engine import get_brand_voice_prompt
from openrouter_client import create_async_openai

MODEL = os.getenv("OPENROUTER_MODEL", "anthropic/claude-opus-4-8")

client = create_async_openai()

WEEK_STRUCTURE = """
Структура недели (5 постов):
- ПН: Польза (тип: polish) — туториал, совет, объяснение
- ВТ: Кейс (тип: case) — реальный пример из практики
- СР: Личное (тип: personal) — мнение, история, наблюдение
- ЧТ: Тренд (тип: trend) — реакция на новость или тренд из ниши
- ПТ: Продающий (тип: selling) — услуга, портфолио, CTA на контакт
"""


async def generate_weekly_plan() -> list[dict]:
    """Возвращает список из 5 постов: {day, type, topic, angle}"""
    brand_voice = await get_brand_voice_prompt()
    used_topics = await get_recent_topics(60)
    signals = await get_recent_signals(20)

    used_text = "\n".join(f"- {t}" for t in used_topics) if used_topics else "Нет"
    signals_text = "\n".join(
        f"- [{s['source_type']}] {s['content_summary']}" for s in signals
    ) if signals else "Нет свежих сигналов"

    response = await client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": brand_voice},
            {
                "role": "user",
                "content": (
                    f"{WEEK_STRUCTURE}\n\n"
                    f"Темы, которые уже были (не повторяй):\n{used_text}\n\n"
                    f"Свежие сигналы из мониторинга:\n{signals_text}\n\n"
                    "Составь контент-план на неделю: 5 постов по структуре выше.\n"
                    "Ответь строго в формате JSON-массива:\n"
                    '[\n'
                    '  {"day": "ПН", "type": "polish", "topic": "...", "angle": "..."},\n'
                    '  ...\n'
                    ']\n'
                    "Только JSON, без пояснений."
                ),
            },
        ],
        max_tokens=800,
    )

    import json
    text = response.choices[0].message.content.strip()
    # убираем markdown-обёртку если есть
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


def format_plan_message(plan: list[dict]) -> str:
    day_emoji = {
        "ПН": "📅", "ВТ": "📅", "СР": "📅", "ЧТ": "📅", "ПТ": "📅"
    }
    type_emoji = {
        "polish": "🎓 Польза",
        "case": "💼 Кейс",
        "personal": "💬 Личное",
        "trend": "🔥 Тренд",
        "selling": "💰 Продающий",
    }

    lines = ["📋 <b>ПЛАН НА НЕДЕЛЮ</b>\n"]
    for item in plan:
        day = item.get("day", "")
        type_label = type_emoji.get(item.get("type", ""), item.get("type", ""))
        topic = item.get("topic", "")
        angle = item.get("angle", "")
        lines.append(f"<b>{day}</b> — {type_label}")
        lines.append(f'"{topic}"')
        if angle:
            lines.append(f"<i>{angle}</i>")
        lines.append("")

    return "\n".join(lines)
