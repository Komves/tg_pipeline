from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


class CalendarStorage:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.db_path))
        con.row_factory = sqlite3.Row
        return con

    def _init_db(self) -> None:
        with self._connect() as con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    remind_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    sent_at TEXT
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS group_registry (
                    group_id INTEGER NOT NULL,
                    owner_user_id INTEGER NOT NULL,
                    group_title TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    PRIMARY KEY (group_id, owner_user_id)
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS birthdays (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id INTEGER NOT NULL,
                    owner_user_id INTEGER NOT NULL,
                    group_title TEXT NOT NULL,
                    person_name TEXT NOT NULL,
                    telegram_user_id INTEGER,
                    username TEXT,
                    birthday TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_congrats_year INTEGER,
                    UNIQUE(group_id, owner_user_id, person_name)
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS group_members (
                    group_id INTEGER NOT NULL,
                    telegram_user_id INTEGER NOT NULL,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    full_name TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    PRIMARY KEY (group_id, telegram_user_id)
                )
            """)
            self._ensure_column(con, "birthdays", "telegram_user_id", "INTEGER")
            self._ensure_column(con, "birthdays", "username", "TEXT")

    def _ensure_column(self, con: sqlite3.Connection, table: str, column: str, column_type: str) -> None:
        rows = con.execute(f"PRAGMA table_info({table})").fetchall()
        existing = {str(row["name"]) for row in rows}
        if column not in existing:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")

    def add_reminder(self, chat_id: int, user_id: int, text: str, remind_at: str, created_at: str) -> None:
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO reminders(chat_id, user_id, text, remind_at, created_at, sent_at)
                VALUES (?, ?, ?, ?, ?, NULL)
                """,
                (chat_id, user_id, text, remind_at, created_at),
            )

    def due_reminders(self, now_iso: str) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                """
                SELECT id, chat_id, user_id, text, remind_at
                FROM reminders
                WHERE sent_at IS NULL AND remind_at <= ?
                ORDER BY remind_at ASC
                """,
                (now_iso,),
            ).fetchall()
        return [dict(r) for r in rows]

    def mark_reminder_sent(self, reminder_id: int, sent_at: str) -> None:
        with self._connect() as con:
            con.execute(
                "UPDATE reminders SET sent_at = ? WHERE id = ?",
                (sent_at, reminder_id),
            )

    def register_group(self, group_id: int, owner_user_id: int, group_title: str, last_seen_at: str) -> None:
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO group_registry(group_id, owner_user_id, group_title, last_seen_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(group_id, owner_user_id)
                DO UPDATE SET group_title = excluded.group_title, last_seen_at = excluded.last_seen_at
                """,
                (group_id, owner_user_id, group_title, last_seen_at),
            )

    def list_groups(self, owner_user_id: int) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                """
                SELECT group_id, group_title, last_seen_at
                FROM group_registry
                WHERE owner_user_id = ?
                ORDER BY group_title COLLATE NOCASE
                """,
                (owner_user_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def find_group(self, owner_user_id: int, group_title: str) -> dict[str, Any] | None:
        needle = group_title.strip().lower()
        for row in self.list_groups(owner_user_id):
            if row["group_title"].strip().lower() == needle:
                return row
        for row in self.list_groups(owner_user_id):
            if needle in row["group_title"].strip().lower():
                return row
        return None

    def upsert_birthday(
        self,
        group_id: int,
        owner_user_id: int,
        group_title: str,
        person_name: str,
        birthday: str,
        now_iso: str,
        telegram_user_id: int | None = None,
        username: str | None = None,
    ) -> None:
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO birthdays(group_id, owner_user_id, group_title, person_name, telegram_user_id, username, birthday, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(group_id, owner_user_id, person_name)
                DO UPDATE SET birthday = excluded.birthday,
                              telegram_user_id = excluded.telegram_user_id,
                              username = excluded.username,
                              group_title = excluded.group_title,
                              updated_at = excluded.updated_at
                """,
                (group_id, owner_user_id, group_title, person_name, telegram_user_id, username, birthday, now_iso, now_iso),
            )

    def list_birthdays(self, owner_user_id: int, group_id: int) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                """
                SELECT id, group_id, group_title, person_name, birthday
                FROM birthdays
                WHERE owner_user_id = ? AND group_id = ?
                ORDER BY substr(birthday, 6, 5), person_name COLLATE NOCASE
                """,
                (owner_user_id, group_id),
            ).fetchall()
        return [dict(r) for r in rows]

    def find_birthday_by_person(self, owner_user_id: int, person_name: str) -> list[dict[str, Any]]:
        needle = " ".join(person_name.strip().lower().split())

        with self._connect() as con:
            rows = con.execute(
                """
                SELECT id, group_id, group_title, person_name, birthday
                FROM birthdays
                WHERE owner_user_id = ?
                """,
                (owner_user_id,),
            ).fetchall()

        result = []

        for row in rows:
            normalized = " ".join(str(row["person_name"]).strip().lower().split())
            if normalized == needle:
                result.append(dict(row))

        return result

    def find_birthday_by_person_in_group(
        self,
        owner_user_id: int,
        group_id: int,
        person_name: str,
    ) -> list[dict[str, Any]]:
        needle = " ".join(person_name.strip().lower().split())

        with self._connect() as con:
            rows = con.execute(
                """
                SELECT id, group_id, group_title, person_name, birthday
                FROM birthdays
                WHERE owner_user_id = ?
                  AND group_id = ?
                """,
                (owner_user_id, group_id),
            ).fetchall()

        result = []

        for row in rows:
            normalized = " ".join(str(row["person_name"]).strip().lower().split())
            if normalized == needle:
                result.append(dict(row))

        return result

    def find_birthday_by_person_in_group(
        self,
        owner_user_id: int,
        group_id: int,
        person_name: str,
    ) -> list[dict[str, Any]]:
        needle = " ".join(person_name.strip().lower().split())

        with self._connect() as con:
            rows = con.execute(
                """
                SELECT id, group_id, group_title, person_name, birthday
                FROM birthdays
                WHERE owner_user_id = ?
                  AND group_id = ?
                  AND lower(trim(person_name)) = ?
                """,
                (owner_user_id, group_id, needle),
            ).fetchall()

        return [dict(r) for r in rows]

    def update_birthday(self, birthday_id: int, birthday: str, now_iso: str) -> None:
        with self._connect() as con:
            con.execute(
                "UPDATE birthdays SET birthday = ?, updated_at = ? WHERE id = ?",
                (birthday, now_iso, birthday_id),
            )

    def delete_birthday(self, birthday_id: int) -> None:
        with self._connect() as con:
            con.execute("DELETE FROM birthdays WHERE id = ?", (birthday_id,))

    def todays_birthdays(self, month_day: str, year: int) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                """
                SELECT id, group_id, group_title, person_name, telegram_user_id, username, birthday, last_congrats_year
                FROM birthdays
                WHERE substr(birthday, 6, 5) = ?
                  AND (last_congrats_year IS NULL OR last_congrats_year < ?)
                ORDER BY group_title COLLATE NOCASE, person_name COLLATE NOCASE
                """,
                (month_day, year),
            ).fetchall()
        return [dict(r) for r in rows]

    def mark_birthday_congratulated(self, birthday_id: int, year: int, now_iso: str) -> None:
        with self._connect() as con:
            con.execute(
                "UPDATE birthdays SET last_congrats_year = ?, updated_at = ? WHERE id = ?",
                (year, now_iso, birthday_id),
            )

    def register_group_member(
        self,
        group_id: int,
        telegram_user_id: int,
        username: str | None,
        first_name: str | None,
        last_name: str | None,
        full_name: str,
        last_seen_at: str,
    ) -> None:
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO group_members(group_id, telegram_user_id, username, first_name, last_name, full_name, last_seen_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(group_id, telegram_user_id)
                DO UPDATE SET username = excluded.username,
                              first_name = excluded.first_name,
                              last_name = excluded.last_name,
                              full_name = excluded.full_name,
                              last_seen_at = excluded.last_seen_at
                """,
                (group_id, telegram_user_id, username, first_name, last_name, full_name, last_seen_at),
            )

    def find_group_member(self, group_id: int, person_name: str) -> dict[str, Any] | None:
        needle = " ".join(person_name.strip().lower().split())

        with self._connect() as con:
            rows = con.execute(
                """
                SELECT group_id, telegram_user_id, username, first_name, last_name, full_name
                FROM group_members
                WHERE group_id = ?
                """,
                (group_id,),
            ).fetchall()

        candidates = [dict(r) for r in rows]

        for row in candidates:
            if " ".join(str(row["full_name"]).lower().split()) == needle:
                return row

        for row in candidates:
            username = str(row.get("username") or "").lower().lstrip("@")
            if username and username == needle.lstrip("@"):
                return row

        return None

    def list_group_members(self, group_id: int) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                """
                SELECT telegram_user_id, username, first_name, last_name, full_name, last_seen_at
                FROM group_members
                WHERE group_id = ?
                ORDER BY full_name COLLATE NOCASE
                """,
                (group_id,),
            ).fetchall()

        return [dict(r) for r in rows]