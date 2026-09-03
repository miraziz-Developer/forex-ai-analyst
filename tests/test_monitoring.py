import os
import unittest
from unittest.mock import patch

os.environ.setdefault("TURSO_DATABASE_URL", "libsql://test.invalid")
os.environ.setdefault("TURSO_AUTH_TOKEN", "test")

import monitoring


class MonitoringFormattingTests(unittest.TestCase):
    def test_format_stats(self):
        text = monitoring.format_stats({
            "open_signals": 2,
            "all_time": {"WIN": 3, "LOSS": 1, "EXPIRED": 1,
                         "win_rate_pct": 75.0, "realized_pnl_usdt": 4.25},
            "last_30_days": {"WIN": 2, "LOSS": 1,
                             "win_rate_pct": 66.7, "realized_pnl_usdt": 1.5},
        })
        self.assertIn("WIN: 3", text)
        self.assertIn("75.0%", text)
        self.assertIn("+4.25 USDT", text)

    @patch("monitoring.storage.get_signals")
    def test_recent_requests_only_resolved_signals(self, get_signals):
        get_signals.return_value = {"signals": []}
        response = monitoring.command_response("/recent", False, "")
        get_signals.assert_called_once_with(status="RESOLVED", limit=10)
        self.assertIn("Ma'lumot yo'q", response)

    def test_status_includes_mode_and_dashboard(self):
        response = monitoring.command_response("/status", True, "https://example.test/dashboard")
        self.assertIn("DEMO", response)
        self.assertIn("https://example.test/dashboard", response)


if __name__ == "__main__":
    unittest.main()