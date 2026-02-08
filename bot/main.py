# bot/main.py
import os
import json
import asyncio
import hashlib
from datetime import datetime, timezone, time as dtime
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import ingest_runner
import nsfw_runner
import ranker

try:
    import meme_ranker
except Exception:
    meme_ranker = None

try:
    import b_video_ranker
except Exception:
    b_video_ranker = None

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder


# =========================
# ENV / PATHS
# =========================
BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
CHAT_ID = (os.getenv("CHAT_ID") or "").strip()
ADMIN_USER_IDS = (os.getenv("ADMIN_USER_IDS") or "").strip()

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
RAW_DIR = DATA_DIR / "raw"

POSTED_TSV = DATA_DIR / "a_posted_master.tsv"
FEEDBACK_TSV = DATA_DIR / "feedback.tsv"
SENT_INDEX_JSON = DATA_DIR / "sent_index.json"
STATE_PATH = DATA_DIR / "daily_state.json"

# working limits
A_MEMES_LIMIT = int(os.getenv("A_MEMES_LIMIT", "30"))
A_VIDEOS_LIMIT = int(os.getenv("A_VIDEOS_LIMIT", "20"))
B_VIDEOS_LIMIT = int(os.getenv("B_VIDEOS_LIMIT", "5"))

# heartbeat (NOT a run frequency)
HEARTBEAT_SEC = int(os.getenv("HEARTBEAT_SEC", "300"))  # 5 min

# auto window MSK 00:00–06:00
MSK = ZoneInfo("Europe/Moscow")
AUTO_DEADLINE_MSK = dtime(6, 0, 0)

_run_lock = asyncio.Lock()


# =========================
# LOGGING
# =========================
def log(msg: str) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[main] {now} {msg}", flush=True)


def _resolve_chat_id() -> str:
    if CHAT_ID:
        return CHAT_ID
    if ADMIN_USER_IDS:
        s = ADMIN_USER_IDS.replace(",", " ").split()
        if s:
            return s[0].strip()
    return ""


def _chat_user_id() -> int:
    cid = _resolve_chat_id()
    try:
        return int(cid)
    except Exception:
        return 0


# =========================
# STATE (auto daily 24h once per MSK day)
# =========================
def _load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(d: dict) -> None:
    try:
        STATE_PATH.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        log(f"state write error: {e}")


def _today_msk_str() -> str:
    return datetime.now(MSK).strftime("%Y-%m-%d")


def _in_auto_window_msk() -> bool:
    return datetime.now(MSK).time() <= AUTO_DEADLINE_MSK


def _auto_ran_today() -> bool:
    st = _load_state()
    return st.get("last_auto_msk_day") == _today_msk_str()


def _mark_auto_ran_today() -> None:
    st = _load_state()
    st["last_auto_msk_day"] = _today_msk_str()
    st["last_auto_ts_utc"] = datetime.now(timezone.utc).isoformat()
    _save_state(st)


# =========================
# POSTED (anti-repeat)
# =========================
def _ensure_posted_header() -> None:
    if not POSTED_TSV.exists():
        POSTED_TSV.write_text("timestamp\tuser_id\titem_id\tfeed\n", encoding="utf-8")


def _load_posted(user_id: int, feed: str) -> set[str]:
    if not POSTED_TSV.exists():
        return set()
    out = set()
    for line in POSTED_TSV.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("timestamp"):
            continue
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        _ts, u, item, f = parts[0], parts[1], parts[2], parts[3]
        if str(u) == str(user_id) and f == feed:
            out.add(item)
    return out


def _mark_posted(user_id: int, item_id: str, feed: str) -> None:
    _ensure_posted_header()
    ts = datetime.now(timezone.utc).isoformat()
    with POSTED_TSV.open("a", encoding="utf-8") as f:
        f.write(f"{ts}\t{user_id}\t{item_id}\t{feed}\n")


# =========================
# FEEDBACK (A only)
# =========================
def _load_sent_index() -> Dict[str, Any]:
    if not SENT_INDEX_JSON.exists():
        return {}
    try:
        return json.loads(SENT_INDEX_JSON.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_sent_index(d: Dict[str, Any]) -> None:
    try:
        SENT_INDEX_JSON.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        log(f"sent_index write error: {e}")


def _ensure_feedback_header() -> None:
    if not FEEDBACK_TSV.exists():
        FEEDBACK_TSV.write_text(
            "ts_utc\tuser_id\taction\tfeed\tsid\tsrc\titem_id\tabs_path\n",
            encoding="utf-8",
        )


def _append_feedback(user_id: int, action: str, payload: Dict[str, Any]) -> None:
    _ensure_feedback_header()
    ts = datetime.now(timezone.utc).isoformat()
    feed = payload.get("feed", "")
    sid = payload.get("sid", "")
    src = payload.get("src", "")
    item_id = payload.get("item_id", "")
    abs_path = payload.get("abs_path", "")
    with FEEDBACK_TSV.open("a", encoding="utf-8") as f:
        f.write(f"{ts}\t{user_id}\t{action}\t{feed}\t{sid}\t{src}\t{item_id}\t{abs_path}\n")


def _sid(feed: str, item_id: str) -> str:
    raw = f"{feed}:{item_id}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _kb_for_sid(sid: str):
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Отлично", callback_data=f"fb:good:{sid}")
    kb.button(text="👎 Плохо", callback_data=f"fb:bad:{sid}")
    kb.button(text="⛔️ Бан", callback_data=f"fb:ban:{sid}")
    kb.adjust(3)
    return kb.as_markup()


# =========================
# META / STABLE ID / CAPTION (NO LINKS)
# =========================
def _meta_path(abs_path: str) -> Path:
    return Path(str(abs_path) + ".meta.json")


def _read_meta(abs_path: str) -> Dict[str, Any]:
    mp = _meta_path(abs_path)
    if not mp.exists():
        return {}
    try:
        return json.loads(mp.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _stable_item_id(abs_path: str) -> str:
    """
    Stable id: src#msg_id if present (dedupe 20979.mp4 and 20979 (1).mp4).
    """
    meta = _read_meta(abs_path)
    src = (meta.get("src") or "").strip()
    mid = meta.get("msg_id")
    if src and mid is not None:
        return f"{src}#{mid}"
    try:
        rp = str(Path(abs_path).resolve().relative_to(RAW_DIR.resolve()))
        return rp.replace("\\", "/")
    except Exception:
        return abs_path


def _caption_for_item(it: Dict[str, Any]) -> Optional[str]:
    """
    IMPORTANT: no item_id in caption (to avoid Telegram auto-link under each post).
    """
    abs_path = it.get("abs_path", "")
    meta = _read_meta(abs_path)

    src = (meta.get("src") or it.get("src") or "").strip()
    tg_date = (meta.get("tg_date") or "").strip()
    score = it.get("score", None)

    parts = []
    if src:
        parts.append(f"src: {src}")
    if tg_date:
        parts.append(f"tg_date: {tg_date}")
    if score is not None:
        try:
            parts.append(f"score: {float(score):.4f}")
        except Exception:
            parts.append(f"score: {score}")

    text = "\n".join(parts).strip()
    return text if text else None


# =========================
# NORMALIZE OUTPUTS
# =========================
def _to_item(x: Any, feed: str) -> Optional[Dict[str, Any]]:
    if x is None:
        return None

    if hasattr(x, "abs_path"):
        abs_path = str(getattr(x, "abs_path"))
        item_id = _stable_item_id(abs_path)
        src = str(getattr(x, "src", "") or "")
        score = getattr(x, "score", None)
        return {"feed": feed, "item_id": item_id, "abs_path": abs_path, "src": src, "score": score}

    if isinstance(x, dict):
        abs_path = x.get("abs_path") or x.get("path") or x.get("file")
        if not abs_path:
            return None
        abs_path = str(abs_path)
        item_id = _stable_item_id(abs_path)
        src = str(x.get("src") or x.get("channel") or "")
        score = x.get("score")
        return {"feed": feed, "item_id": item_id, "abs_path": abs_path, "src": src, "score": score}

    if isinstance(x, (str, Path)):
        abs_path = str(x)
        item_id = _stable_item_id(abs_path)
        return {"feed": feed, "item_id": item_id, "abs_path": abs_path, "src": "", "score": None}

    return None


def _dedupe_keep_order(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out: List[Dict[str, Any]] = []
    for it in items:
        key = f"{it.get('feed')}::{it.get('item_id')}"
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


# =========================
# RANKERS
# =========================
def _rank_a_videos(n: int) -> List[Dict[str, Any]]:
    items = ranker.rank_top_n(user_id=0, category=ranker.CAT_A_VIDEO, n=max(0, n), feed="feed_a_video")
    out = []
    for x in items:
        it = _to_item(x, "a_video")
        if it:
            out.append(it)
    return out


def _rank_a_memes(n: int) -> List[Dict[str, Any]]:
    if meme_ranker is None:
        return []

    uid = _chat_user_id()
    out: List[Dict[str, Any]] = []

    try:
        if hasattr(meme_ranker, "rank_memes"):
            cand = meme_ranker.rank_memes(uid, max(0, n))
        elif hasattr(meme_ranker, "rank_top_n"):
            cand = meme_ranker.rank_top_n(uid, max(0, n))
        elif hasattr(meme_ranker, "rank"):
            try:
                cand = meme_ranker.rank(uid, max(0, n))
            except TypeError:
                cand = meme_ranker.rank()
        else:
            cand = []
    except Exception as e:
        log(f"memes rank error: {e}")
        cand = []

    for x in (cand or []):
        it = _to_item(x, "a_meme")
        if it:
            out.append(it)
        if len(out) >= n:
            break
    return out


def _rank_b_videos(n: int) -> List[Dict[str, Any]]:
    if b_video_ranker is None:
        return []

    uid = _chat_user_id()
    out: List[Dict[str, Any]] = []

    try:
        if hasattr(b_video_ranker, "rank_b_videos"):
            try:
                cand = b_video_ranker.rank_b_videos(uid, max(0, n))
            except TypeError:
                cand = b_video_ranker.rank_b_videos()
        elif hasattr(b_video_ranker, "rank_top_n"):
            cand = b_video_ranker.rank_top_n(uid, max(0, n))
        elif hasattr(b_video_ranker, "rank"):
            try:
                cand = b_video_ranker.rank(uid, max(0, n))
            except TypeError:
                cand = b_video_ranker.rank()
        else:
            cand = []
    except Exception as e:
        log(f"B rank error: {e}")
        cand = []

    for x in (cand or []):
        it = _to_item(x, "b_video")
        if it:
            out.append(it)
        if len(out) >= n:
            break
    return out


# =========================
# SENDING
# =========================
async def _send_one(bot: Bot, chat_id: str, it: Dict[str, Any], *, with_buttons: bool) -> bool:
    abs_path = it["abs_path"]
    p = Path(abs_path)
    if not p.exists():
        log(f"send skip missing file: {abs_path}")
        return False

    ext = p.suffix.lower()
    caption = _caption_for_item(it)

    reply_markup = None
    sid = None
    if with_buttons:
        sid = _sid(it["feed"], it["item_id"])
        reply_markup = _kb_for_sid(sid)

    file = FSInputFile(str(p))

    try:
        if ext in {".jpg", ".jpeg", ".png", ".webp"}:
            await bot.send_photo(chat_id=chat_id, photo=file, caption=caption, reply_markup=reply_markup)
        elif ext in {".mp4", ".mov", ".mkv", ".webm", ".m4v"}:
            await bot.send_video(chat_id=chat_id, video=file, caption=caption, reply_markup=reply_markup)
        else:
            await bot.send_document(chat_id=chat_id, document=file, caption=caption, reply_markup=reply_markup)
    except Exception as e:
        log(f"send error: {e}")
        return False

    if with_buttons and sid:
        sent_index = _load_sent_index()
        sent_index[sid] = {
            "sid": sid,
            "feed": it["feed"],
            "item_id": it["item_id"],
            "abs_path": it["abs_path"],
            "src": it.get("src", ""),
        }
        _save_sent_index(sent_index)

    return True


async def send_batch(bot: Bot, items: List[Dict[str, Any]]) -> int:
    chat_id = _resolve_chat_id()
    if not chat_id:
        log("CHAT_ID missing -> cannot send")
        return 0

    user_id = _chat_user_id()

    posted_a_video = _load_posted(user_id, "a_video")
    posted_a_meme = _load_posted(user_id, "a_meme")
    posted_b_video = _load_posted(user_id, "b_video")

    sent_total = 0

    for it in items:
        feed = it["feed"]
        item_id = it["item_id"]

        if feed == "a_video" and item_id in posted_a_video:
            continue
        if feed == "a_meme" and item_id in posted_a_meme:
            continue
        if feed == "b_video" and item_id in posted_b_video:
            continue

        with_buttons = feed in {"a_video", "a_meme"}  # A only (B no buttons)

        ok = await _send_one(bot, chat_id, it, with_buttons=with_buttons)
        if not ok:
            continue

        _mark_posted(user_id, item_id, feed)

        if feed == "a_video":
            posted_a_video.add(item_id)
        elif feed == "a_meme":
            posted_a_meme.add(item_id)
        elif feed == "b_video":
            posted_b_video.add(item_id)

        sent_total += 1

    return sent_total


# =========================
# RUN
# =========================
async def run_all(hours: int, *, reason: str) -> None:
    async with _run_lock:
        log(f"RUN start reason={reason} hours={hours}")

        try:
            log(f"ingest start hours={hours}")
            await ingest_runner.ingest_hours(hours)
            log("ingest done")
        except Exception as e:
            log(f"ingest error: {e}")

        # B scoring: must never block A
        try:
            log("nsfw scoring start")
            nsfw_runner.score_missing_b(hours=hours)
            log("nsfw scoring done")
        except Exception as e:
            log(f"nsfw scoring stop: {e}")

        a_videos = _rank_a_videos(A_VIDEOS_LIMIT)
        a_memes = _rank_a_memes(A_MEMES_LIMIT)
        b_videos = _rank_b_videos(B_VIDEOS_LIMIT)

        log(f"ranked a_videos={len(a_videos)} a_memes={len(a_memes)} b_videos={len(b_videos)}")

        items = _dedupe_keep_order(a_memes + a_videos + b_videos)

        bot = Bot(token=BOT_TOKEN)
        try:
            sent = await send_batch(bot, items)
            log(f"send done sent={sent}")
        finally:
            await bot.session.close()

        log("RUN end")


# =========================
# BOT
# =========================
@router.message(Command("get12"))
async def cmd_get12(msg: Message):
    await msg.answer("Ок. Запускаю прогон за 12 часов (A мемы/видео + B видео).")
    asyncio.create_task(run_all(12, reason="manual_get12"))


@router.callback_query()
async def on_feedback(cb: CallbackQuery):
    data = (cb.data or "").strip()
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "fb":
        await cb.answer("?", show_alert=False)
        return

    action = parts[1]
    sid = parts[2]

    sent_index = _load_sent_index()
    payload = sent_index.get(sid)
    if not payload:
        await cb.answer("Старое/не найдено", show_alert=False)
        return

    if payload.get("feed") not in {"a_video", "a_meme"}:
        await cb.answer("Ок", show_alert=False)
        return

    user_id = cb.from_user.id if cb.from_user else 0
    _append_feedback(user_id, action, payload)

    if action == "good":
        await cb.answer("Записал: Отлично ✅", show_alert=False)
    elif action == "bad":
        await cb.answer("Записал: Плохо 👎", show_alert=False)
    elif action == "ban":
        await cb.answer("Записал: Бан ⛔️", show_alert=False)
    else:
        await cb.answer("Записал", show_alert=False)


# =========================
# SCHEDULER LOOP
# =========================
async def scheduler_loop():
    log("scheduler loop started")
    while True:
        try:
            if _in_auto_window_msk() and not _auto_ran_today():
                log("auto window hit -> starting auto 24h run")
                await run_all(24, reason="auto_daily_24h")
                _mark_auto_ran_today()
            else:
                log("heartbeat")
        except Exception as e:
            log(f"scheduler error: {e}")

        await asyncio.sleep(HEARTBEAT_SEC)


# =========================
# MAIN
# =========================
async def main_async():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN missing")

    log("worker started")
    log("manual command: /get12 (12h for ALL categories: A memes + A videos + B videos)")
    log("auto: once per day in MSK 00:00–06:00 window (24h for ALL categories)")
    log(f"limits: A_MEMES={A_MEMES_LIMIT} A_VIDEOS={A_VIDEOS_LIMIT} B_VIDEOS={B_VIDEOS_LIMIT}")
    log("feedback: A only (memes + videos). B has NO buttons.")

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    await asyncio.gather(
        dp.start_polling(bot),
        scheduler_loop(),
    )


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
