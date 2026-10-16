"""Regression checks for the shared birthday calendar (stdlib unittest)."""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vesya_tools.calendar.birthday import build_birthday_message
from vesya_tools.calendar.parser import parse_birthday_date, parse_birthday_lines
from vesya_tools.calendar.storage import CalendarStorage


class BirthdaySharedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "calendar.sqlite3"
        self.s = CalendarStorage(self.db)
        self.now = "2026-10-16T09:00:00+05:00"
        self.group = -100123456

    def add(self, owner, name, date="1973-01-29", telegram_id=None):
        self.s.upsert_birthday(self.group, owner, "В гостях у Веси",
                              name, date, self.now, telegram_user_id=telegram_id)

    def test_two_owners_share_one_record(self):
        self.add(1, "Серёга")
        self.add(2, "Серёга", "1973-01-29")
        self.assertEqual(len(self.s.list_birthdays(1, self.group)), 1)
        self.assertEqual(len(self.s.list_birthdays(2, self.group)), 1)

    def test_legacy_id_is_backfilled_and_replaced_without_losing_birthday(self):
        # Existing production-like record pre-dates migration.
        with sqlite3.connect(self.db) as con:
            con.execute("""INSERT INTO birthdays
                (group_id, owner_user_id, group_title, person_name,
                 birthday, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (self.group, 1, "В гостях у Веси", "id=123456789",
                 "1973-01-29", self.now, self.now))
        s2 = CalendarStorage(self.db)
        row = s2.list_birthdays(42, self.group)[0]
        self.assertEqual(row["telegram_user_id"], 123456789)
        self.assertEqual(row["birthday"], "1973-01-29")
        self.assertTrue(s2.rename_birthday_by_telegram_id(self.group, 123456789, "Сергей", self.now))
        row = s2.list_birthdays(77, self.group)[0]
        self.assertEqual((row["person_name"], row["telegram_user_id"]),
                         ("Сергей", 123456789))
        self.assertEqual(row["birthday"], "1973-01-29")

    def test_import_with_id_updates_legacy_row_not_adds_duplicate(self):
        self.add(1, "id=123456789")
        self.add(2, "Сергей", telegram_id=123456789)
        rows = self.s.list_birthdays(77, self.group)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["person_name"], "Сергей")

    def test_group_boundaries_are_independent(self):
        self.add(1, "Сергей")
        self.s.upsert_birthday(-10098765, 1, "Комвес", "Сергей",
                              "1974-04-19", self.now)
        self.assertEqual(len(self.s.list_birthdays(1, self.group)), 1)
        self.assertEqual(len(self.s.list_birthdays(1, -10098765)), 1)

    def test_legacy_duplicate_marks_both_owners(self):
        for owner in (1, 2):
            with sqlite3.connect(self.db) as con:
                con.execute("""INSERT INTO birthdays
                    (group_id, owner_user_id, group_title, person_name,
                     telegram_user_id, birthday, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (self.group, owner, "В гостях у Веси", "id=123456789",
                     123456789, "1973-01-29", self.now, self.now))
        due = self.s.todays_birthdays("01-29", 2027)
        self.assertEqual(len(due), 1)
        self.s.mark_birthday_congratulated(due[0]["id"], 2027, self.now)
        self.assertEqual(self.s.todays_birthdays("01-29", 2027), [])

    def test_numeric_and_written_dates(self):
        self.assertEqual(parse_birthday_date("29.01.1973", 2026), "1973-01-29")
        self.assertEqual(parse_birthday_date("29 января 1973", 2026), "1973-01-29")
        self.assertEqual(parse_birthday_date("29/01", 2026), "1900-01-29")
        self.assertIsNone(parse_birthday_date("31.02.1973", 2026))
        self.assertEqual(parse_birthday_lines(
            "Сергей (id=123456789) — 29.01.1973\nМиша - 19-04-1974", 2026),
            [("Сергей (id=123456789)", "1973-01-29"), ("Миша", "1974-04-19")])

    def test_html_caption_escapes_user_input(self):
        text = build_birthday_message("<Сергей>", 123456789,
                                      wish="Пусть всё получается!")
        self.assertIn('<a href="tg://user?id=123456789">&lt;Сергей&gt;</a>', text)
        self.assertTrue(text.endswith("Красоты тебе! 😎"))


class BirthdaySendTests(unittest.IsolatedAsyncioTestCase):
    async def test_video_uses_existing_beauty_pool_and_group_history(self):
        import asyncio
        import sys
        import types
        from unittest.mock import AsyncMock, patch

        from vesya_tools.calendar.scheduler import _send_birthday

        class File:
            def __init__(self, path):
                self.path = path

        class Bot:
            def __init__(self):
                self.videos = []
                self.messages = []

            async def send_video(self, group_id, **kwargs):
                self.videos.append((group_id, kwargs))

            async def send_message(self, group_id, text, **kwargs):
                self.messages.append((group_id, text, kwargs))

        class Storage:
            def update_birthday_identity(self, *args):
                pass

        with tempfile.TemporaryDirectory() as tmp:
            video_path = Path(tmp) / "clip.mp4"
            video_path.write_bytes(b"fake mp4 bytes")
            marks = []
            beauty = types.ModuleType("beauty_pool")
            beauty.pick_unseen_beauty_clip = lambda user_id: {
                "id": "clip1", "path": str(video_path)
            }
            beauty.mark_beauty_clip_sent = lambda user_id, clip_id: marks.append((user_id, clip_id))
            types_module = types.ModuleType("aiogram.types")
            types_module.FSInputFile = File
            bot = Bot()
            row = {"id": 2, "group_id": -100123456, "person_name": "Сергей",
                   "group_title": "В гостях у Веси", "telegram_user_id": 123456789,
                   "username": None}
            with patch.dict(sys.modules, {"beauty_pool": beauty, "aiogram.types": types_module}), \
                 patch("vesya_tools.calendar.scheduler.generate_birthday_message",
                       new=AsyncMock(return_value="🎂 Сергей, с днюхой!\n\nКрасоты тебе!")):
                await _send_birthday(bot, Storage(), row)
            self.assertEqual(len(bot.videos), 1)
            self.assertEqual(bot.messages, [])
            self.assertEqual(bot.videos[0][0], -100123456)
            self.assertEqual(marks, [(-100123456, "clip1")])

    async def test_missing_video_falls_back_to_text(self):
        import sys
        import types
        from unittest.mock import AsyncMock, patch
        from vesya_tools.calendar.scheduler import _send_birthday

        beauty = types.ModuleType("beauty_pool")
        beauty.pick_unseen_beauty_clip = lambda group_id: None
        beauty.mark_beauty_clip_sent = lambda group_id, clip_id: None
        aiogram_types = types.ModuleType("aiogram.types")
        aiogram_types.FSInputFile = object

        class Bot:
            def __init__(self):
                self.messages = []

            async def send_message(self, *args, **kwargs):
                self.messages.append((args, kwargs))

        class Storage:
            pass

        bot = Bot()
        row = {"id": 1, "group_id": -10077, "person_name": "Анна",
               "group_title": "Комвес", "telegram_user_id": None, "username": None}
        with patch.dict(sys.modules, {"beauty_pool": beauty, "aiogram.types": aiogram_types}), \
             patch("vesya_tools.calendar.scheduler.generate_birthday_message",
                   new=AsyncMock(return_value="Поздравление!")):
            await _send_birthday(bot, Storage(), row)
        self.assertEqual(len(bot.messages), 1)
        self.assertEqual(bot.messages[0][0][0], -10077)


if __name__ == "__main__":
    unittest.main()
