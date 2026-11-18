"""Encrypted GPT passwords remain stable and are never stored as plaintext."""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.vesya_tools.gpt.registry import GPTRegistry
from bot.vesya_tools.gpt.credential_vault import seal, unseal, VaultError

API_TOKEN = "vault-fixture-" + "x" * 60
LOGIN = "gpt_0123456789abcdef"
PASSWORD = "secure-password-0123456789"


class CredentialEscrowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, {"GPT_AGENT_API_TOKEN": API_TOKEN})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.path = Path(self.tmp.name) / "gpt.sqlite3"
        self.reg = GPTRegistry(self.path)
        self.reg.register(111)
        self.reg.set_stage(111, "provisioning")

    def test_escrow_survives_restart_without_password_rotation(self):
        self.reg.save_credential(111, LOGIN, PASSWORD)
        self.reg.note_provisioned(111, LOGIN)
        fresh = GPTRegistry(self.path)
        self.assertEqual(fresh.get_credential(111), (LOGIN, PASSWORD))
        self.assertEqual(fresh.get_credential(111), (LOGIN, PASSWORD))
        raw = self.path.read_bytes()
        self.assertNotIn(PASSWORD.encode(), raw)
        self.assertNotIn(b"secure-password-0123456789", raw)

    def test_nonowner_cannot_get_someone_elses_password(self):
        self.reg.save_credential(111, LOGIN, PASSWORD)
        self.reg.register(222)
        self.reg.set_stage(222, "provisioning")
        self.assertIsNone(self.reg.get_credential(222))
        with sqlite3.connect(self.path) as db:
            ciphertext = db.execute(
                "SELECT encrypted_password FROM credential_vault WHERE telegram_id=111"
            ).fetchone()[0]
        with self.assertRaises(VaultError):
            unseal(222, ciphertext)

    def test_tamper_detection_and_wrong_key(self):
        secret = seal(111, LOGIN, PASSWORD)
        self.assertEqual(unseal(111, secret), (LOGIN, PASSWORD))
        with self.assertRaises(VaultError):
            unseal(111, secret[:-1] + bytes([secret[-1] ^ 1]))
        with patch.dict(os.environ, {"GPT_AGENT_API_TOKEN": "other-" + "y" * 60}):
            with self.assertRaises(VaultError):
                unseal(111, secret)

    def test_password_rotation_overwrites_ciphertext(self):
        self.reg.save_credential(111, LOGIN, PASSWORD)
        self.reg.note_provisioned(111, LOGIN)
        self.reg.save_credential(111, LOGIN, "new-secret-do-not-print")
        self.assertEqual(self.reg.get_credential(111), (LOGIN, "new-secret-do-not-print"))
        self.assertNotIn(PASSWORD.encode(), self.path.read_bytes())

    def test_only_authorized_stage_can_save(self):
        self.reg.register(222)
        with self.assertRaises(ValueError):
            self.reg.save_credential(222, LOGIN, PASSWORD)
        with self.assertRaises(ValueError):
            self.reg.save_credential(999, LOGIN, PASSWORD)


if __name__ == "__main__":
    unittest.main()
