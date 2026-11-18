"""Regression: /start must never be interpreted as an open-ended LLM prompt."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bot"))

from vesya_tools.start_command import is_bare_start, START_GREETING


class BareStartTests(unittest.TestCase):
    def test_plain_telegram_start(self):
        for command in ("/start", " /start ", "/START", "/start@VesyaBot"):
            with self.subTest(command=command):
                self.assertTrue(is_bare_start(command))

    def test_other_commands_are_not_hijacked(self):
        for command in (
            "/start proxy", "/start gpt", "/gpt", "/proxy", "/gpt_status",
            "/start@VesyaBot proxy", "/startsomething", "Веся, подключи GPT",
        ):
            with self.subTest(command=command):
                self.assertFalse(is_bare_start(command))

    def test_greeting_is_not_a_research_prompt(self):
        self.assertIn("Веся на связи", START_GREETING)
        self.assertIn("подключения ChatGPT", START_GREETING)

    def test_start_is_intercepted_before_dialog_route(self):
        main = (ROOT / "bot" / "main.py").read_text(encoding="utf-8")
        handler = main.index("async def vesya_handler(message: Message)")
        segment = main[handler:handler + 3600]
        start_pos = segment.index("if is_bare_start(message.text or \"\"):")
        gpt_pos = segment.index("if await handle_gpt_message(")
        legacy_pos = segment.index('print("[TYPE DEBUG]"')
        self.assertLess(start_pos, gpt_pos)
        self.assertLess(start_pos, legacy_pos)
        self.assertIn("await message.answer(START_GREETING)", segment)


if __name__ == "__main__":
    unittest.main()
