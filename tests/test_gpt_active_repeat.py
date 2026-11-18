"""Regression tests for existing GPT customers requesting setup again."""
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bot"))
from vesya_tools.gpt import handler


def app(stage="active", login="gpt_0123456789abcdef"):
    return {"stage": stage, "proxy_username": login, "method": "free"}


def message(uid=101):
    return SimpleNamespace(from_user=SimpleNamespace(id=uid), chat=SimpleNamespace(type="private"), answer=AsyncMock())


def callback(action, uid=101):
    return SimpleNamespace(
        data=action,
        from_user=SimpleNamespace(id=uid),
        message=SimpleNamespace(chat=SimpleNamespace(type="private"), answer=AsyncMock()),
        answer=AsyncMock(),
    )


class PasswordIntentTests(unittest.TestCase):
    def test_natural_language_password_request_is_not_sent_to_llm(self):
        for prompt in (
            "Покажи пароль gpt", "Веся, покажи пароль GPT",
            "Дай мне пароль чатгпт", "Я забыл пароль GPT",
            "Где мой пароль GPT?", "Как восстановить пароль GPT?",
            "/gpt_password",
        ):
            with self.subTest(prompt=prompt):
                self.assertEqual(handler.parse_intent(prompt), "password_help")

    def test_general_password_mention_not_hijacked(self):
        self.assertIsNone(handler.parse_intent("Покажи пароль от Wi-Fi"))
        self.assertEqual(handler.parse_intent("Подключи GPT"), "register")


class ExistingCustomerTests(unittest.IsolatedAsyncioTestCase):
    async def test_password_help_requires_registered_active_user(self):
        msg = message()
        with patch.object(handler, "registry", return_value=SimpleNamespace(get=lambda uid: None)):
            handled = await handler.handle_gpt_message(msg, "Покажи пароль GPT")
        self.assertTrue(handled)
        self.assertIn("ещё не оформлен", msg.answer.await_args.args[0])

    async def test_password_help_existing_user_reissues_same_password_without_rotation(self):
        msg = message()
        reg = SimpleNamespace(get=lambda uid: app(),
                              get_credential=lambda uid: ("gpt_0123456789abcdef", "same-password-forever"))
        client = SimpleNamespace(
            get_user_status=AsyncMock(return_value={"status": "active", "username": "gpt_0123456789abcdef"}),
            rotate_password=AsyncMock(), create_user=AsyncMock(),
        )
        with patch.object(handler, "registry", return_value=reg), \
             patch.object(handler.GPTAgentConfig, "from_env", return_value=object()), \
             patch.object(handler, "GPTAgentClient", return_value=client):
            handled = await handler.handle_gpt_message(msg, "Веся, покажи пароль GPT")
        self.assertTrue(handled)
        answer = msg.answer.await_args.args[0]
        self.assertIn("same-password-forever", answer)
        self.assertIn("gpt_0123456789abcdef", answer)
        client.rotate_password.assert_not_awaited()
        client.create_user.assert_not_awaited()

    async def test_existing_customer_gets_same_password_and_installer_twice(self):
        msg = message()
        reg = SimpleNamespace(
            register=lambda uid: (app(), False),
            get_credential=lambda uid: ("gpt_0123456789abcdef", "same-password-forever"),
        )
        client = SimpleNamespace(
            get_user_status=AsyncMock(return_value={"status": "active", "username": "gpt_0123456789abcdef"}),
            rotate_password=AsyncMock(),
        )
        url = "https://github.com/Komves/tg_pipeline/releases/download/gpt-windows-v1/GPT.exe"
        with patch.object(handler, "registry", return_value=reg), \
             patch.object(handler.GPTAgentConfig, "from_env", return_value=object()), \
             patch.object(handler, "GPTAgentClient", return_value=client), \
             patch.dict(os.environ, {"GPT_INSTALLER_URL": url}):
            await handler._register(msg)
            await handler._register(msg)
        first = msg.answer.await_args_list[0].args[0]
        second = msg.answer.await_args_list[1].args[0]
        self.assertEqual(first, second)
        self.assertIn("same-password-forever", first)
        self.assertIn("повторная оплата не нужна", first)
        self.assertIn(url, first)
        self.assertIn("gpt_0123456789abcdef", first)
        client.rotate_password.assert_not_awaited()
        markup = msg.answer.await_args.kwargs["reply_markup"]
        self.assertEqual(markup.inline_keyboard[0][0].url, url)

    async def test_preexisting_user_without_escrow_requires_one_explicit_reset(self):
        msg = message()
        reg = SimpleNamespace(register=lambda uid: (app(), False),
                              get_credential=lambda uid: None)
        with patch.object(handler, "registry", return_value=reg):
            await handler._register(msg)
        reply = msg.answer.await_args.args[0]
        self.assertIn("создан до внедрения", reply)
        self.assertIn("Один раз нажми", reply)
        self.assertEqual(msg.answer.await_args.kwargs["reply_markup"].inline_keyboard[-1][0].callback_data, "gpt:reset")

    async def test_missing_existing_login_cannot_reset(self):
        msg = message()
        registry = SimpleNamespace(register=lambda uid: (app(login=None), False))
        with patch.object(handler, "registry", return_value=registry):
            await handler._register(msg)
        self.assertIn("обратись к владельцу", msg.answer.await_args.args[0].lower())

    async def test_password_recovery_requires_second_click(self):
        cb = callback("gpt:reset")
        registry = SimpleNamespace(get=lambda uid: app())
        with patch.object(handler, "registry", return_value=registry):
            await handler.handle_gpt_callback(cb)
        self.assertIn("перестанет работать", cb.message.answer.await_args.args[0])
        keyboard = cb.message.answer.await_args.kwargs["reply_markup"]
        self.assertEqual(keyboard.inline_keyboard[0][0].callback_data, "gpt:reset_confirm")

    async def test_disabled_account_is_not_rotated(self):
        cb = callback("gpt:reset_confirm")
        registry = SimpleNamespace(get=lambda uid: app())
        client = SimpleNamespace(
            get_user_status=AsyncMock(return_value={"status": "disabled", "username": "gpt_0123456789abcdef"}),
            rotate_password=AsyncMock(),
        )
        with patch.object(handler, "registry", return_value=registry), patch.object(handler.GPTAgentConfig, "from_env", return_value=object()), patch.object(handler, "GPTAgentClient", return_value=client):
            await handler.handle_gpt_callback(cb)
        client.rotate_password.assert_not_awaited()
        self.assertIn("отключён", cb.message.answer.await_args.args[0])

    async def test_password_rotation_is_for_same_telegram_user_only(self):
        cb = callback("gpt:reset_confirm")
        registry = SimpleNamespace(get=lambda uid: app(), save_credential=lambda uid, username, password: None)
        client = SimpleNamespace(
            get_user_status=AsyncMock(return_value={"status": "active", "username": "gpt_0123456789abcdef"}),
            rotate_password=AsyncMock(return_value={
                "status": "active", "username": "gpt_0123456789abcdef", "initial_password": "temporary-password-only"
            }),
        )
        with patch.object(handler, "registry", return_value=registry), patch.object(handler.GPTAgentConfig, "from_env", return_value=object()), patch.object(handler, "GPTAgentClient", return_value=client):
            await handler.handle_gpt_callback(cb)
        client.rotate_password.assert_awaited_once_with(101)
        self.assertIn("temporary-password-only", cb.message.answer.await_args.args[0])

    async def test_other_user_mismatch_cannot_rotate_password(self):
        cb = callback("gpt:reset_confirm")
        registry = SimpleNamespace(get=lambda uid: app())
        client = SimpleNamespace(
            get_user_status=AsyncMock(return_value={"status": "active", "username": "gpt_ffffffffffffffff"}),
            rotate_password=AsyncMock(),
        )
        with patch.object(handler, "registry", return_value=registry), patch.object(handler.GPTAgentConfig, "from_env", return_value=object()), patch.object(handler, "GPTAgentClient", return_value=client):
            await handler.handle_gpt_callback(cb)
        client.rotate_password.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
