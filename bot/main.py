# bot/main.py
import os
import json
import asyncio
import hashlib
import random
import requests
from datetime import datetime, timezone, time as dtime
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.enums import ChatAction

import ingest_runner
import nsfw_runner
import ranker
import c_youtube_fetcher
import news_digest

import memory
import persona

try:
    import meme_ranker
except Exception:
    meme_ranker = None

try:
    import b_video_ranker
except Exception:
    b_video_ranker = None


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

C_POSTED_TSV = DATA_DIR / "c_posted_master.tsv"

A_MEMES_LIMIT = int(os.getenv("A_MEMES_LIMIT", "30"))
A_VIDEOS_LIMIT = int(os.getenv("A_VIDEOS_LIMIT", "20"))
B_VIDEOS_LIMIT = int(os.getenv("B_VIDEOS_LIMIT", "5"))

HEARTBEAT_SEC = int(os.getenv("HEARTBEAT_SEC", "300"))

MSK = ZoneInfo("Europe/Moscow")
AUTO_DEADLINE_MSK = dtime(6, 0, 0)

NEWS_HOURS = int(os.getenv("NEWS_HOURS", "12"))
NEWS_LIMIT = int(os.getenv("NEWS_LIMIT", "10"))

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent
NEWS_SOURCES_PRIMARY = _THIS_DIR / "news_sources.txt"
NEWS_SOURCES_FALLBACK = _REPO_ROOT / "tg_pipeline" / "news_sources.txt"
NEWS_SOURCES_FILE = NEWS_SOURCES_PRIMARY if NEWS_SOURCES_PRIMARY.exists() else NEWS_SOURCES_FALLBACK

_run_lock = asyncio.Lock()
router = Router()


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


def _ensure_c_posted_header() -> None:
    if not C_POSTED_TSV.exists():
        C_POSTED_TSV.write_text("ts_utc\tvideo_id\turl\ttitle\tsource\n", encoding="utf-8")


def _load_c_posted_video_ids() -> set[str]:
    if not C_POSTED_TSV.exists():
        return set()
    out: set[str] = set()
    for line in C_POSTED_TSV.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("ts_utc"):
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        vid = (parts[1] or "").strip()
        if vid:
            out.add(vid)
    return out


def _load_c_last_sent_by_source() -> Dict[str, str]:
    if not C_POSTED_TSV.exists():
        return {}
    out: Dict[str, str] = {}
    for line in C_POSTED_TSV.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("ts_utc"):
            continue
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        ts = (parts[0] or "").strip()
        source = (parts[4] or "").strip().lower()
        if not ts or not source:
            continue
        prev = out.get(source)
        if not prev or ts > prev:
            out[source] = ts
    return out


def _mark_c_posted(video_id: str, url: str, title: str, source: str) -> None:
    _ensure_c_posted_header()
    ts = datetime.now(timezone.utc).isoformat()
    video_id = (video_id or "").strip()
    if not video_id:
        return
    url = (url or "").replace("\t", " ").strip()
    title = (title or "").replace("\t", " ").strip()
    source = (source or "").replace("\t", " ").strip().lower()
    with C_POSTED_TSV.open("a", encoding="utf-8") as f:
        f.write(f"{ts}\t{video_id}\t{url}\t{title}\t{source}\n")


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


def _kb_for_sid_a(sid: str):
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Отлично", callback_data=f"fb:good:{sid}")
    kb.button(text="👎 Плохо", callback_data=f"fb:bad:{sid}")
    kb.button(text="⛔️ Бан", callback_data=f"fb:ban:{sid}")
    kb.adjust(3)
    return kb.as_markup()


def _kb_for_sid_c(sid: str):
    kb = InlineKeyboardBuilder()
    kb.button(text="👍 Нравится", callback_data=f"fb:good:{sid}")
    kb.button(text="👎 Не нравится", callback_data=f"fb:bad:{sid}")
    kb.adjust(2)
    return kb.as_markup()


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


def _caption_for_item(_it: Dict[str, Any]) -> Optional[str]:
    return None


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


def _youtube_available(url: str) -> bool:
    """
    Исключаем "Video no longer available": проверка через oEmbed.
    Для удалённых/приватных/недоступных часто будет 404.
    """
    url = (url or "").strip()
    if not url:
        return False
    try:
        oembed = "https://www.youtube.com/oembed"
        r = requests.get(oembed, params={"url": url, "format": "json"}, timeout=6)
        return r.status_code == 200
    except Exception:
        return False


async def _typing_loop(bot: Bot, chat_id: int, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        except Exception:
            pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            pass


async def _answer_with_typing(msg: Message, text: str, min_sec: int = 3, max_sec: int = 7) -> None:
    stop = asyncio.Event()
    task = asyncio.create_task(_typing_loop(msg.bot, msg.chat.id, stop))
    try:
        await asyncio.sleep(0.05)
        await asyncio.sleep(random.randint(min_sec, max_sec))
        await msg.answer(text)
    finally:
        stop.set()
        try:
            await task
        except Exception:
            pass


async def _send_one(bot: Bot, chat_id: str, it: Dict[str, Any], *, with_buttons: bool) -> bool:
    if (it.get("feed") or "").strip() == "c_youtube":
        title = (it.get("title") or "").strip()
        url = (it.get("url") or "").strip()
        video_id = (it.get("video_id") or it.get("item_id") or "").strip()

        if not url or not video_id:
            return False

        sid = None
        reply_markup = None
        if with_buttons:
            sid = _sid("c_youtube", video_id)
            reply_markup = _kb_for_sid_c(sid)

        text_parts = []
        if title:
            text_parts.append(f"🎸 {title}")
        text_parts.append(url)
        text = "\n".join(text_parts).strip()

        try:
            await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
        except Exception as e:
            log(f"send C error: {e}")
            return False

        if with_buttons and sid:
            sent_index = _load_sent_index()
            sent_index[sid] = {
                "sid": sid,
                "feed": "c_youtube",
                "item_id": video_id,
                "abs_path": "",
                "src": url,
            }
            _save_sent_index(sent_index)

        return True

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
        reply_markup = _kb_for_sid_a(sid)

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
    posted_c_video_ids = _load_c_posted_video_ids()

    sent_total = 0

    for it in items:
        feed = (it.get("feed") or "").strip()
        item_id = (it.get("item_id") or "").strip()

        if feed == "a_video" and item_id in posted_a_video:
            continue
        if feed == "a_meme" and item_id in posted_a_meme:
            continue
        if feed == "b_video" and item_id in posted_b_video:
            continue
        if feed == "c_youtube":
            vid = (it.get("video_id") or item_id or "").strip()
            if not vid:
                continue
            if vid in posted_c_video_ids:
                continue

        with_buttons = (feed != "b_video")

        ok = await _send_one(bot, chat_id, it, with_buttons=with_buttons)
        if not ok:
            continue

        if feed == "c_youtube":
            vid = (it.get("video_id") or item_id or "").strip()
            _mark_c_posted(
                vid,
                (it.get("url") or ""),
                (it.get("title") or ""),
                (it.get("source") or ""),
            )
            posted_c_video_ids.add(vid)
        else:
            _mark_posted(user_id, item_id, feed)
            if feed == "a_video":
                posted_a_video.add(item_id)
            elif feed == "a_meme":
                posted_a_meme.add(item_id)
            elif feed == "b_video":
                posted_b_video.add(item_id)

        sent_total += 1

    return sent_total


async def run_all(hours: int, *, reason: str) -> None:
    async with _run_lock:
        log(f"RUN start reason={reason} hours={hours}")

        try:
            log(f"ingest start hours={hours}")
            await ingest_runner.ingest_hours(hours)
            log("ingest done")
        except Exception as e:
            log(f"ingest error: {e}")

        try:
            log("nsfw scoring start")
            nsfw_runner.score_missing_b(hours=hours)
            log("nsfw scoring done")
        except Exception as e:
            log(f"nsfw scoring stop: {e}")

        a_memes = _rank_a_memes(A_MEMES_LIMIT)
        a_videos = _rank_a_videos(A_VIDEOS_LIMIT)
        b_videos = _rank_b_videos(B_VIDEOS_LIMIT)

        log(f"ranked a_memes={len(a_memes)} a_videos={len(a_videos)} b_videos={len(b_videos)}")

        c_limit = 0
        if hours >= 24:
            c_limit = 6
        elif hours >= 12:
            c_limit = 2

        c_items: List[Dict[str, Any]] = []
        try:
            posted_c = _load_c_posted_video_ids()
            last_by_source = _load_c_last_sent_by_source()
            c_items = c_youtube_fetcher.get_batch(
                limit=c_limit,
                posted_video_ids=posted_c,
                last_sent_by_source=last_by_source,
            )
        except Exception as e:
            log(f"C fetch error: {e}")
            c_items = []

        # === FIX Category C: выкидываем недоступные видео (oEmbed 404) ===
        if c_items:
            before = len(c_items)
            filtered = []
            for it in c_items:
                url = (it.get("url") or "").strip()
                if url and _youtube_available(url):
                    filtered.append(it)
            c_items = filtered
            log(f"C availability filter: {before} -> {len(c_items)}")
        # ===============================================================

        log(f"ranked c_youtube={len(c_items)} (limit={c_limit})")

        items = _dedupe_keep_order(a_memes + a_videos + b_videos + c_items)

        bot = Bot(token=BOT_TOKEN)
        try:
            sent = await send_batch(bot, items)
            log(f"send done sent={sent}")
        finally:
            await bot.session.close()

        log("RUN end")


async def run_news(*, hours: int, limit: int, reason: str) -> None:
    async with _run_lock:
        log(f"NEWS start reason={reason} hours={hours} limit={limit}")

        chat_id = _resolve_chat_id()
        if not chat_id:
            log("CHAT_ID missing -> cannot send news")
            return

        bot = Bot(token=BOT_TOKEN)
        try:
            items = await news_digest.get_news_digest(
                news_sources_path=NEWS_SOURCES_FILE,
                hours=hours,
                limit=limit,
            )

            html_text = news_digest.build_html_message(items, hours=hours)
            await bot.send_message(
                chat_id=chat_id,
                text=html_text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )

            if items:
                news_digest.mark_digest_as_seen(items)

            log(f"NEWS done items={len(items)} sources={NEWS_SOURCES_FILE}")
        except Exception as e:
            log(f"NEWS error: {e}")
        finally:
            await bot.session.close()


@router.message(Command("get12"))
async def cmd_get12(msg: Message):
    await msg.answer("Ок. Запускаю прогон за 12 часов (A мемы/видео + B видео + C YouTube=2 ссылки).")
    asyncio.create_task(run_all(12, reason="manual_get12"))


@router.message(Command("news"))
async def cmd_news(msg: Message):
    await msg.answer(f"Ок. Собираю главные новости за последние {NEWS_HOURS} часов (до {NEWS_LIMIT}), только новые.")
    asyncio.create_task(run_news(hours=NEWS_HOURS, limit=NEWS_LIMIT, reason="manual_news"))


# =========================
# PHOTO: Vesya recognition
# =========================
@router.message(F.photo)
async def vesya_photo(msg: Message):
    # в личке — всегда реагирует
    # в группе — реагирует если есть "Веся" в подписи или если это reply на сообщение бота
    cap = (msg.caption or "").strip()
    is_reply_to_bot = (
        msg.reply_to_message
        and msg.reply_to_message.from_user
        and msg.reply_to_message.from_user.id == msg.bot.id
    )
    is_private = (getattr(msg.chat, "type", "") == "private")

    if not is_private and (not persona.is_addressed(cap) and not is_reply_to_bot):
        return

    # скачиваем фото (самое большое)
    try:
        ph = msg.photo[-1]
        file = await msg.bot.get_file(ph.file_id)
        bio = await msg.bot.download_file(file.file_path)
        img_bytes = bio.read() if hasattr(bio, "read") else bytes(bio)
    except Exception as e:
        log(f"photo download error: {e}")
        return

    answer = persona.answer_photo(img_bytes, caption=cap)

    # имитация набора
    await _answer_with_typing(msg, answer, min_sec=2, max_sec=6)


# =========================
# VESYA (chat control)
# =========================
@router.message()
async def vesya_handler(msg: Message):
    text = (msg.text or "").strip()
    if not text:
        return
    if text.startswith("/"):
        return

    ir = persona.detect_intent(text)
    if not getattr(ir, "addressed", False):
        return

    try:
        profiles = memory.load_profiles()
        u = msg.from_user
        prof = memory.ensure_user_profile(
            profiles,
            user_id=(u.id if u else 0),
            display_name=(u.full_name if u else ""),
            username=(u.username if u else ""),
        )
        memory.update_night_owl(prof, hour_local=datetime.now(MSK).hour)
        memory.bump_intent(prof, getattr(ir, "intent", "unclear") or "unclear")
        memory.save_profiles(profiles)

        memory.append_event(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "kind": "msg",
                "uid": (u.id if u else 0),
                "text": text,
                "intent": getattr(ir, "intent", ""),
            }
        )
        memory.prune_memory()
    except Exception:
        pass

    intent = (getattr(ir, "intent", "") or "").strip()

    if intent == "ping":
        d = persona.maybe_delay_seconds_for_ping()
        if d:
            await _answer_with_typing(msg, f"{persona.ping_answer()}. {persona.excuse_text()}.", min_sec=max(2, d), max_sec=max(3, d + 2))
        else:
            await msg.answer(persona.ping_answer())
        return

    if intent == "alive_check":
        if random.random() < 0.60:
            await msg.answer(persona.alive_answer())
        return

    if intent == "bot_q":
        await msg.answer(persona.bot_q_answer())
        return

    if intent == "info_q":
        q = getattr(ir, "question", "") or text
        ans = persona.answer_info_fast(q)
        await _answer_with_typing(msg, ans, min_sec=3, max_sec=8)
        return

    if intent == "unclear":
        await msg.answer(persona.clarify_answer())
        return

    ack = persona.maybe_ack()
    if ack:
        await msg.answer(ack)

    if intent == "news":
        asyncio.create_task(run_news(hours=NEWS_HOURS, limit=NEWS_LIMIT, reason="chat_nl_news"))
        return

    if intent == "music":

        async def _run_music_only():
            async with _run_lock:
                bot = Bot(token=BOT_TOKEN)
                try:
                    posted_c = _load_c_posted_video_ids()
                    last_by_source = _load_c_last_sent_by_source()
                    c_items = c_youtube_fetcher.get_batch(
                        limit=2,
                        posted_video_ids=posted_c,
                        last_sent_by_source=last_by_source,
                    )
                    # фильтр доступности
                    if c_items:
                        c_items = [it for it in c_items if _youtube_available((it.get("url") or "").strip())]
                    if c_items:
                        await send_batch(bot, c_items)
                finally:
                    await bot.session.close()

        asyncio.create_task(_run_music_only())
        return

    # default: обычный ответ через LLM в стиле
    q = getattr(ir, "question", "") or persona.strip_name_prefix(text) or text
    ans = persona.answer_info_fast(q)
    await _answer_with_typing(msg, ans, min_sec=4, max_sec=9)


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

    if payload.get("feed") == "b_video":
        await cb.answer("Ок", show_alert=False)
        return

    user_id = cb.from_user.id if cb.from_user else 0
    _append_feedback(user_id, action, payload)

    if action == "good":
        await cb.answer("Записал ✅", show_alert=False)
    elif action == "bad":
        await cb.answer("Записал 👎", show_alert=False)
    elif action == "ban":
        await cb.answer("Записал ⛔️", show_alert=False)
    else:
        await cb.answer("Записал", show_alert=False)


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


async def main_async():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN missing")

    log("worker started")
    log("manual command: /get12")
    log("manual command: /news")
    log("auto: once per day in MSK 00:00–06:00 window (24h)")
    log(f"limits: A_MEMES={A_MEMES_LIMIT} A_VIDEOS={A_VIDEOS_LIMIT} B_VIDEOS={B_VIDEOS_LIMIT} | C:24h=6 C:12h=2")
    log(f"news: hours={NEWS_HOURS} limit={NEWS_LIMIT} sources={NEWS_SOURCES_FILE}")
    log("buttons: A (3) + C (2), B none")
    log("C fix: youtube oEmbed availability filter = ON")
    log("photo: vesya recognize = ON")
    log("scheduler loop starting")

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
