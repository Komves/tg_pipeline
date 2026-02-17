import argparse
import hashlib
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Iterable, Optional, Tuple

# --------- config defaults (MVP) ----------
DEFAULT_EXTS = {".mp4"}
MIN_SIZE_BYTES = 1 * 1024 * 1024  # 1MB
QUICK_N_BYTES = 4 * 1024 * 1024   # 4MB from head + 4MB from tail
CHUNK = 8 * 1024 * 1024           # 8MB streaming for full hash


def now_ts() -> int:
    return int(time.time())


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def sha256_bytes(data: bytes) -> str:
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def quick_hash(path: Path, n_bytes: int = QUICK_N_BYTES) -> str:
    """
    Quick hash = sha256(head N + tail N + size).
    Works well for exact duplicates/reuploads (no re-encode).
    """
    size = path.stat().st_size
    with path.open("rb") as f:
        head = f.read(min(n_bytes, size))
        if size > n_bytes:
            # tail
            try:
                f.seek(max(0, size - n_bytes))
                tail = f.read(min(n_bytes, size))
            except OSError:
                tail = b""
        else:
            tail = b""
    payload = head + tail + str(size).encode("utf-8")
    return sha256_bytes(payload)


def full_hash_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(CHUNK)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def iter_files(root: Path, exts: set[str]) -> Iterable[Path]:
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        # ignore partials / temp
        if p.name.endswith(".part"):
            continue
        if p.suffix.lower() not in exts:
            continue
        yield p


def db_connect(db_path: Path) -> sqlite3.Connection:
    ensure_parent(db_path)
    con = sqlite3.connect(str(db_path))
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA synchronous=NORMAL;")
    return con


def db_init(con: sqlite3.Connection) -> None:
    con.executescript("""
    CREATE TABLE IF NOT EXISTS runs (
        run_id INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at INTEGER NOT NULL,
        finished_at INTEGER,
        scanned_count INTEGER DEFAULT 0,
        new_count INTEGER DEFAULT 0,
        changed_count INTEGER DEFAULT 0,
        hashed_quick_count INTEGER DEFAULT 0,
        hashed_full_count INTEGER DEFAULT 0,
        dup_groups_count INTEGER DEFAULT 0,
        errors_count INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS files (
        path TEXT PRIMARY KEY,
        size INTEGER NOT NULL,
        mtime INTEGER NOT NULL,
        ext TEXT NOT NULL,
        qh TEXT,
        fh TEXT,
        status TEXT NOT NULL DEFAULT 'ok',   -- ok|missing|hash_error
        last_seen_run INTEGER
    );

    CREATE INDEX IF NOT EXISTS idx_files_size ON files(size);
    CREATE INDEX IF NOT EXISTS idx_files_qh ON files(qh);
    CREATE INDEX IF NOT EXISTS idx_files_fh ON files(fh);

    CREATE TABLE IF NOT EXISTS dup_groups (
        group_id TEXT PRIMARY KEY,          -- fh:<hex>
        fh TEXT NOT NULL,
        canonical_path TEXT NOT NULL,
        count INTEGER NOT NULL,
        updated_run INTEGER NOT NULL
    );
    """)
    con.commit()


def db_get_file(con: sqlite3.Connection, path: str) -> Optional[Tuple[int, int, Optional[str], Optional[str], str]]:
    """
    returns (size, mtime, qh, fh, status) or None
    """
    cur = con.execute("SELECT size, mtime, qh, fh, status FROM files WHERE path = ?", (path,))
    row = cur.fetchone()
    return row


def db_upsert_file(con: sqlite3.Connection, *, path: str, size: int, mtime: int, ext: str,
                   qh: Optional[str], fh: Optional[str], status: str, run_id: int) -> None:
    con.execute("""
    INSERT INTO files(path, size, mtime, ext, qh, fh, status, last_seen_run)
    VALUES(?,?,?,?,?,?,?,?)
    ON CONFLICT(path) DO UPDATE SET
        size=excluded.size,
        mtime=excluded.mtime,
        ext=excluded.ext,
        qh=COALESCE(excluded.qh, files.qh),
        fh=COALESCE(excluded.fh, files.fh),
        status=excluded.status,
        last_seen_run=excluded.last_seen_run
    """, (path, size, mtime, ext, qh, fh, status, run_id))


def mark_missing(con: sqlite3.Connection, run_id: int) -> None:
    # any file not seen in this run becomes missing (but we keep history)
    con.execute("""
    UPDATE files
    SET status='missing'
    WHERE last_seen_run IS NOT NULL AND last_seen_run <> ? AND status <> 'missing'
    """, (run_id,))


def choose_canonical(paths: list[str], con: sqlite3.Connection) -> str:
    """
    Canonical = minimal mtime, tie-breaker = shortest path (stable).
    """
    best = None
    for p in paths:
        cur = con.execute("SELECT mtime FROM files WHERE path = ?", (p,))
        row = cur.fetchone()
        if not row:
            continue
        mtime = int(row[0])
        cand = (mtime, len(p), p)
        if best is None or cand < best:
            best = cand
    return best[2] if best else paths[0]


def rebuild_dup_groups(con: sqlite3.Connection, run_id: int) -> int:
    """
    Create/update dup groups for files with same fh and fh not null.
    Group id = 'fh:<hex>'.
    """
    # gather groups
    cur = con.execute("""
    SELECT fh, GROUP_CONCAT(path) as paths, COUNT(*) as cnt
    FROM files
    WHERE status='ok' AND fh IS NOT NULL
    GROUP BY fh
    HAVING cnt >= 2
    """)
    groups = cur.fetchall()

    for fh, paths_csv, cnt in groups:
        paths = paths_csv.split(",") if paths_csv else []
        canonical = choose_canonical(paths, con)
        gid = f"fh:{fh}"
        con.execute("""
        INSERT INTO dup_groups(group_id, fh, canonical_path, count, updated_run)
        VALUES(?,?,?,?,?)
        ON CONFLICT(group_id) DO UPDATE SET
            canonical_path=excluded.canonical_path,
            count=excluded.count,
            updated_run=excluded.updated_run
        """, (gid, fh, canonical, int(cnt), run_id))

    con.commit()
    return len(groups)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="Root directory to scan, e.g. .\\data\\tg\\raw\\MIX")
    ap.add_argument("--db", required=True, help="SQLite DB path, e.g. .\\out\\index\\dedup.sqlite")
    ap.add_argument("--ext", action="append", default=[], help="Extensions (repeatable), default: mp4")
    ap.add_argument("--min-size-mb", type=int, default=1, help="Min file size MB (default 1)")
    ap.add_argument("--quick-mb", type=int, default=4, help="Quick hash head/tail MB (default 4)")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    db_path = Path(args.db).resolve()
    exts = {("." + e.lower().lstrip(".")) for e in args.ext} if args.ext else DEFAULT_EXTS
    min_size = int(args.min_size_mb) * 1024 * 1024
    quick_n = int(args.quick_mb) * 1024 * 1024

    print(f"[dedup] root={root}")
    print(f"[dedup] db={db_path}")
    print(f"[dedup] exts={sorted(exts)} min_size={min_size} quick_n={quick_n}")

    con = db_connect(db_path)
    db_init(con)

    started = now_ts()
    con.execute("INSERT INTO runs(started_at) VALUES(?)", (started,))
    run_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    con.commit()

    scanned = new_count = changed_count = 0
    quick_hashed = full_hashed = 0
    errors = 0

    # ---- scan + update file rows (without hashes yet) ----
    candidates_for_hash: list[Path] = []

    for p in iter_files(root, exts):
        try:
            st = p.stat()
        except OSError:
            continue

        if st.st_size < min_size:
            continue

        scanned += 1
        path_str = str(p)
        size = int(st.st_size)
        mtime = int(st.st_mtime)

        prev = db_get_file(con, path_str)
        if prev is None:
            new_count += 1
            candidates_for_hash.append(p)
        else:
            prev_size, prev_mtime, prev_qh, prev_fh, prev_status = prev
            if prev_size != size or prev_mtime != mtime:
                changed_count += 1
                candidates_for_hash.append(p)

        db_upsert_file(con,
                       path=path_str, size=size, mtime=mtime, ext=p.suffix.lower(),
                       qh=None, fh=None, status="ok", run_id=run_id)

        if scanned % 500 == 0:
            con.commit()
            print(f"[scan] scanned={scanned} new={new_count} changed={changed_count}")

    con.commit()

    # ---- quick hash for new/changed ----
    for p in candidates_for_hash:
        path_str = str(p)
        try:
            qh = quick_hash(p, n_bytes=quick_n)
            quick_hashed += 1
            con.execute("UPDATE files SET qh=?, status='ok' WHERE path=?", (qh, path_str))
        except Exception:
            errors += 1
            con.execute("UPDATE files SET status='hash_error', qh=NULL, fh=NULL WHERE path=?", (path_str,))
        if quick_hashed % 200 == 0:
            con.commit()
            print(f"[qh] done={quick_hashed}/{len(candidates_for_hash)} errors={errors}")
    con.commit()

    # ---- find (size,qh) collisions to full-hash ----
    cur = con.execute("""
    SELECT size, qh
    FROM files
    WHERE status='ok' AND qh IS NOT NULL
    GROUP BY size, qh
    HAVING COUNT(*) >= 2
    """)
    collisions = cur.fetchall()

    # for each collision group, full-hash all members (only those without fh yet)
    for size, qh in collisions:
        cur2 = con.execute("""
        SELECT path, fh
        FROM files
        WHERE status='ok' AND size=? AND qh=?
        """, (int(size), qh))
        rows = cur2.fetchall()
        for path_str, fh in rows:
            if fh is not None:
                continue
            try:
                fh_new = full_hash_sha256(Path(path_str))
                full_hashed += 1
                con.execute("UPDATE files SET fh=?, status='ok' WHERE path=?", (fh_new, path_str))
            except Exception:
                errors += 1
                con.execute("UPDATE files SET status='hash_error', fh=NULL WHERE path=?", (path_str,))
        con.commit()
        print(f"[fh] collision(size={size}) processed, total_full={full_hashed}, errors={errors}")

    # ---- mark missing (not seen this run) ----
    mark_missing(con, run_id)
    con.commit()

    # ---- rebuild dup groups by fh ----
    groups_count = rebuild_dup_groups(con, run_id)

    finished = now_ts()
    con.execute("""
    UPDATE runs
    SET finished_at=?, scanned_count=?, new_count=?, changed_count=?,
        hashed_quick_count=?, hashed_full_count=?, dup_groups_count=?, errors_count=?
    WHERE run_id=?
    """, (finished, scanned, new_count, changed_count,
          quick_hashed, full_hashed, groups_count, errors, run_id))
    con.commit()

    print("\n[done]")
    print(f" run_id={run_id}")
    print(f" scanned={scanned} new={new_count} changed={changed_count}")
    print(f" quick_hashed={quick_hashed} full_hashed={full_hashed}")
    print(f" dup_groups={groups_count} errors={errors}")
    print(f" db={db_path}")

    con.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[abort] Ctrl+C")
        sys.exit(130)
