"""Tests for the owner's structured Parus timeline."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import parus_monitor


class ParusMonitorTests(unittest.TestCase):
    def test_default_period(self):
        self.assertEqual(parus_monitor.route_params({"route": "parus_monitor"}), (4, None, "summary"))

    def test_case_followup_inherits_scope(self):
        x = parus_monitor.route_params(
            {"inherit_case": True, "inherit_period": True, "focus": "cases"},
            {"type": "parus_monitor", "hours": 24, "case_id": 60},
        )
        self.assertEqual(x, (24, 60, "cases"))

    def test_invalid_hours_are_bounded(self):
        self.assertEqual(parus_monitor.route_params({"hours": 9999})[0], 720)
        self.assertEqual(parus_monitor.route_params({"hours": -2})[0], 1)

    def test_case_report_does_not_claim_raw_logs(self):
        s = parus_monitor.format_monitor({
            "hours": 4,
            "selected_case": {
                "number": "A75-2026/123", "email": "user@example.ru",
                "status": "В работе", "updated_at": "2026-10-31T05:00:00+00:00",
            },
            "jobs": [{"case_id": 60, "job_type": "workspace_turn",
                      "status": "completed", "updated_at": "2026-10-31T05:10:00+00:00"}],
            "events": [],
        }, case_id=60)
        self.assertIn("Дело №60", s)
        self.assertIn("завершено", s)
        self.assertIn("не сырые логи", s)

    def test_payment_total_no_bonus_mixing(self):
        s = parus_monitor.format_monitor({
            "hours": 4,
            "payments": [
                {"email": "one@example.ru", "amount_rub": "1000", "at": "2026-10-31T05:00:00+00:00"},
                {"email": "two@example.ru", "amount_rub": "2000", "at": "2026-10-31T05:10:00+00:00"},
            ],
            "charges": {"cash_rub": "50.5"},
        }, focus="money")
        self.assertIn("3 000,00 ₽", s)
        self.assertIn("50,50 ₽", s)


if __name__ == "__main__":
    unittest.main()
