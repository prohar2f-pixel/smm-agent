import os
import vk_api
from agent.memory import add_signal, get_setting_json

VK_TOKEN = os.getenv("VK_TOKEN")


def monitor_vk_competitors():
    competitors = None

    import asyncio
    loop = asyncio.new_event_loop()
    competitors = loop.run_until_complete(get_setting_json("vk_competitors", []))
    loop.close()

    if not competitors:
        return

    session = vk_api.VkApi(token=VK_TOKEN)
    vk = session.get_api()

    for group_id in competitors:
        try:
            result = vk.wall.get(owner_id=f"-{group_id}", count=20)
            for post in result.get("items", []):
                text = post.get("text", "").strip()
                if len(text) > 50:
                    summary = text[:200].replace("\n", " ")

                    async def _save():
                        await add_signal(
                            source=str(group_id),
                            source_type="vk",
                            summary=summary,
                            signal_type="topic",
                        )

                    import asyncio
                    asyncio.get_event_loop().run_until_complete(_save())
        except Exception:
            pass
