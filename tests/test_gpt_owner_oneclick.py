"""Single, explicit owner decision after the original payment receipt."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bot"))

from vesya_tools.gpt import handler
from vesya_tools.gpt.registry import GPTRegistry


class OwnerApprovalTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.reg = GPTRegistry(Path(self.tmp.name) / "pay.sqlite")
        self.uid = 123456
        self.reg.register(self.uid)
        self.assertTrue(self.reg.record_receipt(
            self.uid, file_id="telegram-receipt", kind="photo", sha256="a" * 64,
            txn="txn-once", bank="T-Bank", date="2026-11-18", amount=2990,
            recipient="Test", accepted=False,
        ))

    def owner_button(self, uid=None, fingerprint="aaaaaaaaaaaaaaaa", sender=999):
        msg = SimpleNamespace(
            chat=SimpleNamespace(type="private"),
            answer=AsyncMock(),
            edit_reply_markup=AsyncMock(),
            bot=SimpleNamespace(send_message=AsyncMock()),
        )
        return SimpleNamespace(
            data=f"gpt:payapprove:{uid or self.uid}:{fingerprint}",
            from_user=SimpleNamespace(id=sender),
            message=msg, answer=AsyncMock()
        )

    async def test_owner_button_provisions_immediately_and_once(self):
        cb = self.owner_button()
        provision = AsyncMock()
        with patch.object(handler, "registry", return_value=self.reg), \
             patch.object(handler, "owner_id", return_value=999), \
             patch.object(handler, "_provision", provision):
            result = await handler.handle_gpt_callback(cb, is_admin=True)
        self.assertTrue(result)
        self.assertEqual(self.reg.get(self.uid)["stage"], "provisioning")
        self.assertEqual(self.reg.get(self.uid)["payment_confirmed"], 1)
        provision.assert_awaited_once_with(cb.message, self.uid, owner_approved=True)
        cb.message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)
        with patch.object(handler, "registry", return_value=self.reg), \
             patch.object(handler, "owner_id", return_value=999), \
             patch.object(handler, "_provision", provision):
            await handler.handle_gpt_callback(cb, is_admin=True)
        provision.assert_awaited_once()

    async def test_non_owner_cannot_approve(self):
        cb = self.owner_button(sender=321)
        with patch.object(handler, "registry", return_value=self.reg), \
             patch.object(handler, "owner_id", return_value=999):
            await handler.handle_gpt_callback(cb, is_admin=True)
        self.assertEqual(self.reg.get(self.uid)["stage"], "receipt_review")

    async def test_stale_button_cannot_approve_replaced_receipt(self):
        cb = self.owner_button(fingerprint="bbbbbbbbbbbbbbbb")
        with patch.object(handler, "registry", return_value=self.reg), \
             patch.object(handler, "owner_id", return_value=999):
            await handler.handle_gpt_callback(cb, is_admin=True)
        self.assertEqual(self.reg.get(self.uid)["stage"], "receipt_review")

    async def test_owner_approval_does_not_need_second_payment_command(self):
        self.assertTrue(self.reg.mark_payment(self.uid, receipt_prefix="aaaaaaaaaaaaaaaa"))
        self.assertEqual(self.reg.get(self.uid)["stage"], "provisioning")
        self.assertFalse(self.reg.mark_payment(self.uid))
        self.assertTrue(self.reg.mark_payment(self.uid, tax=True))

    async def test_no_receipt_never_approves(self):
        self.reg.register(4567)
        self.assertFalse(self.reg.mark_payment(4567))


if __name__ == "__main__":
    unittest.main()
