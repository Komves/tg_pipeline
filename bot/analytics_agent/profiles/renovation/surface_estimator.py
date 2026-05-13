from __future__ import annotations


def estimate_surfaces(params: dict) -> dict:
    layout = params.get("layout") or {}
    rooms = layout.get("rooms") or []

    total_floor = 0.0
    bathroom_floor = 0.0
    living_floor = 0.0

    for r in rooms:
        try:
            area = float(r.get("area_m2") or 0)
        except Exception:
            continue

        total_floor += area

        name = str(r.get("name") or "").lower()

        if any(x in name for x in ("сануз", "ванн")):
            bathroom_floor += area
        else:
            living_floor += area

    ceiling_area = total_floor

    ceiling_height = float(params.get("ceiling_height") or 2.7)

    wall_area = round(
        total_floor * ceiling_height * 1.1,
        1,
    )

    bathroom_tile = round(bathroom_floor * 2.5, 1)

    plinth = round(total_floor * 0.8, 1)

    return {
        "total_floor_area": round(total_floor, 1),
        "living_floor_area": round(living_floor, 1),
        "bathroom_floor_area": round(bathroom_floor, 1),
        "ceiling_area": round(ceiling_area, 1),
        "wall_area": wall_area,
        "bathroom_tile_area": bathroom_tile,
        "plinth_m": plinth,
    }