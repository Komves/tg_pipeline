import asyncio
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from telethon import TelegramClient

try:
    import yaml
except Exception:
    print("ERROR: missing PyYAML. Install: pip install pyyaml")
    sys.exit(2)

# ===== CONFIG (reliable mode) =====
PROJECT_DIR = Path(__file__).resolve().parent
CFG_PATH = PROJECT_DIR / "tg_config.yaml"
OUT_ROOT = PROJECT_DIR / "data" / "tg" / "raw" / "MIX"

HOURS = 24
LIMIT_PER_CHANNEL = 400

DOWNLOAD_TIMEOUT_S = 900      # 15 min per file
RETRIES = 3
RETRY_SLEEP_S = 10

HEARTBEAT_SEC = 30
# =================================

VIDEO_EXT_RE = re.compile(r"\.(mp4|mov|mkv|webm|m4v)$", re.IGNORECASE)

stats = {
    "channels_done": 0,
    "channels_total": 0,
    "scanned": 0,
    "downloaded": 0,
    "failed": 0,
    "skipped_channels": 0,
    "last_ok_ts": None,
}

def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

async def heartbeat() -> None:
    while True:
        await asyncio.sleep(HEARTBEAT_SEC)
        if stats["last_ok_ts"] is None:
            last_ok = "-"
        else:
            last_ok = str(int(time.time() - stats["last_ok_ts"])) + "s"

        log(
            "HEARTBEAT | ch "
            + str(stats["channels_done"]) + "/" + str(stats["channels_total"])
            + " | scanned=" + str(stats["scanned"])
            + " | ok=" + str(stats["downloaded"])
            + " | fail=" + str(stats["failed"])
            + " | skip_ch=" + str(stats["skipped_channels"])
            + " | last_ok=" + last_ok
        )

def slug_channel(s: str) -> str:
    s = s.strip()
    s = s.replace("https://", "https_").replace("http://", "http_")
    s = s.replace("/", "_")
    s = re.sub(r"[^0-9A-Za-zА-Яа-я_\-\.]", "_", s)
    return s

def load_cfg() -> dict:
    if not CFG_PATH.exists():
        print("ERROR: config not found: " + str(CFG_PATH))
        sys.exit(2)
    cfg = yaml.safe_load(CFG_PATH.read_text(encoding="utf-8"))
    if not isinstance(cfg, dict):
        print("ERROR: tg_config.yaml invalid")
        sys.exit(2)
    return cfg

def get_keys(cfg: dict):
    tg = cfg.get("telegram", {})
    api_id = tg.get("api_id")
    api_hash = tg.get("api_hash")
    if not api_id or not api_hash:
        print("ERROR: telegram.api_id/api_hash not found in tg_config.yaml")
        sys.exit(2)
    return int(api_id), str(api_hash)

def get_session_base(cfg: dict) -> str:
    # exactly like tg_login_qr.py
    tg = cfg.get("telegram", {})
    session_dir = Path(tg.get("session_dir", "data/tg/session"))
    session_name = str(tg.get("session_name", "tg_ingest"))
    session_path = (PROJECT_DIR / session_dir / session_name).resolve()
    return str(session_path)  # without ".session"

def get_sources(cfg: dict):
    sources = cfg.get("sources", {})
    if not isinstance(sources, dict):
        print("ERROR: sources must be dict in tg_config.yaml")
        sys.exit(2)
    mix = sources.get("MIX")
    if not isinstance(mix, list) or not any(str(x).strip() for x in mix):
        print("ERROR: sources.MIX not found or empty in tg_config.yaml")
        sys.exit(2)
    return [str(x).strip() for x in mix if str(x).strip()]

def pick_ext(msg) -> str:
    name = getattr(getattr(msg, "file", None), "name", None)
    if isinstance(name, str) and "." in name:
        ext = "." + name.split(".")[-1].lower()
        if 2 <= len(ext) <= 6:
            return ext

    mime = getattr(getattr(msg, "file", None), "mime_type", "") or ""
    if "mp4" in mime:
        return ".mp4"
    if "webm" in mime:
        return ".webm"
    if "matroska" in mime:
        return ".mkv"
    if "quicktime" in mime:
        return ".mov"

    return ".mp4"

def is_video_like(msg) -> bool:
    if bool(getattr(msg, "video", None)):
        return True
    if bool(getattr(msg, "document", None)):
        name = getattr(getattr(msg, "file", None), "name", "") or ""
        if VIDEO_EXT_RE.search(name):
            return True
        mime = getattr(getattr(msg, "file", None), "mime_type", "") or ""
        if mime.startswith("video/"):
            return True
    return False

async def download_with_retry(client: TelegramClient, msg, fp: Path) -> bool:
    # already ok
    if fp.exists() and fp.stat().st_size > 0:
        return True

    # cleanup empty file
    if fp.exists() and fp.stat().st_size == 0:
        try:
            fp.unlink()
        except Exception:
            pass

    last_err = None
    for attempt in range(1, RETRIES + 1):
        log("DL_TRY msg=" + str(msg.id) + " attempt=" + str(attempt) + "/" + str(RETRIES))
        try:
            _ = await asyncio.wait_for(client.download_media(msg, file=str(fp)), timeout=DOWNLOAD_TIMEOUT_S)
            if fp.exists() and fp.stat().st_size > 0:
                return True
            last_err = "EMPTY"
        except Exception as e:
            last_err = type(e).__name__

        # cleanup
        if fp.exists() and fp.stat().st_size == 0:
            try:
                fp.unlink()
            except Exception:
                pass

        if attempt < RETRIES:
            log("DL_RETRY_WAIT " + str(RETRY_SLEEP_S) + "s reason=" + str(last_err))
            await asyncio.sleep(RETRY_SLEEP_S)

    log("DL_FAIL msg=" + str(msg.id) + " reason=" + str(last_err))
    return False

async def main():
    cfg = load_cfg()
    api_id, api_hash = get_keys(cfg)
    session_base = get_session_base(cfg)
    sources = get_sources(cfg)

    stats["channels_total"] = len(sources)
    since = datetime.now(timezone.utc) - timedelta(hours=HOURS)

    OUT_ROOT.mkdir(parents=True, exist_ok=True)

    log("[SESSION] " + session_base + ".session")
    log("START | reliable | channels=" + str(len(sources)) + " | since=" + since.isoformat())

    client = TelegramClient(session_base, api_id, api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        log("ERROR: session NOT authorized (run: python tg_login_qr.py)")
        await client.disconnect()
        sys.exit(2)

    hb_task = asyncio.create_task(heartbeat())

    try:
        for src in sources:
            ch_key = slug_channel(src)
            log("CHANNEL_START " + ch_key)

            scanned_local = 0

            try:
                async for msg in client.iter_messages(src, limit=LIMIT_PER_CHANNEL):
                    scanned_local += 1
                    stats["scanned"] += 1

                    if not msg.date:
                        continue
                    dt = msg.date.replace(tzinfo=timezone.utc)
                    if dt < since:
                        break

                    if not is_video_like(msg):
                        continue

                    day = dt.date().isoformat()
                    out_dir = OUT_ROOT / ch_key / day
                    out_dir.mkdir(parents=True, exist_ok=True)

                    ext = pick_ext(msg)
                    fp = out_dir / ("msg_" + str(msg.id) + "_video" + ext)

                    ok = await download_with_retry(client, msg, fp)
                    if ok:
                        stats["downloaded"] += 1
                        stats["last_ok_ts"] = time.time()
                        log("DOWNLOADED " + ch_key + " " + fp.name)
                    else:
                        stats["failed"] += 1
                        log("DL_ERROR " + ch_key + " msg=" + str(msg.id))

            except Exception as e:
                # bad channel / username not found / private / etc.
                stats["skipped_channels"] += 1
                log("CHANNEL_SKIP " + ch_key + " reason=" + type(e).__name__)

            stats["channels_done"] += 1
            log("CHANNEL_DONE " + ch_key + " scanned=" + str(scanned_local))

    finally:
        hb_task.cancel()
        try:
            await client.disconnect()
        except Exception:
            pass

    log(
        "FINISH ok=" + str(stats["downloaded"])
        + " fail=" + str(stats["failed"])
        + " skip_ch=" + str(stats["skipped_channels"])
        + " scanned=" + str(stats["scanned"])
    )

if __name__ == "__main__":
    asyncio.run(main())
