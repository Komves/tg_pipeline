#!/usr/bin/env python3
"""Private loopback API for Vesya's 30-day guest proxy accounts.

The public TLS listener is stunnel, not this HTTP server.
Only Telegram IDs, account numbers, credentials and timestamps are stored.
"""
from __future__ import annotations

import hmac
import hashlib
import json
import os
import re
import secrets
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DB_PATH = Path("/var/lib/guest-proxy/users.db")
TOKEN_PATH = Path("/etc/guest-proxy-api.secret")
PORT = 43131
CLIENT_BINARY = Path("/usr/local/share/vesya-proxy/VesyaProxyClient.exe")
CLIENT_MAGIC = b"\nVESYA_PROXY_CONFIG_V1\n"
DOWNLOAD_LINK_TTL = 48 * 3600
DOWNLOAD_BASE = "https://paruslegal.ru:43130/download/"
MONTH = 30 * 24 * 3600


class ApiError(Exception):
    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message


def positive_id(value, key: str) -> int:
    if isinstance(value, bool):
        raise ApiError(400, f"invalid {key}")
    try:
        n = int(value)
    except (ValueError, TypeError):
        raise ApiError(400, f"invalid {key}") from None
    if n <= 0 or n > 9_223_372_036_854_775_807:
        raise ApiError(400, f"invalid {key}")
    return n


def connection():
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=10000")
    return db


def view(row, *, reveal: bool = False) -> dict:
    now = int(time.time())
    status = ("disabled" if row["disabled"]
              else "expired" if row["expires_at"] <= now
              else "active")
    result = {
        "id": int(row["id"]),
        "username": row["username"],
        "expires_at": int(row["expires_at"]),
        "status": status,
    }
    if reveal and status == "active":
        result["password"] = row["password"]
    return result


def dispatch(action: str, params: dict) -> dict:
    if action not in {"register", "status", "list", "renew", "disable"}:
        raise ApiError(404, "unknown action")
    with connection() as db:
        if action in {"register", "status"}:
            tg_id = positive_id(params.get("telegram_id"), "telegram_id")
            if action == "register":
                db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM users WHERE telegram_id=?", (tg_id,)
            ).fetchone()
            if row is None and action == "register":
                now = int(time.time())
                next_id = db.execute(
                    "SELECT COALESCE(MAX(id), 0) + 1 FROM users"
                ).fetchone()[0]
                login = f"user{next_id:03d}"
                password = secrets.token_hex(16)
                db.execute(
                    """INSERT INTO users
                    (id, telegram_id, username, password, created_at, expires_at, disabled)
                    VALUES (?, ?, ?, ?, ?, ?, 0)""",
                    (next_id, tg_id, login, password, now, now + MONTH)
                )
                row = db.execute(
                    "SELECT * FROM users WHERE id=?", (next_id,)
                ).fetchone()
            if row is None:
                return {"status": "not_found"}
            answer = view(row, reveal=True)
            if answer["status"] == "active":
                answer["download_url"] = issue_download_link(db, row)
            return answer

        if action == "list":
            rows = db.execute(
                "SELECT * FROM users ORDER BY id"
            ).fetchall()
            return {"users": [view(row) for row in rows]}

        user_id = positive_id(params.get("id"), "id")
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT * FROM users WHERE id=?", (user_id,)
        ).fetchone()
        if row is None:
            raise ApiError(404, "user not found")
        if action == "disable":
            db.execute("UPDATE users SET disabled=1 WHERE id=?", (user_id,))
        else:
            now = int(time.time())
            until = max(now, int(row["expires_at"])) + MONTH
            db.execute(
                "UPDATE users SET disabled=0, expires_at=? WHERE id=?",
                (until, user_id)
            )
        row = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return view(row)


def issue_download_link(db: sqlite3.Connection, row: sqlite3.Row) -> str:
    """A shareable, short-lived bearer URL; the plaintext token is never stored."""
    db.execute(
        """CREATE TABLE IF NOT EXISTS proxy_downloads (
            user_id INTEGER PRIMARY KEY,
            token_sha256 TEXT UNIQUE NOT NULL,
            issued_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL
        )"""
    )
    now = int(time.time())
    token = secrets.token_urlsafe(24)
    token_sha256 = hashlib.sha256(token.encode("ascii")).hexdigest()
    until = min(now + DOWNLOAD_LINK_TTL, int(row["expires_at"]))
    db.execute(
        """INSERT INTO proxy_downloads
            (user_id, token_sha256, issued_at, expires_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
            token_sha256=excluded.token_sha256,
            issued_at=excluded.issued_at,
            expires_at=excluded.expires_at""",
        (int(row["id"]), token_sha256, now, until),
    )
    return DOWNLOAD_BASE + token


def download_payload(token: str) -> bytes | None:
    """Return the user-specific PE file only for a valid unexpired link."""
    digest = hashlib.sha256(token.encode("ascii")).hexdigest()
    now = int(time.time())
    with connection() as db:
        row = db.execute(
            """SELECT u.* FROM proxy_downloads d
               JOIN users u ON u.id=d.user_id
               WHERE d.token_sha256=? AND d.expires_at>?
                 AND u.disabled=0 AND u.expires_at>?
                 AND u.telegram_id IS NOT NULL""",
            (digest, now, now),
        ).fetchone()
        if row is None:
            return None
        cfg = {
            "host": "paruslegal.ru",
            "port": 43128,
            "username": row["username"],
            "password": row["password"],
            "expires_at": row["expires_at"],
        }
    binary = CLIENT_BINARY.read_bytes()
    if not binary.startswith(b"MZ") or len(binary) > 20_000_000:
        raise RuntimeError("invalid Windows launcher binary")
    suffix = json.dumps(cfg, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return binary + CLIENT_MAGIC + suffix


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Do not log Telegram IDs or proxy credentials.
        pass

    def send_json(self, status: int, data: dict):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        match = re.fullmatch(r"/download/([A-Za-z0-9_-]{32})", self.path)
        if not match:
            self.send_json(404, {"error": "not found"})
            return
        try:
            content = download_payload(match.group(1))
        except (OSError, RuntimeError):
            self.send_json(503, {"error": "client download temporarily unavailable"})
            return
        except Exception:
            self.send_json(500, {"error": "internal error"})
            return
        if content is None:
            self.send_json(410, {"error": "link expired; request a new link from Vesya"})
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Disposition", 'attachment; filename="Vesya_ChatGPT.exe"')
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store, private")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)

    def do_POST(self):
        expected = TOKEN_PATH.read_text(encoding="ascii").strip()
        actual = self.headers.get("X-Proxy-Token", "")
        if not expected or not hmac.compare_digest(actual, expected):
            self.send_json(401, {"error": "unauthorized"})
            return

        if not re.fullmatch(r"/v1/(register|status|list|renew|disable)", self.path):
            self.send_json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 2 or length > 2048:
                raise ApiError(400, "invalid request size")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ApiError(400, "object required")
            answer = dispatch(self.path.rsplit("/", 1)[1], payload)
            self.send_json(200, answer)
        except ApiError as exc:
            self.send_json(exc.code, {"error": exc.message})
        except (ValueError, json.JSONDecodeError):
            self.send_json(400, {"error": "invalid JSON"})
        except Exception:
            self.send_json(500, {"error": "internal error"})


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    server.serve_forever()
