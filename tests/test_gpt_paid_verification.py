"""GPT payment state regression: never issue paid access on receipt OCR alone."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bot.vesya_tools.gpt.registry import GPTRegistry


class PaidReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = GPTRegistry(Path(self.temp.name) / "gpt.sqlite")
        self.uid = 123456
        self.db.register(self.uid)

    def receipt(self, accepted=True):
        return self.db.record_receipt(
            self.uid, file_id="tg-ref-1", kind="document",
            sha256="e" * 64, txn="bank-operation-1",
            bank="T-Bank", date="2026-11-17", amount=2990,
            recipient="Test", accepted=accepted,
        )

    def test_matching_receipt_does_not_authorize_access(self):
        self.assertTrue(self.receipt(accepted=True))
        app = self.db.get(self.uid)
        self.assertEqual(app["stage"], "receipt_review")
        self.assertEqual(app["receipt_review"], "accepted")
        self.assertEqual(app["payment_confirmed"], 0)
        self.assertTrue(self.db.mark_payment(self.uid))
        self.assertEqual(self.db.get(self.uid)["stage"], "provisioning")
        self.assertEqual(self.db.get(self.uid)["payment_confirmed"], 1)
        self.assertFalse(self.db.mark_payment(self.uid))

    def test_suspicious_receipt_requires_two_owner_actions(self):
        self.assertTrue(self.receipt(accepted=False))
        self.assertFalse(self.db.mark_payment(self.uid))
        self.assertEqual(self.db.get(self.uid)["stage"], "receipt_review")
        self.assertTrue(self.db.mark_receipt_review_approved(self.uid))
        self.assertEqual(self.db.get(self.uid)["stage"], "receipt_review")
        self.assertEqual(self.db.get(self.uid)["payment_confirmed"], 0)
        self.assertTrue(self.db.mark_payment(self.uid))
        self.assertEqual(self.db.get(self.uid)["stage"], "provisioning")

    def test_no_receipt_cannot_be_confirmed(self):
        self.assertFalse(self.db.mark_payment(self.uid))
        self.assertFalse(self.db.mark_receipt_review_approved(self.uid))
        self.assertFalse(self.db.mark_payment(self.uid, tax=True))

    def test_npd_recording_is_separate_from_bank_confirmation(self):
        self.assertTrue(self.receipt(accepted=True))
        self.assertFalse(self.db.mark_payment(self.uid, tax=True))
        self.assertTrue(self.db.mark_payment(self.uid))
        self.assertTrue(self.db.mark_payment(self.uid, tax=True))
        self.assertFalse(self.db.mark_payment(self.uid, tax=True))
        self.assertEqual(self.db.get(self.uid)["npd_receipt_issued"], 1)


if __name__ == "__main__":
    unittest.main()
