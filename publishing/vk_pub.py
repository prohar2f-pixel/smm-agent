import os
import io
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


class _NamedBytesIO(io.BytesIO):
    def __init__(self, data: bytes, name: str):
        super().__init__(data)
        self.name = name


class VkApiTransport:
    """Concrete VK wall transport with provider random_id idempotency."""

    def __init__(self, token: str):
        import vk_api
        self.session = vk_api.VkApi(token=token)
        self.api = self.session.get_api()
        self.upload = vk_api.VkUpload(self.session)

    def __call__(self, **payload) -> dict:
        group_id = abs(int(str(payload["target_ref"]).removeprefix("-")))
        attachments = []
        for index, asset in enumerate(payload["assets"]):
            mime = asset["mime_type"]
            extension = mime.split("/")[-1].replace("jpeg", "jpg")
            stream = _NamedBytesIO(asset["bytes"], f"asset-{index}.{extension}")
            if mime.startswith("video/"):
                result = self.upload.video(
                    video_file=stream, name="Content Factory", group_id=group_id,
                )
                owner = result.get("owner_id", -group_id)
                video_id = result.get("video_id") or result.get("id")
                attachments.append(f"video{owner}_{video_id}")
            elif mime.startswith("image/"):
                photos = self.upload.photo_wall([stream], group_id=group_id)
                attachments.extend(
                    f"photo{photo['owner_id']}_{photo['id']}" for photo in photos
                )
            else:
                raise ValueError(f"VK adapter does not support {mime}")
        response = self.api.wall.post(
            owner_id=-group_id, from_group=1, message=payload["caption_text"],
            attachments=",".join(attachments),
            random_id=int(payload["idempotency_key"][:8], 16),
        )
        post_id = str(response["post_id"])
        return {"external_id": post_id,
                "evidence_ref": f"https://vk.com/wall-{group_id}_{post_id}"}

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
