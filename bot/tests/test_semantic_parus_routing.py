"""Regression checks: Parus report must not hijack Vesya commands."""
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import chatgpt_dialog


def _response(data):
    return SimpleNamespace(output_text=json.dumps(data, ensure_ascii=False))


class ParusRoutingIsolationTests(unittest.TestCase):
    def _route(self, text, model_outputs, topic=None):
        client = MagicMock()
        client.responses.create.side_effect = [_response(value) for value in model_outputs]
        with patch.object(chatgpt_dialog, "_has_key", return_value=True), \
             patch.object(chatgpt_dialog, "OpenAI", return_value=client):
            result = chatgpt_dialog.semantic_context_route(text, topic=topic)
        return result, client.responses.create

    def test_news_command_stays_in_normal_router_despite_stale_parus_topic(self):
        previous = {"type": "parus_monitor", "hours": 4, "summary": "ПАРУС"}
        result, calls = self._route("Веся новости", [
            {"route": "parus_monitor", "hours": 4, "focus": "summary"},
            {"monitor": False},
        ], topic=previous)
        self.assertEqual(result["route"], "chat")
        self.assertEqual(calls.call_count, 2)

    def test_entertainment_command_not_a_parus_followup(self):
        previous = {"type": "parus_monitor", "hours": 4}
        for command in ("Веся жги", "Веся дай красоты"):
            with self.subTest(command=command):
                result, calls = self._route(command, [
                    {"route": "topic_followup"},
                    {"monitor": False},
                ], topic=previous)
                self.assertEqual(result["route"], "chat")
                self.assertFalse(result["inherit_period"])
                self.assertEqual(calls.call_count, 2)

    def test_real_parus_request_still_routes_to_monitor(self):
        result, calls = self._route("Веся, что происходило в Парусе за 4 часа?", [
            {"route": "parus_monitor", "hours": 4, "focus": "summary"},
            {"monitor": True},
        ])
        self.assertEqual(result["route"], "parus_monitor")
        self.assertEqual(result["hours"], 4)
        self.assertEqual(calls.call_count, 2)

    def test_explicit_parus_followup_keeps_previous_period_and_case(self):
        previous = {"type": "parus_monitor", "hours": 24, "case_id": 60}
        result, _ = self._route("А по делу 60?", [
            {"route": "topic_followup", "focus": "cases"},
            {"monitor": True},
        ], topic=previous)
        self.assertEqual(result["route"], "parus_monitor")
        self.assertTrue(result["inherit_case"])
        self.assertTrue(result["inherit_period"])

    def test_verification_error_fails_closed(self):
        result, _ = self._route("Веся новости", [
            {"route": "parus_monitor"},
            {"unexpected": True},
        ], topic={"type": "parus_monitor"})
        self.assertEqual(result["route"], "chat")

    def test_regular_chat_has_no_extra_llm_call(self):
        result, calls = self._route("Расскажи что-нибудь", [
            {"route": "chat", "instruction": ""},
        ], topic={"type": "parus_monitor"})
        self.assertEqual(result["route"], "chat")
        self.assertEqual(calls.call_count, 1)


if __name__ == "__main__":
    unittest.main()
