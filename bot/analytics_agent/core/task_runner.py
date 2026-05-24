from __future__ import annotations

from .models import ResearchTask
from .report_builder import build_mock_report
from analytics_agent.profiles.real_estate.parser import parse_real_estate_object
from analytics_agent.profiles.real_estate.analyzer import analyze_real_estate
from analytics_agent.profiles.real_estate.report import build_real_estate_report


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

    if task.profile == "auto_parts":
        from analytics_agent.profiles.auto_parts.report import build_auto_parts_report

        task.status = "researched"
        return build_auto_parts_report(
            task.task_id,
            str((task.params or {}).get("vin") or task.user_text or ""),
            str((task.params or {}).get("part") or ""),
            task.params,
        )

    if task.profile == "real_estate":
        data = parse_real_estate_object(task.user_text)
        analysis = analyze_real_estate(data)
        task.params = {
            "object": data,
            "analysis": analysis,
        }
        task.status = "analyzed"
        return build_real_estate_report(data, analysis)

    return build_mock_report(task)