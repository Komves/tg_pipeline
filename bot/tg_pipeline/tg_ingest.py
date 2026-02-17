import asyncio
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from telethon import TelegramClient

try:
    import yaml
except Exception:
    print("ERROR: missing PyYAML. Install: pip install pyyaml")
    sys.exit(2)

# === PATHS (из твоего брифа) ===
PROJECT_DIR = Path(__file__).resolve().parent

CFG_PATH = PROJECT_DIR / "tg_config.yaml"
SESSION_BASE = str(PROJECT_DIR / "meme_session")  # meme_session.session
OUT_ROOT = PROJECT_DIR / "data" / "tg" / "raw" / "MIX"

HOURS = 24


def slug_channel(s: str) -> str:
    s = s.strip()
    s = s.replace("https://", "https_").replace("http://", "http_")
    s = s.replace("/", "_")
    s = re.sub(r"[^0-9A-Za-zА-Яа-я_\-\.]", "_", s)
    return s


def load_cfg() -> dict:
    if not CFG_PATH.exists():
        print(f"ERROR: config not found: {CFG_PATH}")
        sys.exit(2)
    return yaml.safe_load(CFG_PATH.read_text(encoding="utf-8"))


def get_keys(cfg: dict):
    tg = cfg.get("telegram", {})
    api_id = tg.get("api_id")
    api_hash = tg.get("api_hash")

    if not api_id or not api_hash:
        print("ERROR: api_id/api_hash not found in tg_config.yaml")
        sys.exit(2)

    return int(api_id), str(api_hash)


def get_sources(cfg: dict):
    sources = cfg.get("sources", {})
    mix = sources.get("MIX")

    if not mix or not isinstance(mix, list):
        print("ERROR: sources.MIX not found or not list in tg_config.yaml")
        sys.exit(2)

    return [str(x).strip() for x in mix if str(x).strip()]


async def main():
    cfg = load_cfg()
    api_id, api_hash = get_keys(cfg)
    sources = get_sources(cfg)

    print(f"[SESSION] meme_session.session")
    print(f"[CHANNELS] {len(sources)}")

    OUT_ROOT.mkdir(parents=True, exist_ok=True)

    since = datetime.now(timezone.utc) - timedelta(hours=HOURS)

    client = TelegramClient(SESSION_BASE, api_id, api_hash)
    await client.start()

    downloaded = 0

    for src in sources:
        ch_key = slug_channel(src)

        async for msg in client.iter_messages(src):
            if not msg.date:
                continue

            msg_dt = msg.date.replace(tzinfo=timezone.utc)
            if msg_dt < since:
                break

            if not msg.video:
                continue

            day = msg_dt.date().isoformat()
            out_dir = OUT_ROOT / ch_key / day
            out_dir.mkdir(parents=True, exist_ok=True)

            fp = out_dir / f"msg_{msg.id}_video.mp4"
            if fp.exists():
                continue

            await client.download_media(msg.video, file=str(fp))
            downloaded += 1
            print(f"[OK] {ch_key} {day} msg_{msg.id}_video.mp4")

    await client.disconnect()
    print(f"DOWNLOADED: {downloaded}")


if __name__ == "__main__":
    asyncio.run(main())
