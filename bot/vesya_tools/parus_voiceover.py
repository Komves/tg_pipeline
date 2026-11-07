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

# Only the first mention is changed for a pronunciation test. The TTS
# engine may or may not obey the combining stress mark; the user decides
# after listening. Subsequent occurrences keep the original spelling.
BRIAN_STRESS_SAMPLE = SAMPLE_TEXT.replace("ПАРУС —", "Па́рус —", 1)


# IDs for ElevenLabs multilingual TTS. A voice must be available to the
# account; API errors are reported instead of substituting a different voice.
VOICE_PRESETS = (
    ("adam", "Адам — низкий спокойный", "pNInz6obpgDQGcFmaJgB"),
    ("daniel", "Даниэль — ровный дикторский", "onwK4e9ZLuTAKqWW03F9"),
    ("brian", "Брайан — нейтральный", "nPczCjzI2devNBz1zQrb"),
)
MAX_SAMPLE_CHARACTERS = 650


def safe_provider_error_code(response) -> str:
    """Extract only a safe machine code from an ElevenLabs error response.

    Do not emit response body, request URL, provider message, or credentials.
    """
    try:
        detail = response.json().get("detail", {})
        if isinstance(detail, dict):
            code = detail.get("code") or detail.get("status") or ""
            if isinstance(code, str):
                import re
                if re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", code):
                    return code
    except (ValueError, AttributeError, TypeError):
        pass
    return "unspecified"


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

    proxy = (os.getenv("ELEVENLABS_PROXY") or "").strip()
    if not proxy:
        # Direct Render -> ElevenLabs requests are not an acceptable fallback.
        raise RuntimeError("Не настроен ELEVENLABS_PROXY для Timeweb. Прямой запрос заблокирован.")

    import httpx

    # Use exactly the same proxy route and httpx defaults as Vesya's existing
    # Telegram voice replies. Never fall back to a direct ElevenLabs request:
    # it bypasses the Timeweb exit route and may be refused by the provider.
    try:
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
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ProxyError) as exc:
        # Do not expose proxy URL (which may contain credentials) or API key.
        raise RuntimeError(
            f"Не удалось соединиться через прокси Timeweb: {type(exc).__name__}. "
            "Проверьте SOCKS-сервис, адрес/порт и доступность сервера."
        ) from None
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        provider_code = ""
        try:
            details = exc.response.json().get("detail", {})
            if isinstance(details, dict):
                candidate = details.get("code") or details.get("status") or ""
                if isinstance(candidate, str):
                    import re
                    if re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", candidate):
                        provider_code = candidate
        except (ValueError, AttributeError, TypeError):
            pass
        detail = f"HTTP {status}" + (f" / {provider_code}" if provider_code else "")
        raise RuntimeError(f"ElevenLabs через прокси Timeweb: {detail}.") from None
