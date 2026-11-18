"""End-to-end offline test: first GPT issuance is escrowed before repeats."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"bot"))
from vesya_tools.gpt import handler
from vesya_tools.gpt.registry import GPTRegistry

LOGIN="gpt_0123456789abcdef"
PASSWORD="original-password-shown-again"


class GPTFirstIssueTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_issue_and_repeat_share_unmodified_password(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg=GPTRegistry(Path(tmp)/"gpt.sqlite")
            reg.register(111)
            invitation=reg.issue_free_code(111,999)
            self.assertTrue(reg.redeem_free_code(111,invitation))
            msg=SimpleNamespace(
                from_user=SimpleNamespace(id=111),
                chat=SimpleNamespace(type="private"),
                answer=AsyncMock(),
                bot=SimpleNamespace(send_message=AsyncMock()),
            )
            client=SimpleNamespace(
                create_user=AsyncMock(return_value={
                    "status":"active","username":LOGIN,"initial_password":PASSWORD,
                }),
                get_user_status=AsyncMock(return_value={
                    "status":"active","username":LOGIN,
                }),
                rotate_password=AsyncMock(),
            )
            with patch.dict(os.environ,{
                "GPT_AGENT_API_TOKEN":"fixture-"+("k"*65),
                "GPT_INSTALLER_URL":"https://example.com/GPT.exe",
            }), patch.object(handler,"registry",return_value=reg), \
                 patch.object(handler.GPTAgentConfig,"from_env",return_value=object()), \
                 patch.object(handler,"GPTAgentClient",return_value=client), \
                 patch.object(handler,"_notify_owner",new_callable=AsyncMock):
                await handler._provision(msg,111)
                self.assertEqual(reg.get(111)["stage"],"active")
                self.assertEqual(reg.get_credential(111),(LOGIN,PASSWORD))
                first=msg.answer.await_args.args[0]
                await handler._register(msg)
                repeated=msg.answer.await_args.args[0]
                await handler._register(msg)
                again=msg.answer.await_args.args[0]
            for content in (first,repeated,again):
                self.assertIn(PASSWORD,content)
                self.assertIn(LOGIN,content)
                self.assertIn("https://example.com/GPT.exe",content)
            self.assertEqual(repeated,again)
            client.create_user.assert_awaited_once_with(111)
            client.rotate_password.assert_not_awaited()


if __name__=="__main__":
    unittest.main()
