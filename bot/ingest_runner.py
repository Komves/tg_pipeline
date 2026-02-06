import os
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import FloodWaitError


# ======================
# Paths / repo layout
# ======================
# This file lives at: <repo_root>/bot/ingest_runner.py
# Render Root Directory is set to "bot", but the full repo is still mounted at:
# /opt/render/project/src
# We resolve repo root by going 1 level up from this file.
REPO_ROOT = Path(__file__).resolve().parents[1]

# Persistent disk mount on Render
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

RAW_DIR = DATA_DIR / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)

# sources.txt is in repo (NOT on disk)
SOURCES_FILE = REPO_ROOT / "tg_pipeline" / "sources.txt"


# ======================
# Telethon session
# ======================
# ENV may be:
#   TG_SESSION = "tg_session"  (relative)  -> we force to /data/tg_session
# or
#   TG_SESSION = "/data/tg_session" (absolute)
_session_env = os.getenv("TG_SESSION", "tg_session").strip()
if _session_env.startswith("/"):
    SESSION_BASE = _session_env
else:
    SESSION_BASE = str(DATA_DIR / _session_env)

# Telethon credentials
API_ID = int(os.environ["TG_API_ID"])
API_HASH = os.environ["TG_API_HASH"]


# ======================
# Helpers
# ======================
def load_sources() -> list[str]:
    if not SOURCES_FILE.exists():
        raise RuntimeError(f"sources.txt not found: {SOURCES_FILE}")

    out: list[str] = []
    with SOURCES_FILE.open("r", encoding="utf-8") as f:
        for line in f:
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


async def download_media(client: TelegramClient, msg, channel_dir: Path) -> None:
    if not getattr(msg, "media", None):
        return
    try:
        target = channel_dir / f"{msg.id}"
        await client.download_media(msg.media, file=str(target))
    except Exception as e:
        print(f"[media] error msg_id={getattr(msg, 'id', '?')}: {e}")


# ======================
# Core
# ======================
async def ingest_hours(hours: int) -> None:
    sources = load_sources()
    cutoff = cutoff_utc(hours)

    print(f"[INGEST] hours={hours}")
    print(f"[INGEST] sources_file={SOURCES_FILE} exists={SOURCES_FILE.exists()}")
    print(f"[INGEST] data_dir={DATA_DIR}")
    print(f"[INGEST] raw_dir={RAW_DIR}")
    print(f"[INGEST] session_base={SESSION_BASE}")
    print(f"[INGEST] sources_count={len(sources)}")

    # IMPORTANT: do NOT use `async with ...` because it may call `start()`
    # and try to ask for phone/code (impossible on Render), causing EOFError.
    client = TelegramClient(SESSION_BASE, API_ID, API_HASH)
    await client.connect()

    try:
        authorized = await client.is_user_authorized()
        if not authorized:
            raise RuntimeError(
                "Telethon session is NOT authorized.\n"
                f"Expected session file on disk: {SESSION_BASE}.session\n"
                "Fix: ensure TG_SESSION points to /data/tg_session and that /data contains tg_session.session\n"
            )

        for src in sources:
            channel_dir = safe_channel_dir(src)
            downloaded = 0

            try:
                entity = await client.get_entity(src)

                async for msg in client.iter_messages(
                    entity,
                    offset_date=cutoff,
                    reverse=True,
                ):
                    if not getattr(msg, "media", None):
                        continue
                    await download_media(client, msg, channel_dir)
                    downloaded += 1

            except FloodWaitError as e:
                print(f"[FloodWait] {src}: sleep {e.seconds}s")
                await asyncio.sleep(e.seconds)

            except Exception as e:
                print(f"[ERROR] {src}: {e}")

            print(f"[OK] {src}: downloaded={downloaded}")

    finally:
        await client.disconnect()


# ======================
# CLI (optional)
# ======================
if __name__ == "__main__":
    import sys
    h = 24
    if len(sys.argv) > 1:
        h = int(sys.argv[1])
    asyncio.run(ingest_hours(h))
