"""Independent persistent GPT consultations, receipts and one-time invitation codes.

Stores no plaintext invitation codes, proxy passwords, API secrets or bank card numbers.
A separate SQLite database lives on Vesya's persistent /data disk.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import secrets
import sqlite3
import time
from pathlib import Path


def now() -> int:
    return int(time.time())


def code_digest(code: str) -> str:
    return hashlib.sha256(code.strip().upper().encode("ascii")).hexdigest()


class GPTRegistry:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.tx() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS applications (
                    telegram_id INTEGER PRIMARY KEY,
                    stage TEXT NOT NULL DEFAULT 'new',
                    method TEXT,
                    requested_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    notified_at INTEGER,
                    receipt_file_id TEXT,
                    receipt_kind TEXT,
                    receipt_hash TEXT UNIQUE,
                    receipt_txn TEXT UNIQUE,
                    receipt_bank TEXT,
                    receipt_date TEXT,
                    receipt_amount INTEGER,
                    receipt_recipient TEXT,
                    receipt_review TEXT NOT NULL DEFAULT 'none',
                    payment_confirmed INTEGER NOT NULL DEFAULT 0,
                    npd_receipt_issued INTEGER NOT NULL DEFAULT 0,
                    proxy_username TEXT,
                    access_issued_at INTEGER,
                    installer_sent_at INTEGER
                );
                CREATE TABLE IF NOT EXISTS invitation_codes (
                    digest TEXT PRIMARY KEY,
                    applicant_id INTEGER NOT NULL,
                    created_by INTEGER NOT NULL,
                    issued_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    redeemed_at INTEGER,
                    revoked_at INTEGER
                );
                CREATE UNIQUE INDEX IF NOT EXISTS active_gpt_codes
                  ON invitation_codes(applicant_id)
                  WHERE redeemed_at IS NULL AND revoked_at IS NULL;
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts INTEGER NOT NULL,
                    telegram_id INTEGER NOT NULL,
                    event TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT ''
                );
            """)

    @contextmanager
    def tx(self):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=15000")
        db.execute("BEGIN IMMEDIATE")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def get(self, uid: int) -> dict | None:
        with self.tx() as db:
            row = db.execute("SELECT * FROM applications WHERE telegram_id=?", (uid,)).fetchone()
            return dict(row) if row else None

    def register(self, uid: int) -> tuple[dict, bool]:
        with self.tx() as db:
            row = db.execute("SELECT * FROM applications WHERE telegram_id=?", (uid,)).fetchone()
            if row:
                return dict(row), False
            timestamp = now()
            db.execute("INSERT INTO applications (telegram_id, stage, requested_at, updated_at) VALUES (?, 'awaiting_choice', ?, ?)", (uid, timestamp, timestamp))
            db.execute("INSERT INTO audit(ts, telegram_id, event) VALUES (?, ?, 'application_created')", (timestamp, uid))
            return dict(db.execute("SELECT * FROM applications WHERE telegram_id=?", (uid,)).fetchone()), True

    def set_stage(self, uid: int, stage: str):
        allowed = {"awaiting_choice", "awaiting_receipt", "receipt_review", "provisioning", "active", "error"}
        if stage not in allowed:
            raise ValueError("invalid stage")
        with self.tx() as db:
            db.execute("UPDATE applications SET stage=?, updated_at=? WHERE telegram_id=?", (stage, now(), uid))

    def issue_free_code(self, applicant_id: int, admin_id: int) -> str:
        with self.tx() as db:
            app = db.execute("SELECT stage FROM applications WHERE telegram_id=?", (applicant_id,)).fetchone()
            if not app or app["stage"] not in ("awaiting_choice", "awaiting_receipt", "receipt_review"):
                raise ValueError("no pending application")
            ts = now()
            db.execute(
                "UPDATE invitation_codes SET revoked_at=? WHERE applicant_id=? AND redeemed_at IS NULL AND revoked_at IS NULL",
                (ts, applicant_id),
            )
            code = "GPT-" + secrets.token_hex(6).upper() + "-" + secrets.token_hex(6).upper()
            db.execute(
                "INSERT INTO invitation_codes(digest,applicant_id,created_by,issued_at,expires_at) VALUES(?,?,?,?,?)",
                (code_digest(code), applicant_id, admin_id, ts, ts + 7 * 86400),
            )
            db.execute("INSERT INTO audit(ts,telegram_id,event) VALUES(?,?,'free_code_created')", (ts, applicant_id))
            return code

    def redeem_free_code(self, uid: int, code: str) -> bool:
        if not code.startswith("GPT-") or len(code) != 29:
            return False
        with self.tx() as db:
            app = db.execute("SELECT stage FROM applications WHERE telegram_id=?", (uid,)).fetchone()
            if not app or app["stage"] not in ("awaiting_choice", "awaiting_receipt", "receipt_review"):
                return False
            ts = now()
            row = db.execute(
                """UPDATE invitation_codes SET redeemed_at=? WHERE digest=? AND applicant_id=?
                   AND redeemed_at IS NULL AND revoked_at IS NULL AND expires_at>?
                   RETURNING applicant_id""",
                (ts, code_digest(code), uid, ts),
            ).fetchone()
            if not row:
                return False
            db.execute("UPDATE applications SET method='free', stage='provisioning', updated_at=? WHERE telegram_id=?", (ts, uid))
            db.execute("INSERT INTO audit(ts,telegram_id,event) VALUES(?,?,'free_code_redeemed')", (ts, uid))
            return True

    def record_receipt(self, uid: int, *, file_id: str, kind: str, sha256: str, txn: str | None,
                       bank: str, date: str, amount: int, recipient: str, accepted: bool) -> bool:
        with self.tx() as db:
            app = db.execute("SELECT stage FROM applications WHERE telegram_id=?", (uid,)).fetchone()
            if not app or app["stage"] not in {"awaiting_choice", "awaiting_receipt", "receipt_review"}:
                return False
            if db.execute("SELECT 1 FROM applications WHERE receipt_hash=? AND telegram_id!=?", (sha256, uid)).fetchone():
                return False
            if txn and db.execute("SELECT 1 FROM applications WHERE receipt_txn=? AND telegram_id!=?", (txn, uid)).fetchone():
                return False
            timestamp=now()
            db.execute(
                """UPDATE applications SET method='paid',stage=?,receipt_file_id=?,receipt_kind=?,
                  receipt_hash=?,receipt_txn=?,receipt_bank=?,receipt_date=?,receipt_amount=?,
                  receipt_recipient=?,receipt_review=?,updated_at=? WHERE telegram_id=?""",
                ("receipt_review", file_id, kind, sha256,
                 txn, bank[:100], date[:50], amount, recipient[:150],
                 "accepted" if accepted else "manual_review", timestamp, uid),
            )
            db.execute("INSERT INTO audit(ts,telegram_id,event) VALUES(?,?,'receipt_received')", (timestamp, uid))
            return True

    def note_provisioned(self, uid: int, username: str):
        with self.tx() as db:
            db.execute(
                """UPDATE applications SET stage='active',proxy_username=?,access_issued_at=?,
                   updated_at=? WHERE telegram_id=? AND stage='provisioning'""",
                (username, now(), now(), uid),
            )
            db.execute("INSERT INTO audit(ts,telegram_id,event) VALUES(?,?,'access_issued')", (now(), uid))

    def mark_receipt_review_approved(self, uid: int) -> bool:
        with self.tx() as db:
            cur = db.execute(
                """UPDATE applications SET receipt_review='accepted',updated_at=?
                   WHERE telegram_id=? AND stage='receipt_review' AND receipt_file_id IS NOT NULL""",
                (now(),uid),
            )
            return cur.rowcount == 1

    def mark_payment(self, uid: int, *, tax: bool = False) -> bool:
        """Only an explicit owner bank reconciliation may authorize paid access.

        Receipt OCR, even with a matching amount and payee, is never evidence
        of settled funds. NPD receipt recording is a separate later action.
        """
        with self.tx() as db:
            ts = now()
            if tax:
                cur = db.execute(
                    """UPDATE applications SET npd_receipt_issued=1,updated_at=?
                       WHERE telegram_id=? AND method='paid'
                       AND payment_confirmed=1 AND npd_receipt_issued=0""",
                    (ts, uid),
                )
                event = "npd_receipt_marked"
            else:
                cur = db.execute(
                    """UPDATE applications SET payment_confirmed=1,
                       stage=CASE WHEN stage='receipt_review' THEN 'provisioning' ELSE stage END,
                       updated_at=?
                       WHERE telegram_id=? AND method='paid'
                       AND receipt_review='accepted' AND receipt_file_id IS NOT NULL
                       AND stage IN ('receipt_review','active')
                       AND payment_confirmed=0""",
                    (ts, uid),
                )
                event = "bank_payment_confirmed"
            if cur.rowcount:
                db.execute(
                    "INSERT INTO audit(ts,telegram_id,event) VALUES(?,?,?)",
                    (ts,uid,event),
                )
            return cur.rowcount == 1
