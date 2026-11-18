"""Offline tests of invitations, registry, receipt safeguards and GPT intent."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bot"))

from vesya_tools.gpt.registry import GPTRegistry
from vesya_tools.gpt.receipts import analyze_receipt
from vesya_tools.gpt.handler import parse_intent


class InviteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.reg = GPTRegistry(Path(self.temp.name) / "gpt.sqlite3")

    def test_code_bound_one_time_and_atomic(self):
        self.reg.register(111)
        self.reg.register(222)
        code = self.reg.issue_free_code(111, 999)
        self.assertTrue(code.startswith("GPT-"))
        self.assertFalse(self.reg.redeem_free_code(222, code))
        self.assertTrue(self.reg.redeem_free_code(111, code))
        self.assertFalse(self.reg.redeem_free_code(111, code))
        self.assertEqual(self.reg.get(111)["stage"], "provisioning")

    def test_renew_code_revokes_prior(self):
        self.reg.register(111)
        first = self.reg.issue_free_code(111, 999)
        second = self.reg.issue_free_code(111, 999)
        self.assertNotEqual(first, second)
        self.assertFalse(self.reg.redeem_free_code(111, first))
        self.assertTrue(self.reg.redeem_free_code(111, second))

    def test_registration_is_idempotent(self):
        first, created = self.reg.register(111)
        self.assertTrue(created)
        second, created = self.reg.register(111)
        self.assertFalse(created)
        self.assertEqual(first["requested_at"], second["requested_at"])

    def test_duplicate_receipt_hash_and_txn(self):
        self.reg.register(111)
        self.reg.register(222)
        self.reg.set_stage(111, "awaiting_receipt")
        self.reg.set_stage(222, "awaiting_receipt")
        kw = dict(file_id="tgA", kind="document", sha256="sha1", txn="txn1",
                  bank="Bank",date="2026-11-17", amount=2990,recipient="recipient",accepted=True)
        self.assertTrue(self.reg.record_receipt(111, **kw))
        self.assertFalse(self.reg.record_receipt(222, **kw))
        self.assertFalse(self.reg.record_receipt(222, **{**kw,"sha256":"sha2"}))
        self.assertTrue(self.reg.record_receipt(222, **{**kw,"sha256":"sha2","txn":"txn2"}))

    def test_manual_receipt_approval(self):
        self.reg.register(111)
        self.reg.set_stage(111, "awaiting_receipt")
        self.assertTrue(self.reg.record_receipt(
            111,file_id="f",kind="photo",sha256="sha",txn=None,bank="",
            date="",amount=2990,recipient="",accepted=False,
        ))
        self.assertTrue(self.reg.mark_receipt_review_approved(111))
        self.assertFalse(self.reg.mark_receipt_review_approved(111))
        self.assertEqual(self.reg.get(111)["stage"], "receipt_review")
        self.assertFalse(self.reg.get(111)["payment_confirmed"])


class ReceiptRulesTests(unittest.TestCase):
    def test_no_payee_blocks_auto_activation(self):
        with patch.dict(os.environ, {"GPT_PAYMENT_RECIPIENT": ""}):
            accepted, key, amount = analyze_receipt({
                "amount_rub":2990,"bank":"Bank","transaction_id":"123ABC",
                "recipient":"Someone","completed":True
            })
        self.assertFalse(accepted)
        self.assertEqual(amount,2990)
        self.assertIsNotNone(key)

    def test_matching_payee_and_transaction_can_accept(self):
        with patch.dict(os.environ, {"GPT_PAYMENT_RECIPIENT": "Иванов И.И."}):
            accepted, key, amount = analyze_receipt({
                "amount_rub":2990,"bank":"Bank","transaction_id":"123ABC",
                "recipient":"Иванов И.И.","completed":True
            })
        self.assertTrue(accepted)
        self.assertEqual(amount,2990)

    def test_missing_transaction_blocks_auto_activation(self):
        with patch.dict(os.environ, {"GPT_PAYMENT_RECIPIENT": "Иванов"}):
            accepted, _, _ = analyze_receipt({
                "amount_rub":2990,"bank":"Bank","transaction_id":"",
                "recipient":"Иванов","completed":True
            })
        self.assertFalse(accepted)

    def test_not_completed_blocks_auto_activation(self):
        with patch.dict(os.environ, {"GPT_PAYMENT_RECIPIENT": "Иванов"}):
            accepted, _, _ = analyze_receipt({
                "amount_rub":2990,"bank":"Bank","transaction_id":"123",
                "recipient":"Иванов","completed":False
            })
        self.assertFalse(accepted)


class IntentTests(unittest.TestCase):
    def test_explicit_gpt_and_redeem(self):
        self.assertEqual(parse_intent("/gpt"), "register")
        self.assertEqual(parse_intent("Веся, подключи мне GPT"), "register")
        self.assertEqual(parse_intent("GPT-0123456789AB-ABCDEF012345"), "redeem")
        self.assertEqual(parse_intent("/gpt_code 123456"), "admin")
        self.assertIsNone(parse_intent("/proxy"))
        self.assertIsNone(parse_intent("Покажи погоду на завтра"))


if __name__ == "__main__":
    unittest.main()
