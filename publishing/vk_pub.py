import os
from typing import Callable

from publishing.base import PublishRequest, PublishResult


VK_TOKEN = os.getenv("VK_TOKEN")
VK_GROUP_ID = os.getenv("VK_GROUP_ID")


def _get_vk():
    import vk_api
    session = vk_api.VkApi(token=VK_TOKEN)
    return session.get_api()


class VkPublisherAdapter:
    """VK boundary fed only with bytes from verified open handles."""

    platform = "vk"
    provider_key = "vk-api"

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


def publish_to_vk(text: str) -> int:
    """Legacy direct publisher. New production flow uses PublishService guards."""
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
