import os
from urllib.parse import urlsplit


OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def _require_loopback_proxy() -> str:
    proxy = os.getenv("OPENROUTER_HTTP_PROXY", "")
    parsed = urlsplit(proxy)
    if not proxy:
        raise RuntimeError("OPENROUTER_HTTP_PROXY is required")
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.port != 10809
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError(
            "OPENROUTER_HTTP_PROXY must be the approved loopback proxy "
            "http://127.0.0.1:10809"
        )
    return proxy


def create_async_openai(
    *,
    timeout=None,
    client_factory=None,
    http_client_factory=None,
):
    proxy = _require_loopback_proxy()
    if client_factory is None or http_client_factory is None:
        from openai import AsyncOpenAI, DefaultAsyncHttpxClient

        client_factory = client_factory or AsyncOpenAI
        http_client_factory = http_client_factory or DefaultAsyncHttpxClient

    kwargs = {
        "api_key": os.getenv("OPENROUTER_API_KEY"),
        "base_url": OPENROUTER_BASE_URL,
        "http_client": http_client_factory(proxy=proxy),
    }
    if timeout is not None:
        kwargs["timeout"] = timeout
    return client_factory(**kwargs)
