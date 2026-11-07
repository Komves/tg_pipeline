"""Private PАRUS narration samples using Vesya's existing ElevenLabs connection.

This module is intentionally independent of ordinary Telegram voice replies.
It never logs supplied scripts or sends them to anybody except the TTS provider.
"""
from __future__ import annotations

import os


SAMPLE_TEXT = (
    "ПАРУС — платформа для анализа и сопровождения судебных дел "
    "с помощью искусственного интеллекта. Работа строится вокруг "
    "конкретного дела и его документов. Сначала пользователь загружает "
    "материалы, затем формирует паспорт с участниками, обстоятельствами "
    "и хронологией событий. После выбора стороны ПАРУС готовит "
    "аналитический доклад. Дальше в Рабочей комнате можно ставить "
    "новые задачи, добавлять документы и готовить проекты "
    "процессуальных документов."
)

# IDs for ElevenLabs multilingual TTS. A voice must be available to the
# account; API errors are reported instead of substituting a different voice.
VOICE_PRESETS = (
    ("adam", "Адам — низкий спокойный", "pNInz6obpgDQGcFmaJgB"),
    ("daniel", "Даниэль — ровный дикторский", "onwK4e9ZLuTAKqWW03F9"),
    ("brian", "Брайан — нейтральный", "nPczCjzI2devNBz1zQrb"),
)
MAX_SAMPLE_CHARACTERS = 650


def render_narration_sample(text: str, voice_id: str) -> bytes:
    """Generate a short downloadable MP3 with existing credentials and proxy."""
    phrase = str(text or "").strip()
    if not phrase or len(phrase) > MAX_SAMPLE_CHARACTERS:
        raise ValueError("Текст для пробы должен содержать от 1 до 650 символов.")
    if voice_id not in {entry[2] for entry in VOICE_PRESETS}:
        raise ValueError("Неизвестный голос для пробной озвучки.")

    key = (os.getenv("ELEVENLABS_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("Не настроен ELEVENLABS_API_KEY у Веси.")

    proxy = (os.getenv("ELEVENLABS_PROXY") or "").strip() or None

    import httpx

    with httpx.Client(proxy=proxy, timeout=75.0) as client:
        response = client.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
            params={"output_format": "mp3_44100_128"},
            headers={
                "xi-api-key": key,
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
            },
            json={
                "text": phrase,
                "model_id": "eleven_multilingual_v2",
                "language_code": "ru",
                "voice_settings": {
                    "stability": 0.72,
                    "similarity_boost": 0.78,
                    "style": 0.0,
                    "use_speaker_boost": True,
                },
            },
        )
        response.raise_for_status()
        audio = bytes(response.content)
        if not audio:
            raise RuntimeError("ElevenLabs вернул пустой аудиофайл.")
        return audio
