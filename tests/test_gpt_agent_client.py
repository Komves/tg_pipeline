"""Offline client tests. No traffic reaches production."""
import asyncio
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

import httpx

SPEC = importlib.util.spec_from_file_location(
    "gpt_agent_client", Path(__file__).resolve().parents[1] / "bot" / "vesya_tools" / "gpt" / "client.py"
)
import sys
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


class ConfigTests(unittest.TestCase):
    def test_secure_scheme_and_port_required(self):
        for url in ("http://example.test:4456", "https://example.test", "https://user:pass@example.test:4456", "https://example.test:4456/path"):
            with self.assertRaises(ValueError):
                module.GPTAgentConfig(url, "x"*32, "/ca", "/cert", "/key")
        module.GPTAgentConfig("https://example.test:4456", "x"*32, "/ca", "/cert", "/key")


class RequestTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = module.GPTAgentConfig("https://example.test:4456", "x"*40, "/ca", "/cert", "/key")

    async def test_create_response_and_no_proxy_env(self):
        collected = {}
        class MockAsyncClient:
            def __init__(self, **kwargs):
                collected.update(kwargs)
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def post(self, url, headers, json):
                collected.update({"url":url,"headers":headers,"payload":json})
                return httpx.Response(200, json={"status":"active","username":"gpt_123","initial_password":"mock_value"})
        client = module.GPTAgentClient(self.config)
        client._tls_context = lambda: object()
        with patch.object(module.httpx, "AsyncClient", MockAsyncClient):
            response = await client.create_user(123)
        self.assertEqual(response["username"], "gpt_123")
        self.assertFalse(collected["trust_env"])
        self.assertFalse(collected["follow_redirects"])
        self.assertEqual(collected["payload"], {"telegram_id":123})
        self.assertEqual(collected["url"], "https://example.test:4456/v1/create_user")

    async def test_rejected_response_is_sanitized(self):
        class MockAsyncClient:
            def __init__(self, **kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def post(self, *args, **kwargs):
                return httpx.Response(401, text="private token invalid ABC")
        client = module.GPTAgentClient(self.config)
        client._tls_context = lambda: object()
        with patch.object(module.httpx, "AsyncClient", MockAsyncClient):
            with self.assertRaises(module.GPTAgentError) as cm:
                await client.get_service_status()
        self.assertNotIn("private token", str(cm.exception))

    async def test_invalid_actions_or_ids_do_not_connect(self):
        client = module.GPTAgentClient(self.config)
        with self.assertRaises(ValueError):
            await client.call("run_shell", telegram_id=1)
        with self.assertRaises(ValueError):
            await client.create_user(True)


if __name__ == "__main__":
    unittest.main()
