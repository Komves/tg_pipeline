"""Regression tests for Vesya's standalone one-month proxy access."""
import asyncio
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bot"))
sys.path.insert(0, str(ROOT))
from vesya_tools.proxy.handler import handle_proxy_message, parse_proxy_intent, windows_launcher

spec = importlib.util.spec_from_file_location("guest_proxy_agent", ROOT / "ops" / "guest_proxy_agent.py")
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


class IntentTests(unittest.TestCase):
    def test_register_commands(self):
        for txt in ("/proxy", "/start proxy", "Веся, дай мне прокси", "получить прокси"):
            self.assertEqual(parse_proxy_intent(txt), ("register", None))

    def test_admin_commands(self):
        self.assertEqual(parse_proxy_intent("Веся продли прокси 003"), ("renew", 3))
        self.assertEqual(parse_proxy_intent("/proxy_disable 2"), ("disable", 2))
        self.assertEqual(parse_proxy_intent("/proxy_manual"), ("manual", None))
        self.assertEqual(parse_proxy_intent("Веся покажи пользователей прокси"), ("list", None))

    def test_other_chat_unaffected(self):
        for txt in ("Веся новости", "Веся жги", "Что такое SOCKS5?", "как работает прокси?"):
            self.assertIsNone(parse_proxy_intent(txt))

    def test_no_admin_access(self):
        async def run():
            message = SimpleNamespace(
                chat=SimpleNamespace(type="private"),
                from_user=SimpleNamespace(id=20),
                answer=AsyncMock(),
            )
            with patch("vesya_tools.proxy.handler._api", new_callable=AsyncMock) as api:
                self.assertTrue(await handle_proxy_message(message, "/proxy_admin", is_admin=False))
                api.assert_not_awaited()
                self.assertIn("только администратору", message.answer.call_args.args[0])
        asyncio.run(run())


class LauncherTests(unittest.TestCase):
    def test_launcher_opens_chrome_without_powershell_or_password(self):
        content = windows_launcher()
        self.assertTrue(content.startswith(b"@echo off\r\n"))
        self.assertIn(b"start \"\" \"%CHROME%\"", content)
        self.assertIn(b'https://paruslegal.ru:43128', content)
        self.assertIn(b'https://chatgpt.com/', content)
        self.assertIn(b'--user-data-dir=', content)
        self.assertNotIn(b'password', content.lower())
        self.assertNotIn(b'user001', content)
        self.assertNotIn(b'powershell', content.lower())


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.original_db = agent.DB_PATH
        agent.DB_PATH = Path(self.tmp.name) / "users.sqlite3"
        self.addCleanup(lambda: setattr(agent, "DB_PATH", self.original_db))
        with sqlite3.connect(agent.DB_PATH) as db:
            db.execute(
                """CREATE TABLE users (
                   id INTEGER PRIMARY KEY, telegram_id INTEGER UNIQUE,
                   username TEXT UNIQUE NOT NULL, password TEXT NOT NULL,
                   created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
                   disabled INTEGER DEFAULT 0
                )"""
            )

    def test_register_once_and_admin_controls(self):
        first = agent.dispatch("register", {"telegram_id": 112233})
        self.assertEqual(first["id"], 1)
        self.assertEqual(first["status"], "active")
        again = agent.dispatch("register", {"telegram_id": 112233})
        self.assertEqual(again["id"], first["id"])
        self.assertEqual(again["password"], first["password"])
        other = agent.dispatch("register", {"telegram_id": 445566})
        self.assertEqual(other["id"], 2)
        self.assertNotEqual(other["password"], first["password"])
        self.assertEqual(len(agent.dispatch("list", {})["users"]), 2)
        disabled = agent.dispatch("disable", {"id": 1})
        self.assertEqual(disabled["status"], "disabled")
        self.assertNotIn("password", agent.dispatch("register", {"telegram_id": 112233}))
        renewed = agent.dispatch("renew", {"id": 1})
        self.assertEqual(renewed["status"], "active")
        self.assertGreater(renewed["expires_at"], first["expires_at"])

    def test_shareable_download_has_per_user_credentials_and_expiration(self):
        first = agent.dispatch("register", {"telegram_id": 123456})
        self.assertEqual(first["status"], "active")
        self.assertIn("/download/", first["download_url"])
        token = first["download_url"].rsplit("/", 1)[-1]
        self.assertEqual(len(token), 32)
        binary = Path(self.tmp.name) / "client.exe"
        binary.write_bytes(b"MZ" + b"x" * 256)
        with patch.object(agent, "CLIENT_BINARY", binary):
            payload = agent.download_payload(token)
            self.assertIsNotNone(payload)
            self.assertTrue(payload.startswith(b"MZ"))
            cfg = json.loads(payload.split(agent.CLIENT_MAGIC, 1)[1])
            self.assertEqual(cfg["username"], first["username"])
            self.assertEqual(cfg["password"], first["password"])

            refreshed = agent.dispatch("status", {"telegram_id": 123456})
            self.assertNotEqual(refreshed["download_url"], first["download_url"])
            self.assertIsNone(agent.download_payload(token))
            next_token = refreshed["download_url"].rsplit("/", 1)[-1]
            self.assertIsNotNone(agent.download_payload(next_token))
            with sqlite3.connect(agent.DB_PATH) as db:
                db.execute("UPDATE users SET disabled=1 WHERE telegram_id=123456")
            self.assertIsNone(agent.download_payload(next_token))

    def test_link_restricted_to_valid_user(self):
        agent.dispatch("register", {"telegram_id": 777})
        self.assertIsNone(agent.download_payload("a" * 32))

    def test_expired_account_cannot_reregister(self):
        first = agent.dispatch("register", {"telegram_id": 12345})
        with sqlite3.connect(agent.DB_PATH) as db:
            db.execute("UPDATE users SET expires_at=1 WHERE telegram_id=?", (12345,))
        expired = agent.dispatch("register", {"telegram_id": 12345})
        self.assertEqual(expired["status"], "expired")
        self.assertEqual(expired["id"], first["id"])
        self.assertNotIn("password", expired)
        self.assertEqual(len(agent.dispatch("list", {})["users"]), 1)


if __name__ == "__main__":
    unittest.main()
