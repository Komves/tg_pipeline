"""Offline GPT-only Squid reconciliation tests; no production paths are used."""
import importlib.util
from pathlib import Path
import os
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location(
    "gpt_apply_projection",
    Path(__file__).resolve().parents[1] / "ops" / "gpt_apply_projection.py",
)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def bcrypt_sample():
    return "$2y$05$" + "A" * 53


class ProjectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.staged = root / "staged"
        self.live = root / "live"
        self.state = root / "state"
        self.staged.write_text("")
        self.live.write_text("gpt-test:OLD_HASH\nlegacy:OTHER_HASH\n")

    def test_first_sync_preserves_unmanaged(self):
        self.staged.write_text("gpt_0123456789abcdef:" + bcrypt_sample() + "\n")
        self.assertTrue(module.sync_accounts(self.staged, self.live, self.state))
        result = self.live.read_text()
        self.assertIn("gpt-test:OLD_HASH", result)
        self.assertIn("legacy:OTHER_HASH", result)
        self.assertIn("gpt_0123456789abcdef:" + bcrypt_sample(), result)
        self.assertFalse(module.sync_accounts(self.staged, self.live, self.state))

    def test_revoke_removes_only_owned(self):
        self.staged.write_text("gpt_0123456789abcdef:" + bcrypt_sample() + "\n")
        module.sync_accounts(self.staged, self.live, self.state)
        self.staged.write_text("")
        module.sync_accounts(self.staged, self.live, self.state)
        self.assertNotIn("gpt_0123456789abcdef", self.live.read_text())
        self.assertIn("gpt-test:OLD_HASH", self.live.read_text())

    def test_invalid_projection_preserves_live(self):
        cases = (
            "root:bad\n",
            "gpt_0123456789abcdef:plainsecret\n",
            "gpt_0123456789abcdef:" + bcrypt_sample() + "\n"
            "gpt_0123456789abcdef:" + bcrypt_sample() + "\n",
        )
        for invalid in cases:
            previous = self.live.read_bytes()
            self.staged.write_text(invalid)
            with self.assertRaises(ValueError):
                module.sync_accounts(self.staged, self.live, self.state)
            self.assertEqual(self.live.read_bytes(), previous)

    def test_do_not_seize_preexisting_account(self):
        self.live.write_text("gpt_0123456789abcdef:MANUAL\n")
        self.staged.write_text("gpt_0123456789abcdef:" + bcrypt_sample() + "\n")
        with self.assertRaises(ValueError):
            module.sync_accounts(self.staged, self.live, self.state)
        self.assertEqual(self.live.read_text(), "gpt_0123456789abcdef:MANUAL\n")

    def test_manifest_recovery_revokes_after_crash(self):
        self.staged.write_text("gpt_0123456789abcdef:" + bcrypt_sample() + "\n")
        module.sync_accounts(self.staged, self.live, self.state)
        self.staged.write_text("")
        module._store_manifest(self.state / "managed.json", {"gpt_0123456789abcdef"})
        module.sync_accounts(self.staged, self.live, self.state)
        self.assertNotIn("gpt_0123456789abcdef", self.live.read_text())

    def test_symlink_projection_rejected(self):
        self.staged.unlink()
        self.staged.symlink_to(self.live)
        with self.assertRaises(OSError):
            module.sync_accounts(self.staged, self.live, self.state)


if __name__ == "__main__":
    unittest.main()
