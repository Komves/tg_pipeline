"""Encrypted GPT proxy password escrow for repeat setup.

Derive an independent AES-GCM key from the existing *server-only* GPT agent
API token. No encryption key or plaintext password is stored in SQLite.
Changing GPT_AGENT_API_TOKEN requires re-encrypting existing credentials first.
"""
from __future__ import annotations

import hashlib
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag

_HEADER = b"GPT1"


class VaultError(ValueError):
    """Cannot safely access the credential vault."""


def _key() -> bytes:
    secret = (os.environ.get("GPT_AGENT_API_TOKEN") or "").strip()
    if len(secret) < 32:
        raise VaultError("Credential encryption key is not configured")
    return hashlib.sha256(b"vesya:gpt:password-escrow:aesgcm:v1\x00" + secret.encode()).digest()


def seal(uid: int, username: str, password: str) -> bytes:
    if not (isinstance(uid, int) and uid > 0 and username.startswith("gpt_") and password):
        raise VaultError("Invalid credentials")
    plaintext = json.dumps(
        {"uid": uid, "username": username, "password": password},
        ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")
    nonce = os.urandom(12)
    ciphertext = AESGCM(_key()).encrypt(nonce, plaintext, str(uid).encode())
    return _HEADER + nonce + ciphertext


def unseal(uid: int, data: bytes) -> tuple[str, str]:
    if not isinstance(data, bytes) or not data.startswith(_HEADER) or len(data) < 36:
        raise VaultError("Invalid credential envelope")
    try:
        raw = AESGCM(_key()).decrypt(
            data[len(_HEADER):len(_HEADER)+12],
            data[len(_HEADER)+12:],
            str(uid).encode(),
        )
        payload = json.loads(raw)
        if payload.get("uid") != uid:
            raise VaultError("Credential belongs to another user")
        username = payload["username"]
        password = payload["password"]
        if not isinstance(username, str) or not username.startswith("gpt_") or not isinstance(password, str) or not password:
            raise VaultError("Invalid decrypted credential")
        return username, password
    except (ValueError, KeyError, TypeError, InvalidTag):
        raise VaultError("Unable to decrypt GPT credentials") from None
