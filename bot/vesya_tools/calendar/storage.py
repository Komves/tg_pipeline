from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any
from datetime import datetime

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
            con.execute("""
                CREATE TABLE IF NOT EXISTS user_timezones (
                    user_id INTEGER PRIMARY KEY,
                    timezone TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            self._ensure_column(con, "birthdays", "telegram_user_id", "INTEGER")
            self._ensure_column(con, "birthdays", "username", "TEXT")
            # Official name and friendly address are separate from the legacy
            # person_name field, so old data and uniqueness constraints survive.
            self._ensure_column(con, "birthdays", "full_name", "TEXT")
            self._ensure_column(con, "birthdays", "nickname", "TEXT")
            # Legacy records use "id=123456" as the name: restore their Telegram identity.
            con.execute("""
                UPDATE birthdays
                SET telegram_user_id = CAST(SUBSTR(person_name, 4) AS INTEGER)
                WHERE telegram_user_id IS NULL
                  AND person_name GLOB 'id=[0-9]*'
                  AND CAST(SUBSTR(person_name, 4) AS INTEGER) > 0
            """)
            con.execute("CREATE INDEX IF NOT EXISTS idx_birthdays_group ON birthdays(group_id)")


    def _ensure_column(self, con: sqlite3.Connection, table: str, column: str, column_type: str) -> None:
        rows = con.execute(f"PRAGMA table_info({table})").fetchall()
        existing = {str(row["name"]) for row in rows}
        if column not in existing:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")

    def set_user_timezone(self, user_id: int, timezone: str, updated_at: str) -> None:
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO user_timezones(user_id, timezone, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id)
                DO UPDATE SET timezone = excluded.timezone,
                              updated_at = excluded.updated_at
                """,
                (int(user_id), str(timezone), str(updated_at)),
            )

    def get_user_timezone(self, user_id: int) -> str | None:
        with self._connect() as con:
            row = con.execute(
                """
                SELECT timezone
                FROM user_timezones
                WHERE user_id = ?
                """,
                (int(user_id),),
            ).fetchone()

        if not row:
            return None

        value = str(row["timezone"] or "").strip()
        return value or None

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
        now_dt = datetime.fromisoformat(now_iso)

        with self._connect() as con:
            rows = con.execute(
                """
                SELECT id, chat_id, user_id, text, remind_at
                FROM reminders
                WHERE sent_at IS NULL
                ORDER BY remind_at ASC
                """
            ).fetchall()

        due = []

        for row in rows:
            item = dict(row)
            try:
                remind_dt = datetime.fromisoformat(str(item["remind_at"]))
            except Exception:
                continue

            if remind_dt <= now_dt:
                due.append(item)

        return due


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
        """Birthdays belong to the GROUP, not to the user who entered them."""
        name = " ".join(person_name.strip().split())
        if not name:
            raise ValueError("birthday person name is empty")

        if not telegram_user_id and name.lower().startswith("id="):
            raw_id = name[3:].strip()
            if raw_id.isdigit() and int(raw_id) > 0:
                telegram_user_id = int(raw_id)

        norm = name.casefold()
        with self._connect() as con:
            rows = [dict(r) for r in con.execute(
                """SELECT id, owner_user_id, person_name, telegram_user_id,
                          username, last_congrats_year, full_name, nickname
                   FROM birthdays WHERE group_id = ? ORDER BY id""",
                (int(group_id),),
            ).fetchall()]

            matches = []
            for row in rows:
                old_id = row["telegram_user_id"]
                aliases = (
                    row["person_name"],
                    row.get("full_name"),
                    row.get("nickname"),
                )
                old_names = {" ".join(str(alias).casefold().split()) for alias in aliases if alias}
                same_id = telegram_user_id and old_id and int(old_id) == int(telegram_user_id)
                conflicting_ids = telegram_user_id and old_id and int(old_id) != int(telegram_user_id)
                if same_id or (norm in old_names and not conflicting_ids):
                    matches.append(row)

            if not matches:
                con.execute(
                    """INSERT INTO birthdays
                       (group_id, owner_user_id, group_title, person_name,
                        telegram_user_id, username, birthday, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (group_id, owner_user_id, group_title, name, telegram_user_id,
                     username, birthday, now_iso, now_iso),
                )
                return

            # Consolidate duplicate legacy records before updating the canonical one.
            primary = matches[0]
            best_name = name
            if name.lower().startswith("id=") and not str(primary["person_name"]).lower().startswith("id="):
                best_name = str(primary["person_name"])
            keep_id = telegram_user_id or primary["telegram_user_id"]
            keep_username = username or primary["username"]
            keep_full_name = next((str(r["full_name"]) for r in matches if r.get("full_name")), None)
            keep_nickname = next((str(r["nickname"]) for r in matches if r.get("nickname")), None)
            years = [int(row["last_congrats_year"]) for row in matches if row["last_congrats_year"] is not None]
            last_year = max(years) if years else None
            for row in matches[1:]:
                con.execute("DELETE FROM birthdays WHERE id = ?", (int(row["id"]),))
            con.execute(
                """UPDATE birthdays SET group_title=?, person_name=?, birthday=?,
                       telegram_user_id=?, username=?, updated_at=?, last_congrats_year=?,
                       full_name=?, nickname=?
                   WHERE id=?""",
                (group_title, best_name, birthday, keep_id, keep_username,
                 now_iso, last_year, keep_full_name, keep_nickname, int(primary["id"])),
            )

    def update_birthday_identity(
        self, birthday_id: int, person_name: str, username: str | None = None,
    ) -> None:
        """Resolve a legacy id= name, but never overwrite a human-entered name."""
        name = " ".join((person_name or "").split())
        if not name:
            return
        with self._connect() as con:
            row = con.execute("SELECT person_name FROM birthdays WHERE id=?", (birthday_id,)).fetchone()
            if row and str(row["person_name"]).lower().startswith("id="):
                con.execute(
                    """UPDATE birthdays SET person_name=?,
                       full_name=COALESCE(full_name, ?),
                       username=COALESCE(?, username) WHERE id=?""",
                    (name, name, username, birthday_id),
                )

    def rename_birthday_by_telegram_id(
        self, group_id: int, telegram_user_id: int, new_name: str, now_iso: str,
    ) -> bool:
        name = " ".join((new_name or "").split())
        if not name:
            return False
        with self._connect() as con:
            row = con.execute(
                """SELECT id FROM birthdays WHERE group_id=?
                   AND (telegram_user_id=? OR person_name=?) ORDER BY id LIMIT 1""",
                (group_id, telegram_user_id, f"id={telegram_user_id}"),
            ).fetchone()
            if not row:
                return False
            con.execute(
                """UPDATE birthdays SET person_name=?, nickname=?,
                       telegram_user_id=?, updated_at=? WHERE id=?""",
                (name, name, telegram_user_id, now_iso, int(row["id"])),
            )
            return True

    def set_birthday_names_by_telegram_id(
        self, group_id: int, telegram_user_id: int,
        full_name: str, nickname: str, now_iso: str,
    ) -> bool:
        """Attach a full identity and a familiar address without changing DOB."""
        official = " ".join(full_name.split())
        familiar = " ".join(nickname.split())
        if not official or not familiar or len(official) > 100 or len(familiar) > 100:
            return False
        with self._connect() as con:
            cursor = con.execute(
                """UPDATE birthdays
                   SET full_name=?, nickname=?, telegram_user_id=?, updated_at=?
                   WHERE group_id=?
                     AND (telegram_user_id=? OR person_name=?)""",
                (official, familiar, telegram_user_id, now_iso,
                 group_id, telegram_user_id, f"id={telegram_user_id}"),
            )
            return cursor.rowcount > 0

    @staticmethod
    def _unique_birthdays(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Read legacy owner-specific duplicates as a single group birthday."""
        chosen: dict[tuple[int, str], dict[str, Any]] = {}
        # Last edited record wins if legacy owners supplied conflicting dates.
        for row in sorted(rows, key=lambda r: (str(r.get("updated_at") or ""), int(r["id"])), reverse=True):
            user_id = row.get("telegram_user_id")
            name = " ".join(str(row["person_name"]).casefold().split())
            if not user_id and name.startswith("id=") and name[3:].isdigit():
                user_id = int(name[3:])
            identity = f"id:{user_id}" if user_id else f"name:{name}"
            key = (int(row["group_id"]), identity)
            existing = chosen.get(key)
            if existing is None:
                chosen[key] = row
            elif str(existing["person_name"]).lower().startswith("id=") and not name.startswith("id="):
                # A resolved human name is preferable to an old id= placeholder.
                chosen[key] = row
        return list(chosen.values())

    def list_birthdays(self, owner_user_id: int, group_id: int) -> list[dict[str, Any]]:
        # owner_user_id is retained for API compatibility; the list is shared.
        with self._connect() as con:
            rows = con.execute(
                """SELECT id, group_id, group_title, person_name, birthday,
                          telegram_user_id, username, updated_at, last_congrats_year,
                          full_name, nickname
                   FROM birthdays WHERE group_id = ?""",
                (group_id,),
            ).fetchall()
        unique = self._unique_birthdays([dict(r) for r in rows])
        return sorted(unique, key=lambda r: (r["birthday"][5:], r["person_name"].casefold()))

    def find_birthday_by_person(self, owner_user_id: int, person_name: str) -> list[dict[str, Any]]:
        needle = " ".join(person_name.strip().casefold().split())
        with self._connect() as con:
            rows = con.execute(
                """SELECT id, group_id, group_title, person_name, birthday, full_name, nickname
                   FROM birthdays
                   WHERE group_id IN
                     (SELECT group_id FROM group_registry WHERE owner_user_id=?)""",
                (owner_user_id,),
            ).fetchall()
        return [dict(r) for r in rows
                if needle in {
                    " ".join(str(alias).casefold().split())
                    for alias in (r["person_name"], r["full_name"], r["nickname"]) if alias
                }]

    def find_birthday_by_person_in_group(
        self, owner_user_id: int, group_id: int, person_name: str,
    ) -> list[dict[str, Any]]:
        needle = " ".join(person_name.strip().casefold().split())
        with self._connect() as con:
            rows = con.execute(
                """SELECT id, group_id, group_title, person_name, birthday, full_name, nickname
                   FROM birthdays WHERE group_id=?""",
                (group_id,),
            ).fetchall()
        return [dict(r) for r in rows
                if needle in {
                    " ".join(str(alias).casefold().split())
                    for alias in (r["person_name"], r["full_name"], r["nickname"]) if alias
                }]

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
        # De-duplicate BEFORE matching the date: a legacy duplicate with a
        # different stored birthday must not cause a second greeting that year.
        with self._connect() as con:
            rows = con.execute(
                """SELECT id, group_id, group_title, person_name, telegram_user_id,
                          username, birthday, last_congrats_year, updated_at,
                          full_name, nickname
                   FROM birthdays"""
            ).fetchall()
        chosen = self._unique_birthdays([dict(r) for r in rows])
        return sorted(
            [r for r in chosen
             if r["birthday"][5:] == month_day
             and (r["last_congrats_year"] is None or int(r["last_congrats_year"]) < year)],
            key=lambda r: (str(r["group_title"]).casefold(), str(r["person_name"]).casefold()),
        )

    def mark_birthday_congratulated(self, birthday_id: int, year: int, now_iso: str) -> None:
        # Mark all duplicate legacy rows as sent, not only one owner copy.
        with self._connect() as con:
            row = con.execute(
                "SELECT group_id, person_name, telegram_user_id FROM birthdays WHERE id=?",
                (birthday_id,),
            ).fetchone()
            if not row:
                return
            con.execute(
                """UPDATE birthdays SET last_congrats_year=?, updated_at=?
                   WHERE group_id=?
                     AND (id=?
                       OR (telegram_user_id IS NOT NULL AND telegram_user_id=?)
                       OR LOWER(TRIM(person_name))=LOWER(TRIM(?)))""",
                (year, now_iso, int(row["group_id"]), birthday_id,
                 row["telegram_user_id"], row["person_name"]),
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