import os
import unittest
from unittest.mock import patch


class OpenRouterClientTests(unittest.TestCase):
    def test_missing_proxy_fails_closed(self):
        from openrouter_client import create_async_openai

        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "OPENROUTER_HTTP_PROXY"):
                create_async_openai(client_factory=lambda **kwargs: kwargs)

    def test_non_loopback_proxy_fails_closed(self):
        from openrouter_client import create_async_openai

        with patch.dict(
            os.environ,
            {"OPENROUTER_HTTP_PROXY": "http://proxy.example:10809"},
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "loopback"):
                create_async_openai(client_factory=lambda **kwargs: kwargs)

    def test_loopback_proxy_is_passed_to_openai_http_client(self):
        from openrouter_client import create_async_openai

        calls = {}

        def http_client_factory(**kwargs):
            calls["http"] = kwargs
            return "proxied-http-client"

        def client_factory(**kwargs):
            calls["openai"] = kwargs
            return kwargs

        with patch.dict(
            os.environ,
            {
                "OPENROUTER_HTTP_PROXY": "http://127.0.0.1:10809",
                "OPENROUTER_API_KEY": "test-key",
            },
            clear=True,
        ):
            create_async_openai(
                client_factory=client_factory,
                http_client_factory=http_client_factory,
                timeout=60.0,
            )

        self.assertEqual(
            calls["http"], {"proxy": "http://127.0.0.1:10809"}
        )
        self.assertEqual(calls["openai"]["http_client"], "proxied-http-client")
        self.assertEqual(calls["openai"]["api_key"], "test-key")
        self.assertEqual(calls["openai"]["timeout"], 60.0)


if __name__ == "__main__":
    unittest.main()
