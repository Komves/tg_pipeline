from __future__ import annotations

from pathlib import Path
from typing import Any, Dict
from uuid import uuid4

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from analytics_agent.profiles.renovation.surface_estimator import estimate_surfaces
from analytics_agent.profiles.renovation.material_basket import build_material_basket
from analytics_agent.profiles.renovation.market_pricing import price_material_basket
from analytics_agent.profiles.renovation.labor_estimator import estimate_labor


def _auto_width(ws) -> None:
    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)

        for cell in col:
            value = "" if cell.value is None else str(cell.value)
            max_len = max(max_len, len(value))

        ws.column_dimensions[col_letter].width = min(max_len + 2, 45)


def build_renovation_xlsx(task_id: str, params: Dict[str, Any], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)

    surfaces = estimate_surfaces(params)
    basket = build_material_basket(params, surfaces)
    priced = price_material_basket(basket)

    wb = Workbook()

    ws = wb.active
    ws.title = "Summary"

    area = float(params.get("area_m2") or 0)
    repair_class = params.get("repair_class") or "middle"
    estimate_scope = params.get("estimate_scope") or "materials"

    summary_rows = [
        ("ID", task_id),
        ("Площадь, м²", area),
        ("Класс ремонта", repair_class),
        ("Режим", estimate_scope),
        ("Итого материалы", priced.get("total_price_rub")),
        ("Источник материалов", "локальный снимок рынка ВсеИнструменты"),
    ]

    for row in summary_rows:
        ws.append(row)

    ws["A1"].font = Font(bold=True)

    ws2 = wb.create_sheet("Materials")
    ws2.append([
        "Категория",
        "Позиция",
        "Количество",
        "Ед.",
        "Цена за ед.",
        "Формула",
        "Итого",
        "Источник",
        "Комментарий",
    ])

    for cell in ws2[1]:
        cell.font = Font(bold=True)

    row_idx = 2
    for item in priced.get("items") or []:
        qty = item.get("required_packs") if item.get("pricing_mode") == "by_pack" else item.get("quantity")
        unit_price = item.get("unit_price_rub") if item.get("usable_for_total") else None

        ws2.append([
            item.get("category"),
            item.get("name"),
            qty,
            item.get("market_unit") or item.get("unit"),
            unit_price,
            f"=C{row_idx}*E{row_idx}" if unit_price else "",
            f"=C{row_idx}*E{row_idx}" if unit_price else "",
            item.get("source_title") or item.get("pricing_source"),
            item.get("basis"),
        ])
        row_idx += 1

    ws3 = wb.create_sheet("Works")
    ws3.append(["Позиция", "Объем", "Ед.", "Ставка", "Формула", "Итого", "Комментарий"])
    for cell in ws3[1]:
        cell.font = Font(bold=True)

    if estimate_scope == "materials_and_labor":
        labor = estimate_labor(params)
        ws3.append([
            "Работы укрупненно",
            area,
            "м²",
            int(labor.get("labor_rate_per_m2") or 0),
            "=B2*D2",
            labor.get("labor_base"),
            labor.get("labor_note") or "",
        ])

    ws4 = wb.create_sheet("Assumptions")
    ws4.append(["Параметр", "Значение"])
    for cell in ws4[1]:
        cell.font = Font(bold=True)

    for key, value in (params or {}).items():
        if key in ("layout",):
            continue
        ws4.append([key, str(value)])

    for sheet in wb.worksheets:
        _auto_width(sheet)

    path = output_dir / f"renovation_estimate_{task_id}_{uuid4().hex[:6]}.xlsx"
    wb.save(path)
    return path