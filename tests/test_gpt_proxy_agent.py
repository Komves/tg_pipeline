"""Local-only tests: never contact live Squid, Vesya or production servers."""
import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "gpt_proxy_agent", Path(__file__).resolve().parents[1] / "ops" / "gpt_proxy_agent.py"
)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


@unittest.skipUnless(shutil.which("htpasswd"), "htpasswd is required")
class AccountTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.users = root / "users"
        self.users.write_text("gpt-test:$2y$05$existinghashshouldstayuntouched\n")
        self.manager = module.AccountManager(self.users, root / "state.sqlite3")

    def test_create_and_repeat_does_not_repeat_password(self):
        first = self.manager.dispatch("create_user", {"telegram_id": 1234})
        self.assertTrue(first["created"])
        self.assertIn("initial_password", first)
        self.assertEqual(first["status"], "active")
        second = self.manager.dispatch("create_user", {"telegram_id": 1234})
        self.assertFalse(second["created"])
        self.assertNotIn("initial_password", second)
        self.assertEqual(first["username"], second["username"])
        content = self.users.read_text()
        self.assertTrue(content.startswith("gpt-test:"))
        self.assertEqual(content.count(first["username"] + ":"), 1)
        self.assertNotIn(first["initial_password"], content)

    def test_disable_enable_status(self):
        account = self.manager.dispatch("create_user", {"telegram_id": 55})
        uname = account["username"]
        self.assertEqual(
            self.manager.dispatch("disable_user", {"telegram_id": 55})["status"], "disabled"
        )
        self.assertNotIn(uname + ":", self.users.read_text())
        self.assertEqual(
            self.manager.dispatch("get_user_status", {"telegram_id": 55})["status"], "disabled"
        )
        self.assertEqual(
            self.manager.dispatch("enable_user", {"telegram_id": 55})["status"], "active"
        )
        self.assertIn(uname + ":", self.users.read_text())
        self.assertIn("gpt-test:", self.users.read_text())

    def test_invalid_id_never_creates_user(self):
        for bad in (0, -1, 1.1, "123", True, None):
            with self.assertRaises(module.ApiError):
                self.manager.dispatch("create_user", {"telegram_id": bad})
        self.assertEqual(
            self.manager.dispatch("get_user_status", {"telegram_id": 99}),
            {"status": "not_found"},
        )

    def test_unknown_action_is_rejected(self):
        with self.assertRaises(module.ApiError):
            self.manager.dispatch("execute_shell", {"telegram_id": 123})


if __name__ == "__main__":
    unittest.main()
