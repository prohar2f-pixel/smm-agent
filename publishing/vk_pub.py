import os
import vk_api


VK_TOKEN = os.getenv("VK_TOKEN")
VK_GROUP_ID = os.getenv("VK_GROUP_ID")


def _get_vk():
    session = vk_api.VkApi(token=VK_TOKEN)
    return session.get_api()


def publish_to_vk(text: str) -> int:
    vk = _get_vk()
    result = vk.wall.post(
        owner_id=f"-{VK_GROUP_ID}",
        message=text,
        from_group=1,
    )
    return result["post_id"]


def delete_from_vk(post_id: int):
    try:
        vk = _get_vk()
        vk.wall.delete(owner_id=f"-{VK_GROUP_ID}", post_id=post_id)
    except Exception:
        pass
