"""Parus monitoring report."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

EVENT_NAMES = {
    "registration_completed": "зарегистрировался", "login_completed": "вошёл",
    "case_created": "создал дело", "materials_uploaded": "загрузил материалы",
    "passport_started": "запустил паспорт", "passport_completed": "получил паспорт",
    "party_report_completed": "получил отчёт",
    "workspace_turn_completed": "получил результат Рабочей комнаты",
    "public_document_completed": "закончил пробный анализ",
}
JOB_NAMES = {"workspace_turn": "Рабочая комната", "case_advisor": "Штурман"}


def clock(value):
    try:
        dt = datetime.fromisoformat(str(value))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone(timedelta(hours=5))).strftime("%d.%m %H:%M")
    except (ValueError, TypeError):
        return "—"


def money(value):
    return f"{Decimal(str(value or 0)):,.2f}".replace(",", " ").replace(".", ",") + " ₽"


def route_params(route, topic=None):
    topic = topic if isinstance(topic, dict) else {}
    try:
        hours = int(route.get("hours") or (topic.get("hours") if route.get("inherit_period") else 4))
    except (ValueError, TypeError):
        hours = 4
    raw_case = route.get("case_id") or (topic.get("case_id") if route.get("inherit_case") else None)
    try:
        case_id = int(raw_case) if raw_case else None
        if case_id is not None and case_id <= 0:
            case_id = None
    except (ValueError, TypeError):
        case_id = None
    focus = str(route.get("focus") or "summary").lower()
    if focus not in {"summary", "visitors", "cases", "payments", "money", "errors", "problems"}:
        focus = "summary"
    return max(1, min(720, hours)), case_id, focus

