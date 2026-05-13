from __future__ import annotations

import re
from typing import Any, Dict


def parse_renovation_task(text: str) -> Dict[str, Any]:
    t = (text or "").strip()
    tl = t.lower()

    params: Dict[str, Any] = {
        "raw_text": t,
        "property_type": None,
        "area_m2": None,
        "city": None,
        "repair_class": None,
        "ceiling_height": None,
        "bathrooms": None,
        "rooms": None,
        "estimate_scope": "materials",
        "features": [],
        "missing": [],
    }

    if re.search(r"\b(новостройк|новая квартира|от застройщика)\b", tl):
        params["property_type"] = "new_building"
    elif re.search(r"\b(вторичк|старый фонд|хрущ|брежнев)\b", tl):
        params["property_type"] = "secondary"

    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:м2|м²|кв\.?\s*м|метр)", tl)
    if m:
        params["area_m2"] = float(m.group(1).replace(",", "."))

    mh = re.search(
        r"\b(2\.[3-9]|3\.[0-5])\b",
        tl,
    )

    if mh:
        params["ceiling_height"] = float(mh.group(1))

    city_patterns = [
        r"\b(москва|санкт-петербург|спб|нижневартовск|тюмень|екатеринбург|новосибирск|казань|сургут)\b",
        r"\bг\.?\s*([а-яё-]+)\b",
        r"\bгород\s+([а-яё-]+)\b",
    ]
    for p in city_patterns:
        m = re.search(p, tl, flags=re.I)
        if m:
            params["city"] = m.group(1).strip().title()
            if params["city"].lower() == "спб":
                params["city"] = "Санкт-Петербург"
            break

    if re.search(r"\b(эконом|дешев|минимальн|бюджетн)\b", tl):
        params["repair_class"] = "economy"
    elif re.search(r"\b(средн|нормальн|обычн|комфорт)\b", tl):
        params["repair_class"] = "middle"
    elif re.search(r"\b(хорош|дорог|премиум|бизнес)\b", tl):
        params["repair_class"] = "premium"

    m = re.search(r"(\d+)\s*(?:сануз|с/у|ванн)", tl)
    if m:
        params["bathrooms"] = int(m.group(1))
    elif "санузел" in tl or "ванная" in tl:
        params["bathrooms"] = 1

    m = re.search(r"(\d+)\s*(?:комнат|комн\.?|кк|к\s)", tl)
    if m:
        params["rooms"] = int(m.group(1))

    if "материалы+работы" in tl or "материалы и работы" in tl:
        params["estimate_scope"] = "materials_and_labor"
    elif "работы" in tl:
        params["estimate_scope"] = "materials_and_labor"

    feature_map = {
        "laminate": ["ламинат"],
        "tile": ["плитка", "керамогранит"],
        "paint": ["краска", "покраска"],
        "wallpaper": ["обои"],
        "stretch_ceiling": ["натяжной потолок", "натяжные потолки"],
        "electric": ["электрика", "кабель", "розетки"],
        "plumbing": ["сантехника", "трубы", "водоснабжение"],
        "doors": ["двери"],
        "kitchen": ["кухня"],
    }

    for key, words in feature_map.items():
        if any(w in tl for w in words):
            params["features"].append(key)

    if params["area_m2"] is None:
        params["missing"].append("area_m2")
    if not params["city"]:
        params["missing"].append("city")
    if not params["repair_class"]:
        params["missing"].append("repair_class")
    if not params["property_type"]:
        params["missing"].append("property_type")

    return params

def merge_renovation_params(base: Dict[str, Any], update: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base or {})

    for key in (
        "property_type",
        "area_m2",
        "city",
        "repair_class",
        "ceiling_height",
        "bathrooms",
        "rooms",
        "estimate_scope",
    ):
        if update.get(key) not in (None, "", []):
            out[key] = update[key]

    old_features = list(out.get("features") or [])
    for f in update.get("features") or []:
        if f not in old_features:
            old_features.append(f)
    out["features"] = old_features

    missing = []
    if out.get("area_m2") is None:
        missing.append("area_m2")
    if not out.get("city"):
        missing.append("city")
    if not out.get("repair_class"):
        missing.append("repair_class")
    if not out.get("property_type"):
        missing.append("property_type")

    out["missing"] = missing
    return out