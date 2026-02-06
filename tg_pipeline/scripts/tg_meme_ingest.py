import os, asyncio
from telethon import TelegramClient, errors
from datetime import datetime, timedelta

API_ID = 36061375
API_HASH = "333af701e5d14db02d4b1040c09a699b"

ROOT = os.path.abspath(".")
MIX = os.path.join(ROOT, "data", "tg", "raw", "MIX")
CFG = os.path.join(ROOT, "out", "config", "a_channels_whitelist.txt")

# используем живую сессию от ingest (видео-ветка)
SESSION = os.path.join(ROOT, "data", "tg", "session", "tg_ingest")  # без .session

MAX_AGE_HOURS = float(os.environ.get("MAX_AGE_HOURS", "24"))

def ensure_dir(p):
    os.makedirs(p, exist_ok=True)

def normalize_channel(s: str) -> str:
    s = s.strip()
    if not s:
        return ""
    s = s.replace("\\", "/")
    s = s.strip()
    # частые формы из имён папок/логов
    for pref in ("https_t.me_", "http_t.me_", "t_me_", "https://t.me/", "http://t.me/", "t.me/"):
        if s.startswith(pref):
            s = s[len(pref):]
    if s.startswith("@"):
        s = s[1:]
    # иногда в whitelist могут быть ссылки целиком
    if "/" in s:
        s = s.split("/")[-1]
    # уберём мусорные хвосты
    s = s.strip().strip("/")
    return s

def load_channels():
    if not os.path.exists(CFG):
        return []
    out = []
    with open(CFG, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            ch = normalize_channel(line)
            if ch:
                out.append(ch)
    return out

async def main():
    ensure_dir(MIX)
    print("SESSION PATH:", SESSION + ".session")
    print("SESSION EXISTS:", os.path.exists(SESSION + ".session"))

    channels = load_channels()
    print("CHANNELS:", len(channels))

    client = TelegramClient(SESSION, API_ID, API_HASH)
    await client.connect()
    print("AUTHORIZED:", await client.is_user_authorized())
    if not await client.is_user_authorized():
        raise RuntimeError("Session is not authorized")

    cutoff = datetime.utcnow() - timedelta(hours=MAX_AGE_HOURS)
    downloaded = 0
    skipped = 0

    for ch in channels:
        print(f"\n== CHANNEL: {ch} ==")
        out_dir = os.path.join(MIX, ch)
        ensure_dir(out_dir)

        try:
            async for msg in client.iter_messages(ch, limit=400):
                if not msg.date:
                    continue
                msg_time = msg.date.replace(tzinfo=None)
                if msg_time < cutoff:
                    break
                if not msg.photo:
                    continue

                fname = f"{msg.id}.jpg"
                path = os.path.join(out_dir, fname)
                if os.path.exists(path):
                    continue

                await msg.download_media(file=path)
                downloaded += 1
                print(f" + {path}")

        except (errors.UsernameNotOccupiedError, errors.UsernameInvalidError):
            print(" ! SKIP: invalid/empty username")
            skipped += 1
        except Exception as e:
            # не валим весь ingest из-за одного канала
            print(" ! ERROR, skip channel:", repr(e))
            skipped += 1

    print(f"\nDONE. Downloaded: {downloaded} | Skipped/Errored channels: {skipped}")
    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
