import os
import json
import asyncio
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import FloodWaitError


REPO_ROOT = Path(__file__).resolve().parents[1]

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

RAW_DIR = DATA_DIR / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)
# =========================
# AUTO CLEANUP /data/raw (every N days)
# =========================
RAW_CLEANUP_DAYS = int(os.getenv("RAW_CLEANUP_DAYS", "7"))
RAW_CLEANUP_MARK = DATA_DIR / ".raw_cleanup_ts"

def _maybe_cleanup_raw() -> None:
    try:
        if RAW_CLEANUP_DAYS <= 0:
            return

        now_ts = datetime.now(timezone.utc).timestamp()

        last_ts = 0.0
        if RAW_CLEANUP_MARK.exists():
            try:
                last_ts = float((RAW_CLEANUP_MARK.read_text(encoding="utf-8") or "0").strip() or "0")
            except Exception:
                last_ts = 0.0

        if (now_ts - last_ts) < (RAW_CLEANUP_DAYS * 86400):
            return

        if RAW_DIR.exists():
            shutil.rmtree(RAW_DIR, ignore_errors=True)
        RAW_DIR.mkdir(parents=True, exist_ok=True)

        RAW_CLEANUP_MARK.write_text(str(now_ts), encoding="utf-8")
        log(f"[cleanup] RAW_DIR cleaned (every {RAW_CLEANUP_DAYS} days)")

    except Exception as e:
        log(f"[cleanup] error: {e}")

SOURCES_FILE = REPO_ROOT / "tg_pipeline" / "sources.txt"

_session_env = os.getenv("TG_SESSION", "tg_session").strip()
if _session_env.startswith("/"):
    SESSION_BASE = _session_env
else:
    SESSION_BASE = str(DATA_DIR / _session_env)

API_ID = int(os.environ["TG_API_ID"])
API_HASH = os.environ["TG_API_HASH"]


def log(msg: str):
    now = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[INGEST {now}] {msg}", flush=True)


def load_sources() -> list[str]:
    if not SOURCES_FILE.exists():
        raise RuntimeError(f"sources.txt not found: {SOURCES_FILE}")

    out: list[str] = []
    for line in SOURCES_FILE.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        out.append(s)
    return out


def safe_channel_dir(name: str) -> Path:
    safe = name.strip().replace("@", "").replace("/", "_").replace("\\", "_")
    p = RAW_DIR / safe
    p.mkdir(parents=True, exist_ok=True)
    return p


def cutoff_utc(hours: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=hours)


def write_meta(media_path: Path, src: str, msg) -> None:
    meta_path = Path(str(media_path) + ".meta.json")
    payload = {
        "tg_date": (msg.date.isoformat() if getattr(msg, "date", None) else None),
        "msg_id": getattr(msg, "id", None),
        "src": src,
        "caption": getattr(msg, "message", None),

        # metrics (for ranking)
        "views": int(getattr(msg, "views", 0) or 0),
        "forwards": int(getattr(msg, "forwards", 0) or 0),
        "replies": int(
            getattr(getattr(msg, "replies", None), "replies", 0) or 0
        ),
        "reactions_total": int(
            sum(r.count for r in msg.reactions.results)
            if getattr(msg, "reactions", None)
            and getattr(msg.reactions, "results", None)
            else 0
        ),
    }
    try:
        meta_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        log(f"[meta] error write {meta_path}: {e}")


def _already_downloaded(channel_dir: Path, msg_id: int) -> bool:
    """
    SKIP policy:
      if any file exists that starts with msg_id and is NOT a meta.json -> consider downloaded.
      This prevents Telethon from creating '(2)/(3)' duplicates.
    """
    prefix = f"{msg_id}"
    for p in channel_dir.iterdir():
        if not p.is_file():
            continue
        if p.name.endswith(".meta.json"):
            continue
        # exact match prefix: "20979.mp4", "20979.jpg", "20979 (2).mp4" etc
        if p.name == prefix or p.name.startswith(prefix + ".") or p.name.startswith(prefix + " "):
            return True
    return False


async def download_media(client: TelegramClient, src: str, msg, channel_dir: Path) -> bool:
    if not getattr(msg, "media", None):
        return False

    msg_id = getattr(msg, "id", None)
    if msg_id is None:
        return False

    # SKIP duplicates by msg_id
    if _already_downloaded(channel_dir, int(msg_id)):
        return False

    try:
        # Keep Telethon behavior of adding extension automatically,
        # but because we SKIP if any msg_id.* exists, it won't create (2)/(3).
        target = channel_dir / f"{msg_id}"
        saved = await client.download_media(msg.media, file=str(target))
        if not saved:
            return False

        media_path = Path(saved)
        write_meta(media_path, src, msg)
        return True

    except Exception as e:
        log(f"[media] error src={src} msg_id={msg_id}: {e}")
        return False


async def ingest_hours(hours: int) -> None:
    log("START ingest")
    _maybe_cleanup_raw()

    sources = load_sources()
    cutoff = cutoff_utc(hours)

    log(f"hours={hours}")
    log(f"sources={len(sources)}")
    log("connecting telegram...")

    client = TelegramClient(SESSION_BASE, API_ID, API_HASH)
    await client.connect()
    log("connected telegram")

    try:
        if not await client.is_user_authorized():
            raise RuntimeError(
                "Telethon session is NOT authorized.\n"
                f"Expected session file on disk: {SESSION_BASE}.session\n"
                "Fix: ensure TG_SESSION points to /data/tg_session and that /data contains tg_session.session\n"
            )

        total_downloaded = 0

        for src in sources:
            log(f"channel start: {src}")
            channel_dir = safe_channel_dir(src)

            downloaded = 0
            scanned = 0
            skipped = 0

            try:
                entity = await client.get_entity(src)
                log(f"entity ok: {src}")

                async for msg in client.iter_messages(
                    entity,
                    offset_date=cutoff,
                    reverse=True,
                ):
                    scanned += 1

                    if scanned % 50 == 0:
                        log(f"{src} scanned={scanned} downloaded={downloaded} skipped={skipped}")

                    if not getattr(msg, "media", None):
                        continue

                    msg_id = getattr(msg, "id", None)
                    if msg_id is not None and _already_downloaded(channel_dir, int(msg_id)):
                        skipped += 1
                        continue

                    ok = await download_media(client, src, msg, channel_dir)
                    if ok:
                        downloaded += 1
                        total_downloaded += 1

            except FloodWaitError as e:
                log(f"FloodWait {src}: sleep {e.seconds}s")
                await asyncio.sleep(e.seconds)

            except Exception as e:
                log(f"channel error {src}: {e}")

            log(f"channel done {src} scanned={scanned} downloaded={downloaded} skipped={skipped}")

        log(f"INGEST DONE total_downloaded={total_downloaded}")

    finally:
        await client.disconnect()
        log("telegram disconnected")


if __name__ == "__main__":
    import sys
    h = 24
    if len(sys.argv) > 1:
        h = int(sys.argv[1])
    asyncio.run(ingest_hours(h))
