"""Regression tests for Gmail full-text reading (no real Gmail or OpenAI credentials)."""
from __future__ import annotations

import base64
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gmail_reader


def b64(text: str, codec: str = "utf-8") -> str:
    return base64.urlsafe_b64encode(text.encode(codec)).decode("ascii").rstrip("=")


class GmailReaderTests(unittest.TestCase):
    def test_full_plain_over_snippet_and_multiple_mime_parts(self):
        text = "Начало отчёта.\n" + ("Продажи, платежи и пользователи.\n" * 900) + "Конец отчёта: доход 2000 руб."
        detail = {
            "id": "aabb01",
            "snippet": "Начало отчёта. Сообщение обрывается...",
            "payload": {
                "mimeType": "multipart/mixed",
                "headers": [
                    {"name": "Subject", "value": "Ежедневный отчет"},
                    {"name": "From", "value": "ПАРУС <info@example.org>"},
                ],
                "parts": [
                    {"mimeType": "multipart/alternative", "parts": [
                        {"mimeType": "text/plain", "body": {"data": b64(text)}},
                        {"mimeType": "text/html", "body": {
                            "data": b64("<html><body><p>Не выбирать HTML!</p></body></html>")
                        }},
                    ]},
                    {"mimeType": "application/pdf", "filename": "Отчёт.pdf", "body": {
                        "attachmentId": "pdf-id",
                    }},
                ],
            },
        }
        item = gmail_reader.read_message(detail)
        self.assertEqual(item["body"], text)
        self.assertTrue(item["body_available"])
        self.assertEqual(item["attachments"], ["Отчёт.pdf"])
        self.assertNotIn("Сообщение обрывается", item["body"])
        pieces = gmail_reader.format_open_message(item)
        self.assertGreater(len(pieces), 2)
        body_pieces = pieces[1:]
        reconstruction = "".join(p.split(":\n\n", 1)[1] for p in body_pieces)
        self.assertEqual(reconstruction, text)
        self.assertTrue(all(len(p) <= 3600 for p in pieces))

    def test_html_only_preserves_paragraphs_and_table(self):
        detail = {
            "id": "html01",
            "payload": {
                "mimeType": "multipart/alternative",
                "parts": [{
                    "mimeType": "text/html",
                    "body": {"data": b64(
                        "<html><head><style>p{display:none}</style></head>"
                        "<body><p>На начало дня 40 пользователей</p>"
                        "<p>Сегодня добавилось 2</p><table>"
                        "<tr><td>Выручка</td><td>5000</td></tr></table></body></html>"
                    )},
                }],
            },
        }
        item = gmail_reader.read_message(detail)
        self.assertEqual(item["body_available"], True)
        self.assertIn("На начало дня 40 пользователей\n", item["body"])
        self.assertIn("Сегодня добавилось 2", item["body"])
        self.assertIn("Выручка", item["body"])
        self.assertIn("5000", item["body"])
        self.assertNotIn("display:none", item["body"])

    def test_large_text_part_with_attachment_id_fetches_entire_body(self):
        class Response:
            def raise_for_status(self):
                pass
            def json(self):
                return {"data": b64("Длинный текст " * 1200)}

        detail = {
            "id": "abcde",
            "payload": {"mimeType": "text/plain", "body": {"attachmentId": "chunk/1"}},
        }
        with patch.object(gmail_reader.requests, "get", return_value=Response()) as get:
            item = gmail_reader.read_message(detail, "token")
        self.assertEqual(item["body"], "Длинный текст " * 1200)
        self.assertIn("attachments/chunk%2F1", get.call_args.args[0])

    def test_windows_1251_body(self):
        detail = {
            "id": "enc01",
            "payload": {
                "mimeType": "text/plain",
                "headers": [{"name": "Content-Type", "value": 'text/plain; charset="windows-1251"'}],
                "body": {"data": b64("Привет, Фимыч", "cp1251")},
            },
        }
        self.assertEqual(gmail_reader.read_message(detail)["body"], "Привет, Фимыч")

    def test_missing_body_not_replaced_with_snippet(self):
        detail = {"id": "empty", "snippet": "Это лишь обрывок", "payload": {
            "mimeType": "application/octet-stream", "body": {},
        }}
        item = gmail_reader.read_message(detail)
        self.assertFalse(item["body_available"])
        self.assertEqual(item["body"], "")
        output = "\n".join(gmail_reader.format_open_message(item))
        self.assertNotIn("Это лишь обрывок", output)

    def test_split_exact_is_lossless_even_without_whitespace(self):
        for text in ["a" * 9000, "\n\n" * 5000, "one\n" * 3000]:
            self.assertEqual("".join(gmail_reader.split_exact(text, 1000)), text)

    def test_emoji_heavy_full_email_fits_telegram_utf16_limit(self):
        body = "📩" * 5500 + "\nФинальный абзац."
        item = {
            "subject": "Проверка длинного письма", "from": "from@example.org",
            "to": "to@example.org", "date": "31 Oct 2026",
            "body": body, "body_available": True, "attachments": [],
        }
        pieces = gmail_reader.format_open_message(item)
        self.assertTrue(all(len(piece.encode("utf-16-le")) // 2 <= 4096 for piece in pieces))
        self.assertEqual(
            "".join(piece.split(":\n\n", 1)[1] for piece in pieces[1:]),
            body,
        )

    def test_multichunk_summary_includes_last_part_and_consolidates(self):
        class FakeClient:
            def __init__(self, **kwargs):
                self.calls = []
                client_holder.append(self)
                self.responses = self
            def create(self, model, input):
                self.calls.append(input[-1]["content"])
                return types.SimpleNamespace(output_text=f"Содержание части {len(self.calls)}")

        client_holder = []
        fake_openai = types.ModuleType("openai")
        fake_openai.OpenAI = FakeClient
        body = "Рассказ.\n" * 4500 + "\nИтог: необходимо оплатить 5000 руб."
        with patch.dict(sys.modules, {"openai": fake_openai}), patch.dict(os.environ, {
            "OPENAI_API_KEY": "synthetic",
        }):
            output = gmail_reader._summary_sync(body, "Проверка")
        client = client_holder[0]
        self.assertGreater(len(client.calls), 2)
        self.assertTrue(any("Итог: необходимо оплатить 5000 руб." in call for call in client.calls))
        self.assertIn("Часть 1:", client.calls[-1])
        self.assertEqual(output, f"Содержание части {len(client.calls)}")

    def test_user_token_is_scoped_to_telegram_user(self):
        class Response:
            def __init__(self, payload):
                self.payload = payload
            def raise_for_status(self): pass
            def json(self): return self.payload
        with patch.dict(os.environ, {
            "SUPABASE_URL": "https://example.supabase.co",
            "SUPABASE_KEY": "secret", "GOOGLE_CLIENT_ID": "a", "GOOGLE_CLIENT_SECRET": "b",
        }), patch.object(gmail_reader.requests, "get", return_value=Response([
            {"creds_json": {"refresh_token": "fake_refresh"}}
        ])) as get, patch.object(gmail_reader.requests, "post", return_value=Response({
            "access_token": "fake_access",
        })):
            self.assertEqual(gmail_reader.get_access_token(123456), "fake_access")
        self.assertEqual(get.call_args.kwargs["params"]["user_id"], "eq.123456")


class GmailDeleteVerificationTests(unittest.TestCase):
    class FakeResponse:
        def __init__(self, payload, status=200):
            self.payload = payload
            self.status_code = status

        def raise_for_status(self):
            if self.status_code >= 400:
                raise gmail_reader.requests.HTTPError(f"{self.status_code}")

        def json(self):
            return self.payload

    def test_trash_verified_independently_and_other_thread_items_detected(self):
        post_resp = self.FakeResponse({"id": "msg123", "threadId": "thread1"})
        msg_resp = self.FakeResponse({
            "id": "msg123", "threadId": "thread1",
            "labelIds": ["TRASH", "CATEGORY_UPDATES"],
        })
        thread_resp = self.FakeResponse({
            "messages": [
                {"id": "msg123", "labelIds": ["TRASH"]},
                {"id": "anothermsg", "labelIds": ["INBOX", "CATEGORY_UPDATES"]},
            ]
        })
        with patch.object(gmail_reader.requests, "post", return_value=post_resp) as p, \
             patch.object(gmail_reader.requests, "get", side_effect=[msg_resp, thread_resp]) as g:
            result = gmail_reader.move_message_to_trash_verified("msg123", "fake_token")
        self.assertTrue(result["verified"])
        self.assertEqual(result["remaining_thread_inbox"], 1)
        self.assertTrue(p.call_args.args[0].endswith("/messages/msg123/trash"))
        self.assertEqual(g.call_args_list[0].kwargs["params"]["format"], "minimal")
        self.assertEqual(g.call_args_list[1].kwargs["params"]["format"], "minimal")

    def test_success_http_does_not_count_without_trash_label(self):
        with patch.object(gmail_reader.requests, "post", return_value=self.FakeResponse(
            {"id": "abc123", "threadId": "t123"}
        )), patch.object(gmail_reader.requests, "get", return_value=self.FakeResponse({
            "id": "abc123", "labelIds": ["INBOX", "CATEGORY_UPDATES"]
        })):
            with self.assertRaisesRegex(RuntimeError, "не подтвердил"):
                gmail_reader.move_message_to_trash_verified("abc123", "fake_token")

    def test_unauthorized_request_does_not_report_success(self):
        with patch.object(gmail_reader.requests, "post", return_value=self.FakeResponse({}, 403)):
            with self.assertRaises(gmail_reader.requests.HTTPError):
                gmail_reader.move_message_to_trash_verified("abc123", "fake_token")

    def test_verification_error_does_not_report_success(self):
        with patch.object(gmail_reader.requests, "post", return_value=self.FakeResponse({
            "id": "abc123", "threadId": "t123"
        })), patch.object(gmail_reader.requests, "get", return_value=self.FakeResponse({}, 500)):
            with self.assertRaises(gmail_reader.requests.HTTPError):
                gmail_reader.move_message_to_trash_verified("abc123", "fake_token")

    def test_optional_thread_check_failure_does_not_cancel_verified_delete(self):
        with patch.object(gmail_reader.requests, "post", return_value=self.FakeResponse({
            "id": "abc123", "threadId": "t123"
        })), patch.object(gmail_reader.requests, "get", side_effect=[
            self.FakeResponse({"id": "abc123", "labelIds": ["TRASH"]}),
            self.FakeResponse({}, 500)
        ]):
            result = gmail_reader.move_message_to_trash_verified("abc123", "fake_token")
        self.assertTrue(result["verified"])
        self.assertIsNone(result["remaining_thread_inbox"])

    def test_bad_message_id_is_rejected_before_any_http(self):
        with patch.object(gmail_reader.requests, "post") as p:
            with self.assertRaises(ValueError):
                gmail_reader.move_message_to_trash_verified("../bad", "fake_token")
            p.assert_not_called()


if __name__ == "__main__":
    unittest.main()
