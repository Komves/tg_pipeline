PROFILES = {
    "renovation": {
        "title": "Ремонт квартир",
        "aliases": ["ремонт", "квартира", "смета", "материалы", "стройка"],
    },
    "auto_parts": {
        "title": "Автозапчасти",
        "aliases": ["запчасти", "авто", "drom", "дром", "avito", "авито"],
    },
    "real_estate": {
        "title": "Недвижимость",
        "aliases": ["недвижимость", "квартира купить", "циан", "аренда"],
    },

    "electronics": {
        "title": "Электроника",
        "aliases": ["электроника", "телефон", "ноутбук", "маркетплейс"],
    },
    "general_object": {
        "title": "Анализ объекта",
        "aliases": [
            "анализ объекта",
            "объект",
            "предмет",
            "товар",
            "вещь",
            "одежда",
            "мотор",
            "инструмент",
            "снасть",
            "зацени",
        ],
    },
}

def detect_profile(text: str) -> str | None:
    t = (text or "").strip().lower()

    # numeric shortcuts
    if t in ("1", "1.", "ремонт"):
        return "renovation"

    if t in ("2", "2.", "автозапчасти", "авто"):
        return "auto_parts"

    if t in ("3", "3.", "недвижимость"):
        return "real_estate"

    if t in ("4", "4.", "электроника"):
        return "electronics"

    if t in ("5", "5.", "анализ объекта", "объект", "предмет", "товар"):
        return "general_object"

    for key, cfg in PROFILES.items():
        if key in t:
            return key

        if any(alias in t for alias in cfg["aliases"]):
            return key

    return None


def profiles_help() -> str:
    return (
        "Выбери профиль аналитика:\n"
        "1. ремонт квартир\n"
        "2. автозапчасти\n"
        "3. недвижимость\n"
        "4. электроника\n"
        "5. анализ объекта\n"
    )