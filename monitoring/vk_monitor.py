import os
import vk_api
from agent.memory import add_signal, get_setting_json

VK_TOKEN = os.getenv("VK_TOKEN")


async def monitor_vk_competitors():
    competitors = await get_setting_json("vk_competitors", [])
    if not competitors or not VK_TOKEN:
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
                    await add_signal(
                        source=str(group_id),
                        source_type="vk",
                        summary=summary,
                        signal_type="topic",
                    )
        except Exception:
            pass
