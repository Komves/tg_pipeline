from __future__ import annotations

from .models import ResearchTask
from .profile_registry import PROFILES


def build_mock_report(task: ResearchTask) -> str:
    title = PROFILES.get(task.profile, {}).get("title", task.profile)

    return (
        f"🧠 Аналитическая задача создана\n\n"
        f"ID: {task.task_id}\n"
        f"Профиль: {title}\n"
        f"Статус: каркас MVP, без реального сбора данных\n\n"
        f"Задача:\n{task.user_text}\n\n"
        f"Что дальше будет делать полноценный pipeline:\n"
        f"1. разобьёт задачу на параметры;\n"
        f"2. выберет источники данных;\n"
        f"3. соберёт цены/объявления/сигналы;\n"
        f"4. нормализует данные;\n"
        f"5. посчитает выводы;\n"
        f"6. вернёт отчёт со ссылками.\n\n"
        f"Сейчас проверяем только режим, маршрутизацию и lifecycle."
    )