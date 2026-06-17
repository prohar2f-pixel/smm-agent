import os
from openai import AsyncOpenAI
from agent.style_engine import get_brand_voice_prompt

MODEL = os.getenv("OPENROUTER_MODEL", "anthropic/claude-opus-4-8")

client = AsyncOpenAI(
    api_key=os.getenv("OPENROUTER_API_KEY"),
    base_url="https://openrouter.ai/api/v1",
)

POST_TYPES = {
    "polish": "Польза",
    "case": "Кейс",
    "personal": "Личное",
    "trend": "Тренд",
    "selling": "Продающий",
}

TG_REQUIREMENTS = """
ТРЕБОВАНИЯ ДЛЯ TELEGRAM:
- До 1500 знаков
- Хук в первых 1-2 строках (до "..." в превью)
- 3-5 эмодзи уместно по тексту
- Вопрос к аудитории или CTA в конце
- Пиши от первого лица
"""

VK_REQUIREMENTS = """
ТРЕБОВАНИЯ ДЛЯ ВКОНТАКТЕ:
- До 3000 знаков (можно развернуть подробнее)
- Начало без резкого хука — VK читают медленнее
- 1-3 эмодзи, не больше
- CTA в конце
- Пиши от первого лица
"""


async def generate_post(
    topic: str,
    post_type: str,
    angle: str = "",
    rewrite_note: str = "",
) -> tuple[str, str]:
    """Возвращает (content_telegram, content_vk)."""
    brand_voice = await get_brand_voice_prompt()
    type_label = POST_TYPES.get(post_type, post_type)

    base_context = (
        f"Тип поста: {type_label}\n"
        f"Тема: {topic}\n"
        f"Угол подачи: {angle}\n"
    )
    if rewrite_note:
        base_context += f"Правка от автора: {rewrite_note}\n"

    tg_content = await _generate_for_platform(brand_voice, base_context, "Telegram", TG_REQUIREMENTS)
    vk_content = await _generate_for_platform(brand_voice, base_context, "ВКонтакте", VK_REQUIREMENTS)

    return tg_content, vk_content


async def _generate_for_platform(
    brand_voice: str,
    context: str,
    platform: str,
    requirements: str,
) -> str:
    response = await client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": brand_voice},
            {
                "role": "user",
                "content": (
                    f"Платформа: {platform}\n\n"
                    f"{context}\n"
                    f"{requirements}\n\n"
                    "Напиши пост."
                ),
            },
        ],
        max_tokens=1000,
    )
    return response.choices[0].message.content.strip()


async def rewrite_post(
    post_id: int,
    original_tg: str,
    original_vk: str,
    note: str,
) -> tuple[str, str]:
    brand_voice = await get_brand_voice_prompt()

    async def _rewrite(original: str, platform: str, requirements: str) -> str:
        response = await client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": brand_voice},
                {
                    "role": "user",
                    "content": (
                        f"Платформа: {platform}\n\n"
                        f"Оригинальный пост:\n{original}\n\n"
                        f"Правка автора: {note}\n\n"
                        f"{requirements}\n\n"
                        "Перепиши пост с учётом правки."
                    ),
                },
            ],
            max_tokens=1000,
        )
        return response.choices[0].message.content.strip()

    tg = await _rewrite(original_tg, "Telegram", TG_REQUIREMENTS)
    vk = await _rewrite(original_vk, "ВКонтакте", VK_REQUIREMENTS)
    return tg, vk
