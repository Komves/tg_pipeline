# bot/dialog_manager.py
import json
import time
from pathlib import Path
from typing import Dict, Tuple

DATA_DIR = Path("/data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
STATE_PATH = DATA_DIR / "dialog_state.json"

# сколько секунд считаем, что диалог "активен"
ACTIVE_TTL_SEC = 120  # 2 минуты


def _load() -> Dict[str, Dict[str, float]]:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(d: Dict[str, Dict[str, float]]) -> None:
    try:
        STATE_PATH.write_text(json.dumps(d), encoding="utf-8")
    except Exception:
        pass


def _now() -> float:
    return time.time()


def mark_bot_replied(chat_id: int, user_id: int) -> None:
    d = _load()
    d.setdefault(str(chat_id), {})[str(user_id)] = _now()
    _save(d)


def mark_user_message(chat_id: int, user_id: int) -> None:
    d = _load()
    d.setdefault(str(chat_id), {})[str(user_id)] = _now()
    _save(d)


def _is_recent(chat_id: int, user_id: int) -> bool:
    d = _load()
    ts = d.get(str(chat_id), {}).get(str(user_id))
    if not ts:
        return False
    return (_now() - ts) <= ACTIVE_TTL_SEC


def should_reply(*, addressed: bool, is_reply_to_bot: bool, text: str, chat_id: int, user_id: int) -> Tuple[bool, bool]:
    """
    Returns:
      (should_reply, should_clarify)

    Rules:
      1. If explicitly addressed -> reply
      2. If reply-to-bot -> reply
      3. If recent dialog with this user -> reply
      4. Otherwise -> do not reply
      5. If ambiguous but recent dialog expired -> clarify
    """
    if addressed:
        return True, False

    if is_reply_to_bot:
        return True, False

    if _is_recent(chat_id, user_id):
        return True, False

    # если не уверены, но похоже на вопрос — переспросим
    if "?" in text or len(text.split()) <= 4:
        return False, True

    return False, False
