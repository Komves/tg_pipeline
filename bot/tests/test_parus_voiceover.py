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
    BRIAN_STRESS_SAMPLE,
    VOICE_PRESETS,
    render_narration_sample,
    safe_provider_error_code,
)


class ParusVoiceoverTests(unittest.TestCase):
    def test_short_public_sample_and_distinct_voice_ids(self):
        self.assertLessEqual(len(SAMPLE_TEXT), MAX_SAMPLE_CHARACTERS)
        self.assertEqual(len(VOICE_PRESETS), 3)
        self.assertEqual(len({preset[2] for preset in VOICE_PRESETS}), 3)
        self.assertIn("ПАРУС", SAMPLE_TEXT)

    def test_brian_only_first_mention_has_first_syllable_stress(self):
        self.assertTrue(BRIAN_STRESS_SAMPLE.startswith("Па́рус —"))
        self.assertIn("стороны ПАРУС готовит", BRIAN_STRESS_SAMPLE)
        self.assertNotIn("ПАРУС —", BRIAN_STRESS_SAMPLE)
        self.assertLessEqual(len(BRIAN_STRESS_SAMPLE), MAX_SAMPLE_CHARACTERS)

    def test_safe_error_code_extraction(self):
        response = Mock()
        response.json.return_value = {
            "detail": {"status": "invalid_api_key", "message": "Never show token abc123"}
        }
        self.assertEqual(safe_provider_error_code(response), "invalid_api_key")

    def test_safe_error_code_rejects_untrusted_message(self):
        response = Mock()
        response.json.return_value = {"detail": {
            "status": "invalid_key?token=something-secret",
        }}
        self.assertEqual(safe_provider_error_code(response), "unspecified")

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

    def test_missing_proxy_blocks_direct_requests_without_network(self):
        with patch.dict(os.environ, {
            "ELEVENLABS_API_KEY": "testing-key",
            "ELEVENLABS_PROXY": "",
        }):
            with patch("httpx.Client") as client:
                with self.assertRaisesRegex(RuntimeError, "Не настроен ELEVENLABS_PROXY"):
                    render_narration_sample(SAMPLE_TEXT, VOICE_PRESETS[0][2])
        client.assert_not_called()

    def test_broken_proxy_does_not_attempt_direct_route(self):
        import httpx

        proxy_url = "socks5://user:secret@127.0.0.1:1080"
        broken = Mock()
        broken.post.side_effect = httpx.ConnectError("unreachable-diagnostic-private")
        proxy_context = Mock()
        proxy_context.__enter__ = Mock(return_value=broken)
        proxy_context.__exit__ = Mock(return_value=None)

        with patch.dict(os.environ, {
            "ELEVENLABS_API_KEY": "testing-key",
            "ELEVENLABS_PROXY": proxy_url,
        }):
            with patch("httpx.Client", return_value=proxy_context) as factory:
                with self.assertRaises(RuntimeError) as raised:
                    render_narration_sample(SAMPLE_TEXT, VOICE_PRESETS[0][2])

        result = str(raised.exception)
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(factory.call_args.kwargs["proxy"], proxy_url)
        self.assertIn("прокси Timeweb: ConnectError", result)
        self.assertNotIn("secret", result)
        self.assertNotIn("unreachable-diagnostic-private", result)

    def test_provider_error_code_reported_without_leaking_message(self):
        import httpx

        request = httpx.Request("POST", "https://api.elevenlabs.io/v1/text-to-speech/fake")
        response = httpx.Response(
            400,
            request=request,
            json={"detail": {
                "status": "invalid_language_code",
                "message": "private customer details should not be shown",
            }},
        )
        client = Mock()
        client.post.return_value = response
        manager = Mock()
        manager.__enter__ = Mock(return_value=client)
        manager.__exit__ = Mock(return_value=None)
        with patch.dict(os.environ, {
            "ELEVENLABS_API_KEY": "testing-key",
            "ELEVENLABS_PROXY": "socks5://127.0.0.1:1080",
        }):
            with patch("httpx.Client", return_value=manager):
                with self.assertRaises(RuntimeError) as raised:
                    render_narration_sample(SAMPLE_TEXT, VOICE_PRESETS[0][2])
        safe_error = str(raised.exception)
        self.assertIn("HTTP 400 / invalid_language_code", safe_error)
        self.assertIn("прокси Timeweb", safe_error)
        self.assertNotIn("private customer", safe_error)

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
        self.assertNotIn("language_code", kwargs["json"])
        self.assertEqual(kwargs["json"]["model_id"], "eleven_multilingual_v2")
        self.assertEqual(kwargs["params"]["output_format"], "mp3_44100_128")


if __name__ == "__main__":
    unittest.main()
