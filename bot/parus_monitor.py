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


def format_monitor(data, focus="summary", case_id=None):
    out = [f"⚓ ПАРУС — последние {data.get('hours') or 4} ч"]
    payments = data.get("payments") or []
    jobs = data.get("jobs") or []
    events = data.get("events") or []
    errors = data.get("errors") or []
    if case_id:
        record = data.get("selected_case")
        out.append(f"Дело №{case_id}")
        if not record:
            out.append("Дело не найдено.")
        else:
            out.append(f"Номер: {record.get('number')} · {record.get('email') or record.get('user_id')}")
            out.append(f"Статус: {record.get('status')}; обновлено {clock(record.get('updated_at'))}")
        for job in [j for j in jobs if j.get("case_id") == case_id][:10]:
            out.append(f"• {clock(job.get('finished_at') or job.get('updated_at'))}: {JOB_NAMES.get(job.get('job_type'), job.get('job_type'))} — {job.get('status')}")
        for event in [e for e in events if e.get("case_id") == case_id][:10]:
            if event.get("type") in EVENT_NAMES:
                out.append(f"• {clock(event.get('at'))}: {EVENT_NAMES[event['type']]}")
        out.append("Расходы конкретного дела и скачивания этот источник не подтверждает.")
        out.append("Источник: события ПАРУСа, не сырые логи Render.")
        return "\n".join(out)
    visitors = data.get("visitors") or {}
    if focus in {"summary", "visitors"}:
        out.append(f"Посетителей по браузерным идентификаторам: {visitors.get('observed', 0)}; вовлечённых: {visitors.get('engaged', 0)}; с меткой Яндекса: {visitors.get('from_yandex', 0)}.")
        out.append("Это не число уникальных людей или оплаченных кликов.")
        out.append(f"Новых регистраций: {len(data.get('registrations') or [])}; дел: {len(data.get('new_cases') or [])}.")
    if focus in {"summary", "payments", "money"}:
        total = sum((Decimal(str(p.get("amount_rub") or 0)) for p in payments), Decimal("0"))
        out.append(f"Поступило: {len(payments)} на {money(total)}; списано: {money((data.get('charges') or {}).get('cash_rub'))}.")
        for p in payments[:5]:
            out.append(f"• {clock(p.get('at'))}: {p.get('email')} — {money(p.get('amount_rub'))}")
    if focus in {"summary", "cases"}:
        for c in (data.get("new_cases") or [])[:5]:
            out.append(f"• Новое дело №{c.get('case_id')}: {c.get('email')}")
        for j in jobs[:8]:
            out.append(f"• Дело №{j.get('case_id')}: {JOB_NAMES.get(j.get('job_type'), j.get('job_type'))} — {j.get('status')}")
    if focus in {"summary", "errors", "problems"}:
        out.append(f"Ошибок: {len(errors)}; заданий с ошибками: {sum(j.get('status') == 'failed' for j in jobs)}.")
    out.append("Источник: база ПАРУСа, не полные логи Render.")
    return "\n".join(out)
