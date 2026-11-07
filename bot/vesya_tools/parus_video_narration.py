"""Approved 16-scene PARUS technology video narration.

Audio is created on the existing ElevenLabs / Timeweb path, in Brian's voice.
The output ZIP is for an offline video edit, not a public user-facing bot feature.
"""
from __future__ import annotations

import json
import os
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from vesya_tools.parus_voiceover import MAX_SAMPLE_CHARACTERS, VOICE_PRESETS, render_narration_sample

# Each scene corresponds to a section of the previously approved V2 video.
# Offsets are reference times in that silent montage, not claimed speech lengths.
SCENES = (
    (1, "Что такое ПАРУС", 0, 13,
     "ПАРУС — платформа анализа рисков и управления судебными делами. Это не отдельный запрос к нейросети, а система, в которой материалы, аналитика и последующие задачи объединены в рамках конкретного дела."),
    (2, "Какой результат", 13, 29,
     "ПАРУС помогает разобраться в обстоятельствах спора, восстановить хронологию, изучить доводы участников и подготовить проекты процессуальных документов. Окончательные юридические решения и проверка текстов остаются за специалистом."),
    (3, "Вход по электронной почте", 29, 42,
     "Начнём с входа. Для регистрации нужен адрес электронной почты. Вводим его, нажимаем «Получить код», затем вводим одноразовый код из письма. Отдельный пароль создавать не нужно."),
    (4, "Подтверждение входа", 42, 54,
     "После подтверждения кода открывается кабинет пользователя. Если вы входите впервые, список дел будет пустым. Начнём с создания своего судебного дела."),
    (5, "Раздел «Мои дела»", 54, 64,
     "В разделе «Мои дела» нажимаем «Создать дело». Здесь впоследствии будут храниться созданные дела и результаты работы по каждому из них."),
    (6, "Создание дела", 64, 78,
     "Указываем номер судебного дела. При необходимости добавляем краткое название и комментарий. Нажимаем «Создать дело». После этого открывается карточка дела с первым этапом — загрузкой материалов."),
    (7, "Загрузка исходных материалов", 78, 98,
     "Здесь загружаем исходный комплект по делу: иск, отзывы, судебные акты, договоры, переписку и другие документы. Для первоначальной загрузки принимаются PDF, JPG, JPEG, PNG, WEBP и ZIP с такими файлами. Желательно выбрать весь первоначальный комплект за один раз."),
    (8, "Материалы приняты", 98, 112,
     "После выбора документов нажимаем «Загрузить весь комплект» и дожидаемся окончания чтения. Когда материалы готовы, ПАРУС показывает следующий шаг. Теперь первоначальные документы повторно загружать не требуется."),
    (9, "Формирование паспорта", 112, 128,
     "Нажимаем «Сформировать Паспорт дела». ПАРУС исследует загруженные материалы и готовит структурированный паспорт: участники, требования, значимые события, доказательства и процессуальная история. Обработка может занять некоторое время."),
    (10, "Чтение паспорта", 128, 141,
     "Когда паспорт готов, открываем его в карточке дела. Можно изучить разделы и перейти к упомянутым источникам. Готовый паспорт доступен также для скачивания в Word."),
    (11, "Выбор участника", 141, 153,
     "Далее выбираем сторону, с позиции которой будет проводиться дальнейший анализ: например, истца или ответчика. После выбора нажимаем «Выбрать сторону и продолжить»."),
    (12, "Позиционный доклад", 153, 166,
     "На следующем этапе нажимаем «Сформировать позиционный доклад». ПАРУС сопоставляет доводы и доказательства, отмечает противоречия и возможные риски, опираясь на ранее прочитанные материалы дела."),
    (13, "Получение доклада", 166, 178,
     "Полученный доклад можно читать в системе и скачать в формате Word. Если потребуется продолжить работу с материалами и поставить новые задачи, переходим в Рабочую комнату."),
    (14, "Рабочая комната", 178, 195,
     "В Рабочей комнате ставим конкретную задачу обычным языком. Например: «Проверь доводы ответчика по представленным документам». Если появились новые материалы, прикладываем их к заданию. Система использует контекст ранее изученного дела."),
    (15, "Работа с результатом", 195, 210,
     "После выполнения задания читаем ответ, проверяем выводы и при необходимости готовим проект процессуального документа. Результаты остаются в истории работы по делу, а подготовленные документы можно выгрузить для редактирования."),
    (16, "Начало работы", 210, 225.626953,
     "Это основной порядок работы в ПАРУСе: создаём дело, загружаем документы, формируем паспорт, выбираем сторону, получаем доклад и продолжаем работу в Рабочей комнате. Теперь можно перейти на главную страницу платформы."),
)

BRIAN_ID = next(voice_id for slug, _label, voice_id in VOICE_PRESETS if slug == "brian")
CACHE_NAME = "parus_tech_video_brian_16_v1"


def speech_text(text: str) -> str:
    """Use the user-approved stress spelling without changing screen text."""
    return text.replace("ПАРУС", "Па́рус")


def scene_manifest() -> list[dict]:
    return [
        {
            "number": n,
            "title": title,
            "source_start_seconds": start,
            "source_end_seconds": end,
            "filename": f"audio/{n:02d}.mp3",
            "screen_text": txt,
            "speech_text": speech_text(txt),
        }
        for n, title, start, end, txt in SCENES
    ]


def _safe_cache_dir() -> Path:
    # Production Vesya has a persistent /data disk. Tests may override this.
    return Path(os.getenv("PARUS_VOICE_CACHE_DIR", f"/data/{CACHE_NAME}"))


def narration_archive(*, after_scene=None) -> tuple[bytes, int, int]:
    """Return a ZIP with 16 MP3s, manifest and full script.

    Successfully rendered MP3s are cached to avoid paying ElevenLabs twice
    when a transient provider/network error interrupts the run.
    """
    manifest = scene_manifest()
    if len(manifest) != 16:
        raise RuntimeError("Ожидалось 16 сцен.")
    if any(len(item["speech_text"]) > MAX_SAMPLE_CHARACTERS for item in manifest):
        raise RuntimeError("Текст одной из сцен превышает лимит ElevenLabs.")

    cache_dir = _safe_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    buffer = BytesIO()
    synthesized = 0
    reused = 0
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(
            "scene_manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2),
        )
        archive.writestr(
            "narration_script.txt",
            "\n\n".join(
                f'{item["number"]:02d}. {item["title"]}\n{item["screen_text"]}'
                for item in manifest
            ) + "\n",
        )
        archive.writestr(
            "README.txt",
            "ПАРУС — озвучка видеоинструкции голосом Брайана.\n"
            "В папке audio находятся 16 MP3 по сценам.\n"
            "scene_manifest.json связывает аудио с 16 сценами версии 2.\n"
            "Экранный бренд — ПАРУС; ударение в тексте для синтеза — Па́рус.\n"
            "Чтобы собрать готовое видео, нужны эти файлы и исходный видеоряд.\n",
        )
        for item in manifest:
            n = item["number"]
            filename = cache_dir / f"{n:02d}.mp3"
            if filename.is_file() and filename.stat().st_size > 1024:
                audio = filename.read_bytes()
                reused += 1
            else:
                audio = render_narration_sample(item["speech_text"], BRIAN_ID)
                if len(audio) < 1024:
                    raise RuntimeError(f"Сцена {n}: пустой или слишком короткий MP3.")
                temp_path = cache_dir / f".{n:02d}.tmp"
                temp_path.write_bytes(audio)
                temp_path.replace(filename)
                synthesized += 1
            archive.writestr(item["filename"], audio)
            if after_scene is not None:
                after_scene(n, len(manifest))
    return buffer.getvalue(), synthesized, reused
