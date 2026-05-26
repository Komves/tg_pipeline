from __future__ import annotations


def build_birthday_message(person_name: str) -> str:
    name = (person_name or "").strip() or "именинник"

    return (
        f"🎂 Сегодня у {name} день рождения.\n\n"
        f"{name}, с днюхой 😄\n"
        f"Пусть мотор не троит, рыба клюет, а жизнь не требует капремонта."
    )