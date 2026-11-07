"""Offline checks for the isolated PARUS voice test helper."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vesya_tools.parus_voiceover import (
    MAX_SAMPLE_CHARACTERS,
    SAMPLE_TEXT,
    VOICE_PRESETS,
    render_narration_sample,
)


class ParusVoiceoverTests(unittest.TestCase):
    def test_short_public_sample_and_distinct_voice_ids(self):
        self.assertLessEqual(len(SAMPLE_TEXT), MAX_SAMPLE_CHARACTERS)
        self.assertEqual(len(VOICE_PRESETS), 3)
        self.assertEqual(len({preset[2] for preset in VOICE_PRESETS}), 3)
        self.assertIn("ПАРУС", SAMPLE_TEXT)

    def test_overlong_sample_rejected_without_network(self):
        with self.assertRaisesRegex(ValueError, "650 символов"):
            render_narration_sample("а" * (MAX_SAMPLE_CHARACTERS + 1), VOICE_PRESETS[0][2])

    def test_unknown_voice_rejected_without_network(self):
        with self.assertRaisesRegex(ValueError, "Неизвестный голос"):
            render_narration_sample("Проверка.", "unknown")

    def test_missing_api_key_rejected_without_network(self):
        with patch.dict(os.environ, {"ELEVENLABS_API_KEY": ""}):
            with self.assertRaisesRegex(RuntimeError, "ELEVENLABS_API_KEY"):
                render_narration_sample("Проверка.", VOICE_PRESETS[0][2])

    def test_request_uses_existing_proxy_and_russian_model(self):
        response = Mock()
        response.content = b"ID3" + b"audio-payload"
        response.raise_for_status.return_value = None
        client = Mock()
        client.post.return_value = response
        manager = Mock()
        manager.__enter__ = Mock(return_value=client)
        manager.__exit__ = Mock(return_value=None)
        with patch.dict(os.environ, {
            "ELEVENLABS_API_KEY": "testing-key",
            "ELEVENLABS_PROXY": "socks5://127.0.0.1:1080",
        }):
            with patch("httpx.Client", return_value=manager) as http_client:
                audio = render_narration_sample(SAMPLE_TEXT, VOICE_PRESETS[0][2])
        self.assertEqual(audio, response.content)
        self.assertEqual(http_client.call_args.kwargs["proxy"], "socks5://127.0.0.1:1080")
        kwargs = client.post.call_args.kwargs
        self.assertEqual(kwargs["json"]["language_code"], "ru")
        self.assertEqual(kwargs["json"]["model_id"], "eleven_multilingual_v2")
        self.assertEqual(kwargs["params"]["output_format"], "mp3_44100_128")


if __name__ == "__main__":
    unittest.main()
