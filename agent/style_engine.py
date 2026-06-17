import os
from openai import AsyncOpenAI
from agent.memory import get_all_voice, set_voice, get_setting, set_setting

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
MODEL = os.getenv("OPENROUTER_MODEL", "anthropic/claude-opus-4-8")

client = AsyncOpenAI(
    api_key=OPENROUTER_API_KEY,
    base_url="https://openrouter.ai/api/v1",
)

ONBOARDING_QUESTIONS = [
    "1️⃣ Расскажи о себе в 2-3 предложениях — кто ты и чем занимаешься?",
    "2️⃣ Кто твой идеальный клиент? Опиши его — кто он, что у него болит?",
    "3️⃣ Как ты обычно общаешься с людьми — официально или как с другом?",
    "4️⃣ Что тебя бесит в чужих постах про IT / веб-разработку?",
    "5️⃣ Назови 3 слова, которые точно описывают твой стиль общения.",
    "6️⃣ Что ты никогда не напишешь в своём канале? Запрещённые слова или темы.",
    "7️⃣ Главная цель соцсетей — продавать услуги, строить личный бренд, или оба?",
]


async def is_onboarding_done() -> bool:
    val = await get_setting("onboarding_done")
    return val == "1"


async def get_current_question_index() -> int:
    val = await get_setting("onboarding_step", "0")
    return int(val)


async def save_answer(index: int, answer: str):
    await set_voice(f"answer_{index}", answer)
    await set_setting("onboarding_step", str(index + 1))


async def build_brand_voice_prompt(answers: dict) -> str:
    answers_text = "\n".join(
        f"Вопрос {i+1}: {ONBOARDING_QUESTIONS[i]}\nОтвет: {answers.get(f'answer_{i}', '')}"
        for i in range(7)
    )

    response = await client.chat.completions.create(
        model=MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "Ты помогаешь создать профиль голоса бренда для AI SMM-агента. "
                    "На основе ответов пользователя составь короткий system prompt "
                    "для генерации постов в его стиле. "
                    "Формат: не более 300 слов, конкретно, без воды."
                ),
            },
            {
                "role": "user",
                "content": f"Вот ответы пользователя на вопросы об их стиле:\n\n{answers_text}\n\n"
                           "Составь system prompt для AI-агента, который будет писать посты "
                           "от имени этого человека. Включи: стиль, тон, аудиторию, запреты, CTA.",
            },
        ],
        max_tokens=600,
    )
    return response.choices[0].message.content.strip()


async def finish_onboarding(cta_dm: str = "", cta_site: str = ""):
    answers = await get_all_voice()
    prompt = await build_brand_voice_prompt(answers)
    await set_voice("brand_voice_prompt", prompt)
    if cta_dm:
        await set_voice("cta_dm", cta_dm)
    if cta_site:
        await set_voice("cta_site", cta_site)
    await set_setting("onboarding_done", "1")
    await set_setting("onboarding_step", "0")
    return prompt


async def get_brand_voice_prompt() -> str:
    voice = await get_all_voice()
    prompt = voice.get("brand_voice_prompt", "")
    cta_dm = voice.get("cta_dm", "")
    cta_site = voice.get("cta_site", "")

    extras = []
    if cta_dm:
        extras.append(f"CTA для продающих постов: {cta_dm}")
    if cta_site:
        extras.append(f"CTA для экспертных постов: {cta_site}")

    if extras:
        prompt += "\n\n" + "\n".join(extras)
    return prompt


async def update_brand_voice_from_feedback(post_content: str, reaction: str):
    voice = await get_all_voice()
    key = "good_examples" if reaction == "good" else "bad_examples"
    existing = voice.get(key, "")
    examples = [e for e in existing.split("---") if e.strip()]

    examples.append(post_content[:500])
    examples = examples[-10:]  # хранить только последние 10

    await set_voice(key, "---".join(examples))
