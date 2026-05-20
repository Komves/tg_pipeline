from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from telethon import TelegramClient
from telethon.errors import FloodWaitError

import chatgpt_dialog
from beauty_pool import append_beauty_item, load_beauty_pool


REPO_ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

BEAUTY_SOURCES_FILE = REPO_ROOT / "beauty_sources.txt"
BEAUTY_RAW_DIR = DATA_DIR / "beauty_raw"
BEAUTY_RAW_DIR.mkdir(parents=True, exist_ok=True)

BEAUTY_SEEN_INDEX_PATH = DATA_DIR / "beauty_seen_index.json"

_session_env = os.getenv("TG_SESSION", "tg_session").strip()
if _session_env.startswith("/"):
    SESSION_BASE = _session_env
else:
    SESSION_BASE = str(DATA_DIR / _session_env)

API_ID = int(os.environ["TG_API_ID"])
API_HASH = os.environ["TG_API_HASH"]

MAX_SCAN_PER_CHANNEL = int(os.getenv("BEAUTY_MAX_SCAN_PER_CHANNEL", "80"))
MAX_CLASSIFY_PER_RUN = int(os.getenv("BEAUTY_MAX_CLASSIFY_PER_RUN", "20"))


def log(msg: str) -> None:
    now = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[BEAUTY {now}] {msg}", flush=True)


def load_beauty_sources() -> List[str]:
    if not BEAUTY_SOURCES_FILE.exists():
        raise RuntimeError(f"beauty_sources.txt not found: {BEAUTY_SOURCES_FILE}")

    out: List[str] = []

    for line in BEAUTY_SOURCES_FILE.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        out.append(s)

    if not out:
        raise RuntimeError(f"beauty_sources.txt is empty: {BEAUTY_SOURCES_FILE}")

    return out


def safe_channel_dir(src: str) -> Path:
    raw = src.strip()
    h = hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:10]
    name = (
        raw.replace("https://t.me/", "")
        .replace("http://t.me/", "")
        .replace("@", "")
        .replace("+", "invite_")
        .replace("/", "_")
        .replace("\\", "_")
        .strip("_")
    )

    if not name:
        name = "channel"

    p = BEAUTY_RAW_DIR / f"{name}_{h}"
    p.mkdir(parents=True, exist_ok=True)
    return p


def stable_clip_id(src: str, msg_id: int) -> str:
    raw = f"{src}:{int(msg_id)}"
    return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()


def load_seen_index() -> Dict[str, Dict[str, Any]]:
    try:
        if BEAUTY_SEEN_INDEX_PATH.exists():
            data = json.loads(BEAUTY_SEEN_INDEX_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {
                    str(k): v
                    for k, v in data.items()
                    if isinstance(v, dict)
                }
    except Exception:
        pass

    return {}


def save_seen_index(data: Dict[str, Dict[str, Any]]) -> None:
    BEAUTY_SEEN_INDEX_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def mark_seen(clip_id: str, payload: Dict[str, Any]) -> None:
    clip_id = str(clip_id or "").strip()

    if not clip_id:
        return

    data = load_seen_index()
    payload = dict(payload)
    payload.setdefault("seen_ts", int(time.time()))
    data[clip_id] = payload

    if len(data) > 5000:
        items = sorted(
            data.items(),
            key=lambda kv: int((kv[1] or {}).get("seen_ts") or 0),
        )
        data = dict(items[-5000:])

    save_seen_index(data)


def already_in_pool_or_seen(clip_id: str) -> bool:
    clip_id = str(clip_id or "").strip()

    if not clip_id:
        return True

    pool_ids = {
        str(x.get("id") or "").strip()
        for x in load_beauty_pool()
        if str(x.get("id") or "").strip()
    }

    if clip_id in pool_ids:
        return True

    seen = load_seen_index()
    return clip_id in seen


def is_video_message(msg) -> bool:
    if getattr(msg, "video", None):
        return True

    doc = getattr(msg, "document", None)
    mime = str(getattr(doc, "mime_type", "") or "").lower()

    return mime.startswith("video/")


def find_existing_media(channel_dir: Path, msg_id: int) -> Optional[Path]:
    prefix = str(int(msg_id))

    for p in channel_dir.iterdir():
        if not p.is_file():
            continue

        if p.name.endswith(".meta.json"):
            continue

        if p.name == prefix or p.name.startswith(prefix + ".") or p.name.startswith(prefix + " "):
            return p

    return None


async def download_video(client: TelegramClient, src: str, msg, channel_dir: Path) -> Optional[Path]:
    msg_id = getattr(msg, "id", None)

    if msg_id is None:
        return None

    existing = find_existing_media(channel_dir, int(msg_id))
    if existing and existing.exists():
        return existing

    try:
        target = channel_dir / str(int(msg_id))
        saved = await client.download_media(msg.media, file=str(target))

        if not saved:
            return None

        media_path = Path(saved)

        meta = {
            "src": src,
            "msg_id": int(msg_id),
            "tg_date": msg.date.isoformat() if getattr(msg, "date", None) else None,
            "caption": getattr(msg, "message", None),
            "views": int(getattr(msg, "views", 0) or 0),
            "forwards": int(getattr(msg, "forwards", 0) or 0),
            "replies": int(
                getattr(getattr(msg, "replies", None), "replies", 0) or 0
            ),
        }

        Path(str(media_path) + ".meta.json").write_text(
            json.dumps(meta, ensure_ascii=False),
            encoding="utf-8",
        )

        return media_path

    except Exception as e:
        log(f"download error src={src} msg_id={msg_id}: {type(e).__name__}: {e}")
        return None


def extract_video_audio_mp3(video_bytes: bytes) -> bytes:
    if not video_bytes:
        return b""

    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        video_path = d / "input.mp4"
        audio_path = d / "audio.mp3"
        video_path.write_bytes(video_bytes)

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-i", str(video_path),
            "-vn",
            "-ac", "1",
            "-ar", "16000",
            "-t", "120",
            "-f", "mp3",
            str(audio_path),
        ]

        try:
            subprocess.run(cmd, check=False, timeout=45)

            if audio_path.exists() and audio_path.stat().st_size > 0:
                return audio_path.read_bytes()

        except Exception:
            return b""

    return b""


def extract_video_frames(video_bytes: bytes, n: int = 5) -> List[bytes]:
    frames: List[bytes] = []

    if not video_bytes:
        return frames

    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        video_path = d / "input.mp4"
        out_pattern = d / "frame_%03d.jpg"

        video_path.write_bytes(video_bytes)

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-i", str(video_path),
            "-vf", "fps=0.25",
            "-frames:v", str(int(n)),
            "-q:v", "3",
            str(out_pattern),
        ]

        try:
            subprocess.run(cmd, check=False, timeout=45)

            for p in sorted(d.glob("frame_*.jpg")):
                try:
                    frames.append(p.read_bytes())
                except Exception:
                    pass

        except Exception:
            return frames

    return frames


def classify_file(media_path: Path, caption: str = "") -> Dict[str, Any]:
    video_bytes = media_path.read_bytes()

    frames = extract_video_frames(video_bytes, 5)
    audio_mp3 = extract_video_audio_mp3(video_bytes)

    return chatgpt_dialog.classify_beauty_video(
        caption or "",
        frames,
        audio_mp3,
    )


async def collect_beauty_hours(hours: int = 24) -> Dict[str, int]:
    log("START beauty collect")

    sources = load_beauty_sources()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=int(hours))

    client = TelegramClient(SESSION_BASE, API_ID, API_HASH)
    await client.connect()

    stats = {
        "sources": len(sources),
        "scanned": 0,
        "video_seen": 0,
        "already_processed": 0,
        "downloaded_or_found": 0,
        "classified": 0,
        "accepted": 0,
        "rejected": 0,
        "errors": 0,
    }

    try:
        if not await client.is_user_authorized():
            raise RuntimeError(
                "Telethon session is NOT authorized.\n"
                f"Expected session file on disk: {SESSION_BASE}.session"
            )

        classified_this_run = 0

        for src in sources:
            log(f"channel start: {src}")
            channel_dir = safe_channel_dir(src)

            try:
                entity = await client.get_entity(src)
                scanned_channel = 0

                async for msg in client.iter_messages(
                    entity,
                    offset_date=cutoff,
                    reverse=True,
                    limit=MAX_SCAN_PER_CHANNEL,
                ):
                    stats["scanned"] += 1
                    scanned_channel += 1

                    if not is_video_message(msg):
                        continue

                    stats["video_seen"] += 1

                    msg_id = getattr(msg, "id", None)
                    if msg_id is None:
                        continue

                    clip_id = stable_clip_id(src, int(msg_id))

                    if already_in_pool_or_seen(clip_id):
                        stats["already_processed"] += 1
                        continue

                    if classified_this_run >= MAX_CLASSIFY_PER_RUN:
                        log("max classify limit reached")
                        break

                    media_path = await download_video(client, src, msg, channel_dir)

                    if not media_path or not media_path.exists():
                        stats["errors"] += 1
                        mark_seen(
                            clip_id,
                            {
                                "accepted": False,
                                "reason": "download_failed",
                                "src": src,
                                "msg_id": int(msg_id),
                            },
                        )
                        continue

                    stats["downloaded_or_found"] += 1

                    caption = str(getattr(msg, "message", "") or "").strip()

                    try:
                        verdict = classify_file(media_path, caption)
                        stats["classified"] += 1
                        classified_this_run += 1

                    except Exception as e:
                        stats["errors"] += 1
                        mark_seen(
                            clip_id,
                            {
                                "accepted": False,
                                "reason": f"classify_exception: {type(e).__name__}: {e}",
                                "src": src,
                                "msg_id": int(msg_id),
                                "path": str(media_path),
                            },
                        )
                        continue

                    accept = bool(verdict.get("accept"))

                    if accept:
                        item = {
                            "id": clip_id,
                            "path": str(media_path),
                            "src": src,
                            "msg_id": int(msg_id),
                            "tg_date": msg.date.isoformat() if getattr(msg, "date", None) else None,
                            "caption": caption,
                            "beauty_score": float(verdict.get("beauty_score") or 0.0),
                            "erotic_score": float(verdict.get("erotic_score") or 0.0),
                            "has_music": bool(verdict.get("has_music")),
                            "has_speech": bool(verdict.get("has_speech")),
                            "is_ad": bool(verdict.get("is_ad")),
                            "music_track": str(verdict.get("music_track") or ""),
                            "reason": str(verdict.get("reason") or ""),
                            "added_ts": int(time.time()),
                        }

                        added = append_beauty_item(item)

                        mark_seen(
                            clip_id,
                            {
                                "accepted": True,
                                "added": bool(added),
                                "reason": str(verdict.get("reason") or ""),
                                "src": src,
                                "msg_id": int(msg_id),
                                "path": str(media_path),
                            },
                        )

                        if added:
                            stats["accepted"] += 1
                            log(f"ACCEPT msg_id={msg_id} score={item['beauty_score']} path={media_path}")
                        else:
                            stats["already_processed"] += 1

                    else:
                        stats["rejected"] += 1

                        mark_seen(
                            clip_id,
                            {
                                "accepted": False,
                                "reason": str(verdict.get("reason") or "rejected"),
                                "src": src,
                                "msg_id": int(msg_id),
                                "path": str(media_path),
                                "beauty_score": float(verdict.get("beauty_score") or 0.0),
                                "erotic_score": float(verdict.get("erotic_score") or 0.0),
                                "has_music": bool(verdict.get("has_music")),
                                "has_speech": bool(verdict.get("has_speech")),
                                "is_ad": bool(verdict.get("is_ad")),
                            },
                        )

                log(f"channel done: {src} scanned={scanned_channel}")

            except FloodWaitError as e:
                log(f"FloodWait {src}: sleep {e.seconds}s")
                await asyncio.sleep(e.seconds)

            except Exception as e:
                stats["errors"] += 1
                log(f"channel error {src}: {type(e).__name__}: {e}")

        log(f"DONE beauty collect stats={json.dumps(stats, ensure_ascii=False)}")
        return stats

    finally:
        await client.disconnect()
        log("telegram disconnected")


if __name__ == "__main__":
    import sys

    h = 24

    if len(sys.argv) > 1:
        h = int(sys.argv[1])

    asyncio.run(collect_beauty_hours(h))