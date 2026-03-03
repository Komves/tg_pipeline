# bot/main.py (aiogram canonical sender; Telethon ingest-only via modules)
from __future__ import annotations
import asyncio
import os
import time
import random
import json
import shutil
import uuid
import html
import clip_embedder
from pathlib import Path
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ChatAction
from aiogram.types import FSInputFile
import io
from PIL import Image

from ranker import rank_top_n, CAT_A_VIDEO
from ingest_runner import ingest_hours
from meme_ranker import rank_memes

import c_youtube_fetcher
# deploy trigger
RECENT_MSG_IDS = {}
# last image per (chat_id, user_id) to support "опиши фото" without reply
LAST_USER_IMAGE_ID = {}  # (chat_id:int, user_id:int) -> file_id:str

# =========================
# IMAGE REACTION LIMITER (moderate)
# =========================
IMG_REACT_LAST_TS = {}  # chat_id -> ts
IMG_REACT_COOLDOWN_SEC = int(os.getenv("V_IMG_REACT_COOLDOWN_SEC", "20"))
IMG_REACT_PROB = float(os.getenv("V_IMG_REACT_PROB", "0.45"))

FEEDBACK_PATH = "/data/feedback.tsv"
def _is_banned(item_id: str) -> bool:
    try:
        p = Path(FEEDBACK_PATH)
        if not p.exists():
            return False
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                if "\tban\t" in line and item_id in line:
                    return True
    except Exception:
        pass
    return False

def _load_sent(path: Path) -> set[str]:
    """
    Load "sent" ids from disk.
    Stored as list (order preserved), returned as set for fast membership checks.
    """
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return set(str(x) for x in data if x)
    except Exception:
        pass
    return set()

def _save_sent(path: Path, sent: set[str], *, keep_last: int = 500) -> None:
    """
    Save with stable order:
    - read existing list
    - append new ids in deterministic order (sorted)
    - trim from the left (keep last N)
    """
    try:
        existing = []
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    existing = [str(x) for x in data if x]
            except Exception:
                existing = []

        exist_set = set(existing)

        # deterministic append (so we don't "forget" randomly)
        new_ids = sorted([x for x in sent if x and x not in exist_set])

        merged = existing + new_ids
        if len(merged) > keep_last:
            merged = merged[-keep_last:]

        path.write_text(json.dumps(merged, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

async def _download_tg_file_bytes(bot: Bot, file_id: str) -> bytes:
    """
    Скачать файл Telegram по file_id → bytes
    """
    from io import BytesIO

    tg_file = await bot.get_file(file_id)
    bio = BytesIO()
    await bot.download_file(tg_file.file_path, destination=bio)
    return bio.getvalue()

async def _gpt_meme_ok(abs_path: str, src: str = "") -> bool:
    try:
        p = Path(abs_path)
        if not p.exists():
            return True

        img_bytes = p.read_bytes()

        cap = ""
        mp = Path(str(p) + ".meta.json")
        if mp.exists():
            try:
                meta = json.loads(mp.read_text(encoding="utf-8"))
                cap = (meta.get("caption") or "").strip()
                if not src:
                    src = (meta.get("src") or "").strip()
            except Exception:
                pass

        return bool(chatgpt_dialog.meme_should_send(img_bytes, caption=cap, src=src))
    except Exception:
        return False


TG_LOCK = asyncio.Lock()
from pathlib import Path
from typing import Optional

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.filters import Command

import chatgpt_dialog
import news_digest

from aiogram.enums import ChatAction
from aiogram.types import FSInputFile

from ranker import rank_top_n, CAT_A_VIDEO
from ingest_runner import ingest_hours
from meme_ranker import rank_memes


# =========================
# ENV / CONFIG
# =========================
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "45"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is empty (set Render env var BOT_TOKEN).")

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
# === PRIVATE USERS REGISTRY ===
PRIVATE_USERS_PATH = DATA_DIR / "private_users.json"

def _load_private_users() -> set[int]:
    try:
        if PRIVATE_USERS_PATH.exists():
            return set(json.loads(PRIVATE_USERS_PATH.read_text(encoding="utf-8")))
    except Exception:
        pass
    return set()

def _save_private_users(users: set[int]) -> None:
    try:
        PRIVATE_USERS_PATH.write_text(
            json.dumps(list(users), ensure_ascii=False),
            encoding="utf-8"
        )
    except Exception:
        pass
def _format_morning_quote(text_ru: str) -> str:
    q = html.escape((text_ru or "").strip())
    return f"🌅 <b>Утренняя цитата</b>\n<blockquote>{q}</blockquote>"
# =========================
# IMAGE INBOX / SHRINK (vision economy)
# =========================
IMG_INBOX = DATA_DIR / "img_inbox"
IMG_INBOX.mkdir(parents=True, exist_ok=True)

def _shrink_jpeg_bytes(src_bytes: bytes, max_side: int = 1024, quality: int = 70) -> bytes:
    try:
        im = Image.open(io.BytesIO(src_bytes)).convert("RGB")
        w, h = im.size

        scale = min(1.0, max_side / float(max(w, h)))
        if scale < 1.0:
            im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))))

        out = io.BytesIO()
        im.save(out, format="JPEG", quality=int(quality), optimize=True)
        return out.getvalue()

    except Exception:
        return src_bytes

BASE_DIR = Path(__file__).resolve().parent
NEWS_SOURCES = BASE_DIR / "news_sources.txt"

DEFAULT_NEWS_HOURS = int(os.getenv("NEWS_HOURS", "12"))
DEFAULT_NEWS_LIMIT = int(os.getenv("NEWS_LIMIT", "10"))

# optional: restrict to one chat
_CHAT_ID_ENV = (os.getenv("CHAT_ID") or "").strip()
ALLOWED_CHAT_ID: Optional[int] = int(_CHAT_ID_ENV) if _CHAT_ID_ENV else None

# =========================
# BOT
# =========================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


def _chat_allowed(message: Message) -> bool:
    if ALLOWED_CHAT_ID is None:
        return True
    return int(message.chat.id) == int(ALLOWED_CHAT_ID)


def _log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    print(f"[main] {ts} UTC {msg}", flush=True)
def _img_should_react(chat_id: int) -> bool:
    now = time.time()
    last = float(IMG_REACT_LAST_TS.get(int(chat_id), 0.0))

    # cooldown gate
    if (now - last) < IMG_REACT_COOLDOWN_SEC:
        return False

    # probability gate
    if random.random() > IMG_REACT_PROB:
        return False

    IMG_REACT_LAST_TS[int(chat_id)] = now
    return True


# =========================
# NEWS RUNNER (calls Telethon inside news_digest)
# =========================

async def _run_news_for_message(message: Message, *, hours: int, limit: int) -> None:
    async with TG_LOCK:
        items = await news_digest.get_news_digest(
            news_sources_path=NEWS_SOURCES,
            hours=hours,
            limit=limit,
        )
    try:
        async with TG_LOCK:
            print(f"[news] start hours={hours} limit={limit} sources={NEWS_SOURCES}", flush=True)

            items = await news_digest.get_news_digest(
                news_sources_path=NEWS_SOURCES,
                hours=hours,
                limit=limit,
            )

            print(f"[news] digest_items={len(items)}", flush=True)

            text = news_digest.build_html_message(items, hours=hours)

            # Telegram limit ~4096 chars; keep safe margin
            if len(text) > 3800:
                # уменьшаем количество пунктов, пока не влезет
                shrink = list(items)
                while shrink and len(text) > 3800:
                    shrink = shrink[:-1]
                    text = news_digest.build_html_message(shrink, hours=hours)
                items = shrink

            try:
                await message.answer(text, parse_mode="html")
            except Exception as e:
                # fallback: plain text (на случай битого html)
                print(f"[news] send html failed: {type(e).__name__}: {e}", flush=True)
                plain = text.replace("<b>", "").replace("</b>", "").replace("<blockquote>", "").replace("</blockquote>", "")
                await message.answer(plain)

            try:
                news_digest.mark_digest_as_seen(items)
            except Exception as e:
                print(f"[news] mark_seen failed: {type(e).__name__}: {e}", flush=True)

    except Exception as e:
        # Главное: больше не молчим
        print(f"[news] FAILED: {type(e).__name__}: {e}", flush=True)
        await message.answer("Новости сейчас не отдались (ошибка). Смотри логи [news].")

        text = news_digest.build_html_message(items, hours=hours)
        await message.answer(text, parse_mode="html")
        news_digest.mark_digest_as_seen(items)

# =========================
# COMMANDS
# =========================
@dp.message(Command("news"))
async def cmd_news(message: Message) -> None:
    if not _chat_allowed(message):
        return
    await message.answer("ок. сейчас соберу сводку.")
    await _run_news_for_message(message, hours=DEFAULT_NEWS_HOURS, limit=DEFAULT_NEWS_LIMIT)

async def cmd_get12(message: Message) -> None:
    if not _chat_allowed(message):
        return
    user_id = int(message.from_user.id) if message.from_user else 0
    await _send_content(message, user_id=user_id, ingest_hours_n=None)

# =========================
# MAIN ROUTER
# =========================
async def _send_content(message: Message, *, user_id: int, ingest_hours_n: int | None, send_mode: str = "get12") -> None:
    chat_id = int(message.chat.id)

    # --- Telethon ingest (optional) ---
    if ingest_hours_n is not None:
        async with TG_LOCK:
            try:
                await ingest_hours(int(ingest_hours_n))
            except Exception as e:
                print(f"[content] ingest_hours({ingest_hours_n}) error: {e}", flush=True)

    # --- feedback keyboard ---
    def fb_kb(item_id: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="👍", callback_data=f"fb:up:{item_id}"),
            InlineKeyboardButton(text="👎", callback_data=f"fb:down:{item_id}"),
            InlineKeyboardButton(text="🚫 BAN", callback_data=f"fb:ban:{item_id}"),
        ]])

    # --- videos (consume from 24h pool) ---
    sentv_path = DATA_DIR / f"sent_video_{user_id}.json"
    sentv = _load_sent(sentv_path)

    pool_path = _pool_path("video", user_id)
    pool = _load_json(pool_path, {"ts": 0, "items": []})
    if not _pool_is_fresh(pool):
        pool = _refresh_video_pool(user_id)

    items = list(pool.get("items") or [])

    SEND_V = 4 if send_mode == "get24" else 2
    picked = []
    used_src_v: set[str] = set()
    used_embs: list[list[float]] = []

    def _cos(a: list[float], b: list[float]) -> float:
        # cosine similarity
        import math
        if not a or not b or len(a) != len(b):
            return -1.0
        dot = 0.0
        na = 0.0
        nb = 0.0
        for i in range(len(a)):
            x = float(a[i]); y = float(b[i])
            dot += x * y
            na += x * x
            nb += y * y
        if na <= 0 or nb <= 0:
            return -1.0
        return dot / (math.sqrt(na) * math.sqrt(nb))

    def _get_clip_emb(abs_path: str) -> list[float] | None:
        try:
            mp = Path(abs_path + ".meta.json")
            if not mp.exists():
                return None
            j = json.loads(mp.read_text(encoding="utf-8"))
            emb = j.get("clip_emb")
            if isinstance(emb, list) and emb:
                return [float(v) for v in emb]
        except Exception:
            return None
        return None

    for x in items:
        if len(picked) >= SEND_V:
            break
        item_id = (x.get("item_id") or "").strip()
        abs_path = (x.get("abs_path") or "").strip()
        if (not item_id) or (item_id in sentv):
            continue
        if _is_banned(item_id):
            continue
        if abs_path and (not Path(abs_path).exists()):
            continue
        # diversity: не повторять источник в одной рассылке
        src = (x.get("src") or "").strip()
        if src and src in used_src_v:
            continue

        # CLIP embedding (best-effort): пишет clip_emb в meta.json если может
        # если deps (torch/open_clip) не стоят — просто вернёт False и идём дальше без дедупа
        if abs_path:
            try:
                clip_embedder.ensure_meta_clip_emb(abs_path)
            except Exception:
                pass

        emb = _get_clip_emb(abs_path) if abs_path else None
        if emb:
            # дедуп по визуальной похожести
            too_similar = False
            for e2 in used_embs:
                if _cos(emb, e2) >= float(os.getenv("V_VIDEO_CLIP_SIM_THR", "0.88")):
                    too_similar = True
                    break
            if too_similar:
                continue
        picked.append(x)

        if src:
            used_src_v.add(src)
        if emb:
            used_embs.append(emb)

        actually_sent_ids: set[str] = set()

   
    for x in picked:
        item_id = x.get("item_id") or ""
        abs_path = x.get("abs_path") or ""
        tmp_path = f"/tmp/vesya_video_{uuid.uuid4().hex}.mp4"

        if not item_id or not abs_path:
            continue

        try:
            if os.path.getsize(abs_path) > MAX_UPLOAD_BYTES:
                print(f"[send] skip too large video: {abs_path} size={os.path.getsize(abs_path)}", flush=True)
                continue
        except Exception as e:
            print(f"[send] size check failed video: {abs_path}: {e}", flush=True)
            continue

        try:
            shutil.copyfile(abs_path, tmp_path)
            try:
                await message.answer_video(
                    FSInputFile(tmp_path),
                    reply_markup=fb_kb(item_id),
                    request_timeout=int(os.getenv("V_VIDEO_SEND_TIMEOUT", "180")),
                )
                sentv.add(item_id)
                actually_sent_ids.add(item_id)
            except Exception as e:
                print(f"[send][video] FAILED item_id={item_id} path={abs_path}: {type(e).__name__}: {e}", flush=True)
                continue
        finally:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except Exception:
                pass

    # remove sent from pool + save
    pool["items"] = [x for x in items if (x.get("item_id") not in actually_sent_ids)]
    _save_json(pool_path, pool)

    _save_sent(sentv_path, sentv, keep_last=700)

    sentv_path = DATA_DIR / f"sent_video_{user_id}.json"
    sentv = _load_sent(sentv_path)


    # --- мемы (consume from 24h pool + GPT batch ranking) ---
    sentm_path = DATA_DIR / f"sent_meme_{user_id}.json"
    sentm = _load_sent(sentm_path)

    pool_path = _pool_path("meme", user_id)
    pool = _refresh_meme_pool(user_id)

    items = list(pool.get("items") or [])

    POOL_N = int(os.getenv("V_MEME_POOL_N", "30"))   # сколько показать GPT за раз
    SEND_K = 8 if send_mode == "get24" else 4        # сколько отправить пользователю
    def _size_ok(x: dict) -> bool:
        abs_path = (x.get("abs_path") or "").strip()
        if not abs_path:
            return False
        try:
            return os.path.getsize(abs_path) <= MAX_UPLOAD_BYTES
        except Exception:
            return False

    def _mk_batch(cand_items: list[dict]) -> list[chatgpt_dialog.MemeCandidate]:
        out: list[chatgpt_dialog.MemeCandidate] = []
        for x in cand_items:
            try:
                p = Path(x.get("abs_path") or "")
                if not p.exists():
                    continue
                suf = p.suffix.lower()
                if suf not in {".jpg", ".jpeg", ".png", ".webp"}:
                    continue
                if p.stat().st_size < 5000:
                    continue
                img_bytes = p.read_bytes()
                out.append(
                    chatgpt_dialog.MemeCandidate(
                        item_id=(x.get("item_id") or "").strip(),
                        img_bytes=img_bytes,
                        caption=(x.get("caption") or "").strip(),
                        src=(x.get("src") or "").strip(),
                    )
                )
            except Exception:
                continue
        return out

    cand = []
    for x in items:
        if len(cand) >= POOL_N:
            break
        item_id = (x.get("item_id") or "").strip()
        abs_path = (x.get("abs_path") or "").strip()
        if (not item_id) or (item_id in sentm):
            continue
        if _is_banned(item_id):
            continue
        if abs_path and (not Path(abs_path).exists()):
            continue

        # MEME candidates must be images only (skip mp4 etc.)
        if abs_path:
            suf = Path(abs_path).suffix.lower()
            if suf not in {".jpg", ".jpeg", ".png", ".webp"}:
                continue

        # hard pre-filter before GPT ranking (NSFW / personal / ads / trash)
        if abs_path:
            try:
                src = (x.get("src") or "").strip()
                ok = await _gpt_meme_ok(abs_path, src=src)
                if not ok:
                    continue
            except Exception:
                continue

        cand.append(x)
    print(f"[meme_pool] cand={len(cand)} pool_items={len(items)}", flush=True)

    batch: list[chatgpt_dialog.MemeCandidate] = []
    for x in cand:
        try:
            p = Path(x.get("abs_path") or "")

            if (not p.exists()):
                continue

            suf = p.suffix.lower()
            if suf not in {".jpg", ".jpeg", ".png", ".webp"}:
                continue

            # защита от mp4 под видом jpg
            if p.stat().st_size < 5000:
                continue

            img_bytes = p.read_bytes()

            batch.append(
                chatgpt_dialog.MemeCandidate(
                    item_id=x.get("item_id") or "",
                    img_bytes=img_bytes,
                    caption=(x.get("caption") or "").strip(),
                    src=(x.get("src") or "").strip(),
                )
            )
        except Exception:
            pass

    # -------------------------
    # GPT RANK WITH GPT FILL-UP (NO NON-GPT FALLBACK)
    # -------------------------

    # GPT should not waste picks on too-large files -> prefilter by size for ranking pool
    cand_rankable = [x for x in cand if _size_ok(x)]
    print(f"[meme_pool] rankable={len(cand_rankable)} (size_ok) out of cand={len(cand)}", flush=True)

    picked_ids: list[str] = []
    picked_set: set[str] = set()

    # -------------------------
    # CASCADE GPT BATCHES (18 → next 18 → ...)
    # -------------------------

    batch_size = 18
    picked_ids: list[str] = []
    picked_set: set[str] = set()

    start = 0
    total = len(cand_rankable)

    PICK_BUFFER = int(os.getenv("V_MEME_PICK_BUFFER", "6"))  # запас на скипы при отправке
    target_pick = SEND_K + PICK_BUFFER

    # map for send phase
    id2 = {
        (x.get("item_id") or "").strip(): x
        for x in cand_rankable
        if (x.get("item_id") or "").strip()
    }

    while start < total and len(picked_ids) < target_pick:
        slice_items = cand_rankable[start:start + batch_size]

        if not slice_items:
            break

        batch = _mk_batch(slice_items)
        if not batch:
            start += batch_size
            continue

        need = min(batch_size, target_pick - len(picked_ids))

        r = chatgpt_dialog.meme_rank_batch(batch, top_k=need)
        new_ids = list((r or {}).get("picked_item_ids") or [])

        # очистка
        new_ids = [
            pid for pid in new_ids
            if pid and pid not in picked_set and pid not in sentm
        ]

        for pid in new_ids:
            picked_ids.append(pid)
            picked_set.add(pid)

        print(
            f"[MEME_GPT_CASCADE] slice={start}-{start+batch_size} "
            f"picked_now={len(new_ids)} total={len(picked_ids)}",
            flush=True
        )

        start += batch_size

    # 3) send up to SEND_K реально отправленных
    actually_sent_ids: set[str] = set()
    sent_count = 0

    used_src: set[str] = set()

    for pid in picked_ids:
        if sent_count >= SEND_K:
            break

        x = id2.get(pid)
        if not x:
            continue
        src = (x.get("src") or "").strip()
        if src and src in used_src:
            continue

        item_id = (x.get("item_id") or "").strip()
        abs_path = (x.get("abs_path") or "").strip()
        if not item_id or item_id in sentm:
            continue

        # (size already ok in cand_rankable, but keep extra safety)
        try:
            if os.path.getsize(abs_path) > MAX_UPLOAD_BYTES:
                print(f"[send] skip too large meme (post-rank): {abs_path} size={os.path.getsize(abs_path)}", flush=True)
                continue
        except Exception as e:
            print(f"[send] size check failed meme: {abs_path}: {e}", flush=True)
            continue

        await message.answer_photo(
            FSInputFile(abs_path),
            reply_markup=fb_kb(item_id),
        )
        sentm.add(item_id)
        actually_sent_ids.add(item_id)
        sent_count += 1

        if src:
            used_src.add(src)

    print(f"[MEME_SEND] want={SEND_K} sent={sent_count} picked_total={len(picked_ids)} cand={len(cand)} rankable={len(cand_rankable)}", flush=True)

    # 4) remove from pool ONLY реально отправленные
    pool["items"] = [x for x in items if (x.get("item_id") not in actually_sent_ids)]
    _save_json(pool_path, pool)

    _save_sent(sentm_path, sentm, keep_last=700)

    # --- youtube links ---
    try:
        def yt_kb(item_id: str) -> InlineKeyboardMarkup:
            return InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="👍", callback_data=f"fb:up:{item_id}"),
                InlineKeyboardButton(text="👎", callback_data=f"fb:down:{item_id}"),
                InlineKeyboardButton(text="🚫 BAN", callback_data=f"fb:ban:{item_id}"),
            ]])

        sentyt_path = DATA_DIR / f"sent_yt_{user_id}.json"
        sentyt = _load_sent(sentyt_path)
        SEND_YT = 2 if send_mode == "get24" else 1
        yt_sent = 0

        ytstate_path = DATA_DIR / f"yt_state_{user_id}.json"
        try:
            _st = json.loads(ytstate_path.read_text(encoding="utf-8")) if ytstate_path.exists() else {}
        except Exception:
            _st = {}

        posted_ids = set(_st.get("posted_video_ids") or [])
        last_sent_by_source = dict(_st.get("last_sent_by_source") or {})
        last_sent_by_channel = dict(_st.get("last_sent_by_channel") or {})
        now_ts = int(time.time())
        CHANNEL_COOLDOWN_SEC = int(os.getenv("V_YT_CHANNEL_COOLDOWN_DAYS", "7")) * 86400

        pool = c_youtube_fetcher.get_batch(
            limit=6,
            posted_video_ids=posted_ids,
            last_sent_by_channel=last_sent_by_channel,
            channel_cooldown_sec=CHANNEL_COOLDOWN_SEC,
            mode="mix",
        )

        print(f"[yt] pool size={len(pool)}", flush=True)

        for x in pool:
            title = (x.get("title") or "").strip()
            url = (x.get("url") or "").strip()
            vid = (x.get("video_id") or "").strip()

            if not url or url in sentyt:
                continue

            item_id = f"yt:{url}"

            if _is_banned(item_id):
                continue

            text2 = f"🎵 {title}\n{url}" if title else url

            cid = (x.get("channel_id") or "").strip()
            if not cid:
                continue
            if cid:
                last_ts = int(last_sent_by_channel.get(cid) or 0)
                if now_ts - last_ts < CHANNEL_COOLDOWN_SEC:
                    continue

            await message.answer(text2, reply_markup=yt_kb(item_id))

            # фиксируем отправку СРАЗУ (иначе при SEND_YT=1 break съедает запись state)
            sentyt.add(url)

            if vid:
                posted_ids.add(vid)
                last_sent_by_source["c_youtube"] = vid

            if cid:
                last_sent_by_channel[cid] = now_ts

            yt_sent += 1
            if yt_sent >= SEND_YT:
                break

        _save_sent(sentyt_path, sentyt, keep_last=800)

        ytstate_path.write_text(
             json.dumps({
                 "posted_video_ids": list(posted_ids)[-5000:],
                 "last_sent_by_source": last_sent_by_source,
                 "last_sent_by_channel": last_sent_by_channel,
             }),

            encoding="utf-8",
        )

    except Exception as e:
       print(f"[content] youtube error: {e}", flush=True)


@dp.message(F.photo)
async def on_photo(message: Message) -> None:
    print("[IMG] photo handler triggered", flush=True)

    if not _chat_allowed(message):
        return

    try:
        ph = message.photo[-1]
        # save last photo id even if limiter skips reactions
        uid = int(message.from_user.id) if message.from_user else 0
        LAST_USER_IMAGE_ID[(int(message.chat.id), uid)] = ph.file_id

        raw = await _download_tg_file_bytes(message.bot, ph.file_id)
        img_bytes = _shrink_jpeg_bytes(raw)

        chatgpt_dialog.note_last_user_photo(
            int(message.chat.id),
            int(message.from_user.id) if message.from_user else 0,
            img_bytes,
        )

        fn = IMG_INBOX / f"{message.chat.id}_{message.message_id}.jpg"

        try:
            fn.write_bytes(img_bytes)
        except Exception:
            pass
        # In groups: react rarely (cooldown + probability)
        if message.chat.type in ("group", "supergroup"):
            if not _img_should_react(int(message.chat.id)):
                return
        res = chatgpt_dialog.image_react(
            chat_id=int(message.chat.id),
            user_id=int(message.from_user.id) if message.from_user else 0,
            caption=(message.caption or ""),
            img_bytes=img_bytes,
        )

        if not res:
            return

        action = (res.get("action") or "skip").lower()

        reply = (res.get("reply") or "").strip()
        kind = (res.get("kind") or "photo").lower()
        # In groups: for non-meme photos do only "like" (no comments)
        if message.chat.type in ("group", "supergroup") and kind != "meme":
            if action == "comment":
                action = "like"
        if kind == "meme" and action != "skip" and (not _img_should_react(int(message.chat.id))):
            print("[IMG] meme skipped by limiter", flush=True)
            return

        if action == "like":
            await message.reply(reply or "🔥")

        elif action == "comment":
            await message.reply(reply or "норм")

    except Exception as e:
        print(f"[img] photo error: {e}", flush=True)

@dp.message(F.document)
async def on_image_document(message: Message) -> None:

    if not _chat_allowed(message):
        return
    try:
        doc = message.document

        if not doc.mime_type.startswith("image/"):
            return

        raw = await _download_tg_file_bytes(message.bot, doc.file_id)

        img_bytes = _shrink_jpeg_bytes(raw)

        # save last image-document for "опиши фото"
        uid = int(message.from_user.id) if message.from_user else 0
        LAST_USER_IMAGE_ID[(int(message.chat.id), uid)] = doc.file_id

        chatgpt_dialog.note_last_user_photo(
            int(message.chat.id),
            int(message.from_user.id) if message.from_user else 0,
            img_bytes,
        )

        # In groups: react rarely (cooldown + probability)
        if message.chat.type in ("group", "supergroup"):
            if not _img_should_react(int(message.chat.id)):
                return
        res = chatgpt_dialog.image_react(
            chat_id=int(message.chat.id),
            user_id=int(message.from_user.id) if message.from_user else 0,
            caption=(message.caption or ""),
            img_bytes=img_bytes,
        )

        if not res:
            return

        action = (res.get("action") or "skip").lower()

        reply = (res.get("reply") or "").strip()
        kind = (res.get("kind") or "photo").lower()
        # In groups: for non-meme images do only "like" (no comments)
        if message.chat.type in ("group", "supergroup") and kind != "meme":
            if action == "comment":
                action = "like"
        if kind == "meme" and action != "skip" and (not _img_should_react(int(message.chat.id))):
            print("[IMG] meme skipped by limiter", flush=True)
            return
        if action == "like":
            await message.reply(reply or "👍")

        elif action == "comment":
            await message.reply(reply or "ок")

    except Exception as e:
        print(f"[img] doc error: {e}", flush=True)

@dp.message(F.text)
async def vesya_handler(message: Message) -> None:
    print(f"[DEBUG] msg_id={message.message_id} chat_id={message.chat.id} from={message.from_user.id if message.from_user else 0}", flush=True)

    now = time.time()
    k = (int(message.chat.id), int(message.message_id))

    for kk, ts in list(RECENT_MSG_IDS.items()):
        if now - ts > 60:
            RECENT_MSG_IDS.pop(kk, None)
    if k in RECENT_MSG_IDS:
        print(f"[DEBUG] DUPLICATE msg_id={message.message_id} skipped", flush=True)
        return
    RECENT_MSG_IDS[k] = now

    if not _chat_allowed(message):
        return
    # === SAVE PRIVATE USERS ===
    if message.chat.type == "private" and message.from_user:
        users = _load_private_users()
        users.add(int(message.from_user.id))
        _save_private_users(users)

    text = (message.text or "").strip()
    orig_text = text  # keep original text for routing

    # In groups: react only when bot is addressed (name/command/reply)
    if message.chat.type in ("group", "supergroup"):
        t = text.lower()

        is_cmd = t.startswith("/")
        is_name = chatgpt_dialog.persona.is_addressed(t)
        is_reply_to_bot = (
            message.reply_to_message
            and message.reply_to_message.from_user
            and message.reply_to_message.from_user.is_bot
        )

        if not (is_cmd or is_name or is_reply_to_bot):
            return

        # optional: remove name prefix "Веся, ..."
        if is_name and not is_cmd:
            text = chatgpt_dialog.persona.strip_name_prefix(text).lstrip(" ,:.-").strip()
            if not text:
                await message.answer("да?")
                return

    print(f"[route] text={text!r}", flush=True)

    if not text:
        return

    # =========================
    # PHOTO CONTEXT: describe only when user asked
    # =========================
    try:
        img_bytes = None

        # detect "user asked to describe photo"
        t = text.lower()
        wants_photo = (
            "что на фото" in t
            or "что ты видишь" in t
            or t.startswith("опиши фото")
            or t.startswith("опиши картин")
            or t.startswith("опиши изображ")
            or "опиши фото" in t
        )

        if wants_photo:
            # 1) If user replied to a photo/document → use that
            r = message.reply_to_message
            if r:
                img_id = None
                if getattr(r, "photo", None):
                    img_id = r.photo[-1].file_id
                elif getattr(r, "document", None) and (getattr(r.document, "mime_type", "") or "").startswith("image/"):
                    img_id = r.document.file_id

                if img_id:
                    raw = await _download_tg_file_bytes(message.bot, img_id)
                    img_bytes = _shrink_jpeg_bytes(raw)

            # 2) If still none → use last saved photo for this user
            if img_bytes is None:
                uid = int(message.from_user.id) if message.from_user else 0
                fid = LAST_USER_IMAGE_ID.get((int(message.chat.id), uid))
                if fid:
                    raw = await _download_tg_file_bytes(message.bot, fid)
                    img_bytes = _shrink_jpeg_bytes(raw)

        # If we have image bytes → call vision describe
        if img_bytes is not None:
            dd = chatgpt_dialog.describe_or_compare_photo(text, img_bytes)
            if dd and (dd.reply or "").strip():
                await message.answer(dd.reply)
                return

    except Exception as e:
        print(f"[IMG] photo describe routing error: {type(e).__name__}: {e}", flush=True)    
    chat_id = int(message.chat.id)
    user_id = int(message.from_user.id) if message.from_user else 0

    reply = ""
    intent = "chat"
    decision = chatgpt_dialog.decide(chat_id, user_id, orig_text)
    print(f"[route] intent={decision.intent} reply={decision.reply!r}", flush=True)
    intent = (decision.intent or "chat").strip().lower()
    reply = (decision.reply or "").strip()

    if intent == "chat":
        # имитация "печатает..." + пауза 5–10 секунд
        wait_s = random.uniform(5, 10)

        # Telegram "typing" живёт недолго, поэтому поддерживаем его до отправки
        end_at = time.time() + wait_s
        while time.time() < end_at:
            await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
            await asyncio.sleep(min(4.0, end_at - time.time()))

        await message.answer(reply or "слушаю")
        return

    if intent == "news":
        await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

        if reply:
            await message.answer(reply)
        else:
            await message.answer("ок. сейчас соберу сводку.")

        await _run_news_for_message(message, hours=DEFAULT_NEWS_HOURS, limit=DEFAULT_NEWS_LIMIT)
        return

    if intent == "content":
        await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

        if reply:
            await message.answer(reply)
        else:
            await message.answer("сек, собираю горячее.")

        await _send_content(message, user_id=user_id, ingest_hours_n=None)
        return

        # --- мемы ---
        sentm_path = DATA_DIR / f"sent_meme_{user_id}.json"
        sentm = _load_sent(sentm_path)

        m_items = rank_memes(user_id=user_id, n=10)
        m_items = [it for it in m_items if it.item_id not in sentm]

        m_ok = []
        for it in m_items:
            ok = await _gpt_meme_ok(it.abs_path, src=getattr(it, "src", "") or "")
            if ok:
                m_ok.append(it)
        m_items = m_ok

        for it in m_items:
            await message.answer_photo(
                FSInputFile(it.abs_path),
                reply_markup=fb_kb(it.item_id),
            )
            sentm.add(it.item_id)

        _save_sent(sentm_path, sentm, keep_last=700)


# =====================
# INGEST24 LOOP (06:00 MSK)
# =====================

from datetime import datetime, timedelta, timezone

# =========================
# POOLS (24h): memes + videos
# =========================
POOL_TTL_SEC = int(os.getenv("V_POOL_TTL_SEC", str(24 * 3600)))

def _pool_path(kind: str, user_id: int) -> Path:
    return DATA_DIR / f"{kind}_pool_{int(user_id)}.json"

def _pool_raw_path(kind: str) -> Path:
    d = datetime.now(timezone.utc).strftime("%Y%m%d")
    return DATA_DIR / f"{kind}_pool_raw_{d}.json"

def _load_json(path: Path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default

def _save_json(path: Path, data) -> None:
    try:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        pass

def _pool_is_fresh(pool: dict) -> bool:
    try:
        ts = float(pool.get("ts") or 0)
        return (time.time() - ts) < POOL_TTL_SEC
    except Exception:
        return False
def _refresh_video_pool(user_id: int) -> dict:
    COLLECT_N = int(os.getenv("V_VIDEO_COLLECT_N", "80"))

    # scan raw files directly (rank_top_n currently returns 0)
    # build item_id as: {src}/{msg_id}.mp4  (same as old sent_video ids)
    raw_dir = DATA_DIR / "raw"
    exts = {".mp4", ".webm", ".mov", ".mkv"}

    files = []
    try:
        for p in raw_dir.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix.lower() in exts:
                files.append(p)
    except Exception:
        files = []

    # newest first
    try:
        files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    except Exception:
        pass

    out = []
    seen = set()
    for p in files:
        if len(out) >= COLLECT_N:
            break

        meta_path = Path(str(p) + ".meta.json")
        src = ""
        msg_id = None
        views = 0
        forwards = 0
        replies = 0
        reactions_total = 0
        score = 0.0

        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                src = (meta.get("src") or "").strip()
                msg_id = meta.get("msg_id")

                views = int(meta.get("views", 0) or 0)
                forwards = int(meta.get("forwards", 0) or 0)
                replies = int(meta.get("replies", 0) or 0)
                reactions_total = int(meta.get("reactions_total", 0) or 0)

                # simple quality score
                score = (views * 0.15) + (forwards * 4.0) + (replies * 3.0) + (reactions_total * 5.0)
            except Exception:
                src = ""
                msg_id = None
                views = forwards = replies = reactions_total = 0
                score = 0.0

        # fallback: if meta missing, skip (better than wrong ids)
        if not src or not msg_id:
            continue

        item_id = f"{src}/{msg_id}{p.suffix.lower()}"

        if item_id in seen:
            continue
        seen.add(item_id)

        if not p.exists():
            continue

        out.append({
            "item_id": item_id,
            "abs_path": str(p),
            "ts": int(time.time()),
            "src": src,
            "msg_id": msg_id,
            "views": views,
            "forwards": forwards,
            "replies": replies,
            "reactions_total": reactions_total,
            "score": score,
        })
    # best first: by engagement score, tie-breaker by recency already in file order
    try:
        out.sort(key=lambda x: float(x.get("score") or 0.0), reverse=True)
    except Exception:
        pass

    pool = {"ts": int(time.time()), "items": out}
    _save_json(_pool_raw_path("video"), pool)           # what was found today (debug)
    _save_json(_pool_path("video", user_id), pool)      # work pool
    print(f"[pool] video refreshed items={len(out)}", flush=True)
    return pool

def _refresh_meme_pool(user_id: int) -> dict:
    COLLECT_N = int(os.getenv("V_MEME_COLLECT_N", "120"))
    items = rank_memes(user_id=user_id, n=COLLECT_N)

    out = []
    seen = set()
    for it in items:
        item_id = (getattr(it, "item_id", "") or "").strip()
        abs_path = (getattr(it, "abs_path", "") or "").strip()
        if not item_id or item_id in seen:
            continue
        seen.add(item_id)

        p = Path(abs_path) if abs_path else None
        if not p or (not p.exists()):
            continue

        cap = ""
        src = (getattr(it, "src", "") or "").strip()

        mp = Path(str(p) + ".meta.json")
        if mp.exists():
            try:
                meta = json.loads(mp.read_text(encoding="utf-8"))
                cap = (meta.get("caption") or "").strip()
                if not src:
                    src = (meta.get("src") or "").strip()
            except Exception:
                pass

        out.append({
            "item_id": item_id,
            "abs_path": str(p),
            "caption": cap,
            "src": src,
            "ts": int(time.time()),
        })

    pool = {"ts": int(time.time()), "items": out}
    _save_json(_pool_raw_path("meme"), pool)            # what was found today (debug)
    _save_json(_pool_path("meme", user_id), pool)       # work pool
    print(f"[pool] meme refreshed items={len(out)}", flush=True)
    return pool

MSK = timezone(timedelta(hours=3))

async def ingest24_loop(bot: Bot) -> None:
    await asyncio.sleep(10)

    while True:
        now = datetime.now(MSK)
        next_run = now.replace(hour=5, minute=0, second=0, microsecond=0)

        if now >= next_run:
            next_run += timedelta(days=1)

        wait_sec = (next_run - now).total_seconds()
        print(f"[ingest24] next run in {int(wait_sec)} sec", flush=True)

        await asyncio.sleep(wait_sec)

        print("[ingest24] starting ingest_hours(24)", flush=True)

       # === BROADCAST TARGETS ===
        targets = []

        # 1. Группы (список или один id)
        _ids_env = (os.getenv("MORNING_CHAT_IDS") or os.getenv("MORNING_CHAT_ID") or "").strip()
        if _ids_env:
            for part in _ids_env.split(","):
                part = part.strip()
                if part:
                    targets.append(int(part))

        # 2. Все личные пользователи
        targets.extend(list(_load_private_users()))

        if not targets:
            print("[ingest24] no targets, skip sending", flush=True)
            continue

        # === MORNING QUOTE TO ALL TARGETS ===
        try:            
            for target_chat_id in targets:
                seed = int(time.time()) ^ (abs(int(target_chat_id)) & 0xFFFF)
                quote_text = chatgpt_dialog.pick_sarcastic_quote_ru(seed=seed)
                quote_ru = chatgpt_dialog.translate_to_ru(quote_text)
                try:
                    await bot.send_message(
                        target_chat_id,
                        _format_morning_quote(quote_ru),
                        parse_mode="html"
                    )
                except Exception as e:
                    print(f"[ingest24] quote send error to {target_chat_id}: {e}", flush=True)
        except Exception as e:
            print(f"[ingest24] quote send error: {e}", flush=True)
        try:
            async with TG_LOCK:
                await ingest_hours(24)

            # отправка пользователю

            class Dummy:
                def __init__(self, bot, chat_id, user_id):
                    self.bot = bot
                    self.chat = type("c", (), {"id": chat_id})
                    self.message_id = 0
                    self.from_user = type("u", (), {"id": user_id})

                async def answer(self, text, **kw):
                    return await self.bot.send_message(self.chat.id, text, **kw)

                async def answer_photo(self, photo, **kw):
                    return await self.bot.send_photo(self.chat.id, photo, **kw)

                async def answer_video(self, video, **kw):
                    return await self.bot.send_video(self.chat.id, video, **kw)

                async def answer_document(self, document, **kw):
                    return await self.bot.send_document(self.chat.id, document, **kw)

            for target_chat_id in targets:

                dummy = Dummy(bot, target_chat_id, target_chat_id)

                try:
                    _refresh_video_pool(target_chat_id)
                except Exception as e:
                    print(f"[pool] video refresh error: {e}", flush=True)

                try:
                    _refresh_meme_pool(target_chat_id)
                except Exception as e:
                    print(f"[pool] meme refresh error: {e}", flush=True)

                try:
                    await _send_content(dummy, user_id=target_chat_id, ingest_hours_n=None, send_mode="get24")
                except Exception as e:
                    print(f"[ingest24] send error to {target_chat_id}: {e}", flush=True)

                users = _load_private_users()
                if target_chat_id in users:
                    users.discard(target_chat_id)
                    _save_private_users(users)

        except Exception as e:
            print(f"[ingest24] error: {e}", flush=True)
async def heartbeat_loop() -> None:
    while True:
        _log("heartbeat")
        await asyncio.sleep(300)

# =========================
# START
# =========================
async def main() -> None:
    _log("starting aiogram polling")
    asyncio.create_task(heartbeat_loop())
    asyncio.create_task(ingest24_loop(bot))
    await dp.start_polling(bot)

@dp.callback_query(F.data.startswith("fb:"))
async def on_feedback(cb):
    parts = cb.data.split(":")
    action = parts[1]
    item_id = ":".join(parts[2:])   # ← FIX

    print(f"[feedback] action={action} item_id={item_id} user={cb.from_user.id}", flush=True)

    import time
    with open(FEEDBACK_PATH, "a", encoding="utf-8") as f:
        f.write(f"{int(time.time())}\t{cb.from_user.id}\t{action}\t{item_id}\n")

    try:
        await cb.answer("принято 🔥")
    except Exception:
        pass



if __name__ == "__main__":
    asyncio.run(main())