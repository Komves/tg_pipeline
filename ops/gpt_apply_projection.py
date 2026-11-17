#!/usr/bin/env python3
"""Offline GPT-only Squid htpasswd reconciler. No network, shell or service reload.

A separate root-owned systemd oneshot runs this with fixed local paths. The
HTTP agent never gets root or access to /etc/squid-gpt/users.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import stat
import tempfile
from pathlib import Path

MANAGED = re.compile(r"gpt_[0-9a-f]{16}")
LINE = re.compile(r"(gpt_[0-9a-f]{16}):(\$2[aby]\$\d{2}\$[./A-Za-z0-9]{53})")
MAX_PROJECTION_BYTES = 1024 * 1024


def _read_regular(path: Path, max_bytes: int) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > max_bytes:
            raise ValueError("invalid file type or excessive size")
        with os.fdopen(os.dup(fd), "rb") as stream:
            data = stream.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError("file too large")
        return data
    finally:
        os.close(fd)


def _atomic_write(path: Path, data: bytes, *, uid: int, gid: int, mode: int):
    fd, name = tempfile.mkstemp(prefix=".gpt-sync-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.chown(name, uid, gid)
        os.chmod(name, mode)
        os.replace(name, path)
        dirfd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _parse_projection(data: bytes) -> dict[str, str]:
    try:
        decoded = data.decode("ascii")
    except UnicodeError as exc:
        raise ValueError("projection must be ASCII") from exc
    accounts: dict[str, str] = {}
    for line in decoded.splitlines():
        found = LINE.fullmatch(line)
        if not found or found.group(1) in accounts:
            raise ValueError("invalid or duplicate GPT account entry")
        accounts[found.group(1)] = found.group(2)
    return accounts


def _read_manifest(path: Path) -> set[str]:
    if not path.exists():
        return set()
    names = json.loads(_read_regular(path, MAX_PROJECTION_BYTES).decode("ascii"))
    if not isinstance(names, list) or any(
        not isinstance(name, str) or not MANAGED.fullmatch(name)
        for name in names
    ):
        raise ValueError("invalid managed account manifest")
    return set(names)


def _store_manifest(path: Path, names: set[str]):
    payload = (json.dumps(sorted(names), separators=(",", ":")) + "\n").encode("ascii")
    _atomic_write(path, payload, uid=0, gid=0, mode=0o600)


def sync_accounts(projection: Path, target: Path, state_dir: Path) -> bool:
    """Sync exactly the manifest/current projection; preserve unknown accounts.

    Write-ahead inventory prevents orphaned accounts after a crash between the
    root htpasswd update and the final manifest write.
    """
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_fd = os.open(state_dir / ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        desired = _parse_projection(_read_regular(projection, MAX_PROJECTION_BYTES))
        manifest = state_dir / "managed.json"
        previous = _read_manifest(manifest)
        old_data = _read_regular(target, 16 * 1024 * 1024)
        old_text = old_data.decode("ascii")
        st = os.stat(target, follow_symlinks=False)
        if not stat.S_ISREG(st.st_mode):
            raise ValueError("target must be regular")
        for line in old_text.splitlines():
            name = line.partition(":")[0]
            if name in desired and name not in previous:
                raise ValueError("refusing to overwrite pre-existing account")
        owned = previous | set(desired)
        preserved = [
            line for line in old_text.splitlines(keepends=True)
            if line.partition(":")[0] not in owned
        ]
        new_text = "".join(preserved)
        if desired:
            if new_text and not new_text.endswith("\n"):
                new_text += "\n"
            new_text += "".join(
                f"{name}:{hash_value}\n" for name, hash_value in sorted(desired.items())
            )
        # Manifest write-ahead BEFORE touching the live htpasswd file.
        if owned != previous:
            _store_manifest(manifest, owned)
        changed = new_text.encode("ascii") != old_data
        if changed:
            _atomic_write(
                target, new_text.encode("ascii"),
                uid=st.st_uid, gid=st.st_gid, mode=0o640,
            )
        if owned != set(desired):
            _store_manifest(manifest, set(desired))
        return changed
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def main():
    # Fixed production paths: no CLI arguments or user-supplied file names.
    sync_accounts(
        Path("/var/lib/squid-gpt-agent/projection/users"),
        Path("/etc/squid-gpt/users"),
        Path("/var/lib/squid-gpt-sync"),
    )


if __name__ == "__main__":
    main()
