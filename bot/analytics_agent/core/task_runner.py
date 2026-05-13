from __future__ import annotations

from .models import ResearchTask
from .report_builder import build_mock_report


def run_task(task: ResearchTask) -> str:
    if task.profile == "renovation":
        from analytics_agent.profiles.renovation.parser import parse_renovation_task
        from analytics_agent.profiles.renovation.report import build_renovation_report

        parsed = parse_renovation_task(task.user_text)

        if task.params:
            from analytics_agent.profiles.renovation.parser import (
                merge_renovation_params,
            )

            task.params = merge_renovation_params(
                task.params,
                parsed,
            )
        else:
            task.params = parsed
        task.status = "parsed"
        return build_renovation_report(task.task_id, task.params)

    return build_mock_report(task)