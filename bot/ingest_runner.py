import os
import json
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import FloodWaitError


REPO_ROOT = Path(__file__).resolve().parents[1]

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

RAW_DIR = DATA_DIR / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)

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

    out = []
    for line in SOURCES_FILE.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            out.append(s)
    return out


def safe_channel_dir(name: str) -> Path:
    safe = name.strip().replace("@", "").replace("/", "_")
    p = RAW_DIR / safe
    p.mkdir(parents=True, exist_ok=True)
    return p


def cutoff_utc(hours: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=hours)


def write_meta(media_path: Path, src: str, msg):
    meta_path = Path(str(media_path) + ".meta.json")
    payload = {
        "tg_date": msg.date.isoformat() if msg.date else None,
        "msg_id": msg.id,
        "src": src,
        "caption": msg.message,
    }
    meta_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


async def download_media(client, src, msg, channel_dir):
    if not msg.media:
        return False
    try:
        target = channel_dir / f"{msg.id}"
        saved = await client.download_media(msg.media, file=str(target))
        if not saved:
            return False
        write_meta(Path(saved), src, msg)
        return True
    except Exception as e:
        log(f"download error {src} msg={msg.id} err={e}")
        return False


async def ingest_hours(hours: int):
    log("START ingest")

    sources = load_sources()
    log(f"sources={len(sources)}")

    cutoff = cutoff_utc(hours)

    client = TelegramClient(SESSION_BASE, API_ID, API_HASH)

    log("connecting telegram...")
    await client.connect()
    log("connected telegram")

    try:
        if not await client.is_user_authorized():
            raise RuntimeError("Telethon session NOT authorized")

        total_downloaded = 0

        for src in sources:
            log(f"channel start: {src}")

            channel_dir = safe_channel_dir(src)
            downloaded = 0
            scanned = 0

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
                        log(f"{src} scanned={scanned} downloaded={downloaded}")

                    if not msg.media:
                        continue

                    ok = await download_media(client, src, msg, channel_dir)

                    if ok:
                        downloaded += 1
                        total_downloaded += 1

            except FloodWaitError as e:
                log(f"FloodWait {src} sleep={e.seconds}s")
                await asyncio.sleep(e.seconds)

            except Exception as e:
                log(f"channel error {src}: {e}")

            log(f"channel done {src} scanned={scanned} downloaded={downloaded}")

        log(f"INGEST DONE total_downloaded={total_downloaded}")

    finally:
        await client.disconnect()
        log("telegram disconnected")


if __name__ == "__main__":
    import sys
    h = int(sys.argv[1]) if len(sys.argv) > 1 else 24
    asyncio.run(ingest_hours(h))
