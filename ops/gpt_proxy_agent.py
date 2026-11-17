#!/usr/bin/env python3
"""Isolated, loopback-only management API for the dedicated squid-gpt instance.

No nginx changes, Squid reloads, shell execution, or access to older proxies.
NOT DEPLOYED: review service privileges and private channel before production.
"""
from __future__ import annotations

from contextlib import closing
import hmac
import json
import os
import re
import secrets
import sqlite3
import stat
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

USERS_PATH = Path(os.getenv("GPT_AGENT_USERS_PATH", "/etc/squid-gpt/users"))
DB_PATH = Path(os.getenv("GPT_AGENT_DB_PATH", "/var/lib/squid-gpt-agent/accounts.sqlite3"))
TOKEN_PATH = Path(os.getenv("GPT_AGENT_TOKEN_PATH", "/etc/squid-gpt-agent/token"))
HOST = "127.0.0.1"  # Never bind this prototype on a public interface.
PORT = int(os.getenv("GPT_AGENT_PORT", "43141"))
MAX_REQUEST = 4096


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def telegram_id(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value < 2**63:
        raise ApiError(400, "invalid telegram_id")
    return value


class AccountManager:
    """Local operations affect ONLY the new GPT account DB and htpasswd file."""

    def __init__(self, users_path: Path, db_path: Path):
        self.users_path = Path(users_path)
        self.db_path = Path(db_path)
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self):
        db = sqlite3.connect(self.db_path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=15000")
        return db

    def _init_db(self):
        self.db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS accounts (
                    telegram_id INTEGER PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('active','disabled')),
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    telegram_id INTEGER NOT NULL,
                    outcome TEXT NOT NULL
                );
            """)
        os.chmod(self.db_path, 0o600)

    def _read_users(self) -> tuple[str, os.stat_result]:
        if self.users_path.is_symlink():
            raise RuntimeError("users file must not be a symlink")
        st = self.users_path.stat()
        if not stat.S_ISREG(st.st_mode):
            raise RuntimeError("users file must be regular")
        return self.users_path.read_text(encoding="ascii"), st

    def _write_users(self, contents: str, original: os.stat_result):
        """Atomic write to only /etc/squid-gpt/users; preserve user and group."""
        if self.users_path.is_symlink():
            raise RuntimeError("users file changed to symlink")
        fd, tmp = tempfile.mkstemp(prefix=".gpt-users-", dir=self.users_path.parent)
        try:
            with os.fdopen(fd, "w", encoding="ascii") as f:
                f.write(contents)
                f.flush()
                os.fsync(f.fileno())
            os.chown(tmp, original.st_uid, original.st_gid)
            os.chmod(tmp, 0o640)
            os.replace(tmp, self.users_path)
            dirfd = os.open(self.users_path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(dirfd)
            finally:
                os.close(dirfd)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    @staticmethod
    def _set_entry(contents: str, username: str, hash_value: str | None) -> str:
        rows = contents.splitlines(keepends=True)
        kept = [line for line in rows if line.partition(":")[0] != username]
        if hash_value is not None:
            if kept and not kept[-1].endswith("\n"):
                kept[-1] += "\n"
            kept.append(f"{username}:{hash_value}\n")
        return "".join(kept)

    @staticmethod
    def _hash_password(password: str, username: str) -> str:
        # -i: pass through stdin, never through command-line arguments.
        result = subprocess.run(
            ["htpasswd", "-niB", username], input=password + "\n",
            capture_output=True, text=True, timeout=12, check=True,
        )
        entry = result.stdout.strip()
        prefix = username + ":"
        if not entry.startswith(prefix) or not re.fullmatch(
            r"\$2[aby]\$\d{2}\$[./A-Za-z0-9]{53}", entry[len(prefix):]
        ):
            raise RuntimeError("htpasswd did not return a bcrypt hash")
        return entry[len(prefix):]

    @staticmethod
    def _public(row) -> dict:
        return {
            "telegram_id": row["telegram_id"],
            "username": row["username"],
            "status": row["status"],
        }

    def dispatch(self, action: str, payload: dict) -> dict:
        if action not in {"create_user", "get_user_status", "disable_user", "enable_user"}:
            raise ApiError(404, "unknown action")
        uid = telegram_id(payload.get("telegram_id"))
        with self._lock, closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM accounts WHERE telegram_id=?", (uid,)
            ).fetchone()
            if action == "get_user_status":
                return self._public(row) if row else {"status": "not_found"}

            if action == "create_user":
                if row:
                    # Retried requests must not reissue the plaintext password.
                    return {**self._public(row), "created": False}
                contents, st = self._read_users()
                username = "gpt_" + secrets.token_hex(8)
                while db.execute(
                    "SELECT 1 FROM accounts WHERE username=?", (username,)
                ).fetchone() or any(
                    line.startswith(username + ":") for line in contents.splitlines()
                ):
                    username = "gpt_" + secrets.token_hex(8)
                password = secrets.token_urlsafe(24)
                password_hash = self._hash_password(password, username)
                now = int(time.time())
                db.execute(
                    """INSERT INTO accounts
                       (telegram_id, username, password_hash, status, created_at, updated_at)
                       VALUES (?, ?, ?, 'active', ?, ?)""",
                    (uid, username, password_hash, now, now)
                )
                db.execute(
                    "INSERT INTO audit (timestamp, action, telegram_id, outcome) VALUES (?, ?, ?, ?)",
                    (now, action, uid, "created")
                )
                self._write_users(self._set_entry(contents, username, password_hash), st)
                try:
                    db.commit()
                except Exception:
                    self._write_users(contents, st)
                    raise
                return {
                    "telegram_id": uid, "username": username, "status": "active",
                    "created": True, "initial_password": password,
                }

            if row is None:
                raise ApiError(404, "user not found")
            desired = "disabled" if action == "disable_user" else "active"
            if row["status"] == desired:
                return self._public(row)
            contents, st = self._read_users()
            updated = self._set_entry(
                contents, row["username"],
                row["password_hash"] if desired == "active" else None,
            )
            now = int(time.time())
            db.execute(
                "UPDATE accounts SET status=?, updated_at=? WHERE telegram_id=?",
                (desired, now, uid)
            )
            db.execute(
                "INSERT INTO audit (timestamp, action, telegram_id, outcome) VALUES (?, ?, ?, ?)",
                (now, action, uid, desired)
            )
            self._write_users(updated, st)
            try:
                db.commit()
            except Exception:
                self._write_users(contents, st)
                raise
            return {"telegram_id": uid, "username": row["username"], "status": desired}


class Handler(BaseHTTPRequestHandler):
    manager: AccountManager
    token_path: Path

    def log_message(self, fmt, *args):
        # Avoid leaking credentials, token, Telegram IDs or PII.
        pass

    def _reply(self, status: int, obj: dict):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        try:
            expected = self.token_path.read_text(encoding="ascii").strip()
        except OSError:
            self._reply(503, {"error": "agent not configured"})
            return
        received = self.headers.get("X-GPT-Agent-Token", "")
        if not expected or not hmac.compare_digest(expected, received):
            self._reply(401, {"error": "unauthorized"})
            return
        match = re.fullmatch(
            r"/v1/(create_user|get_user_status|disable_user|enable_user)", self.path
        )
        if not match:
            self._reply(404, {"error": "not found"})
            return
        try:
            if self.headers.get("Content-Type", "").split(";")[0].lower().strip() != "application/json":
                raise ApiError(415, "application/json required")
            length = int(self.headers.get("Content-Length", "0"))
            if length < 2 or length > MAX_REQUEST:
                raise ApiError(413, "invalid request size")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ApiError(400, "object required")
            result = self.manager.dispatch(match.group(1), payload)
            self._reply(200, result)
        except ApiError as exc:
            self._reply(exc.status, {"error": exc.message})
        except (ValueError, UnicodeError, json.JSONDecodeError):
            self._reply(400, {"error": "invalid request"})
        except Exception:
            self._reply(500, {"error": "internal error"})


def main():
    if not TOKEN_PATH.exists() or not TOKEN_PATH.read_text(encoding="ascii").strip():
        raise SystemExit("A private GPT_AGENT_TOKEN_PATH is required")
    Handler.manager = AccountManager(USERS_PATH, DB_PATH)
    Handler.token_path = TOKEN_PATH
    with ThreadingHTTPServer((HOST, PORT), Handler) as httpd:
        httpd.serve_forever()


if __name__ == "__main__":
    main()
