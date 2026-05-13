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
}


def detect_profile(text: str) -> str | None:
    t = (text or "").lower()
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
        "4. электроника"
    )