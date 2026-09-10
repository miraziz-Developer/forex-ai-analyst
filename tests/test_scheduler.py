import os
import unittest
from unittest.mock import patch


os.environ.setdefault("TURSO_DATABASE_URL", "libsql://test.invalid")
os.environ.setdefault("TURSO_AUTH_TOKEN", "test")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "token")
os.environ.setdefault("TELEGRAM_CHAT_ID", "chat")

import scheduler


class ResolutionNotificationTests(unittest.TestCase):
    @patch("scheduler.send_telegram_message")
    def test_executed_win_reports_estimated_net_profit(self, notify):
        signal = {
            "id": 1, "pair": "BTC-USDT", "direction": "BUY",
            "entry_price": 100.0, "broker_qty": 0.1, "funding_rate_pct": 0.0,
            "signal_time": scheduler.datetime.now(scheduler.timezone.utc),
        }

        scheduler._notify_resolution(signal, "WIN", 150.0)

        message = notify.call_args.args[0]
        self.assertIn("SAVDO YOPILDI — FOYDA", message)
        self.assertIn("Sof taxminiy foyda: +4.99 USDT", message)

    @patch("scheduler.send_telegram_message")
    def test_analysis_only_loss_is_not_presented_as_pnl(self, notify):
        signal = {
            "id": 2, "pair": "ETH-USDT", "direction": "SELL",
            "entry_price": 100.0, "broker_qty": None, "funding_rate_pct": None,
            "signal_time": scheduler.datetime.now(scheduler.timezone.utc),
        }

        scheduler._notify_resolution(signal, "LOSS", 110.0)

        message = notify.call_args.args[0]
        self.assertIn("SAVDO YOPILDI — ZARAR", message)
        self.assertIn("P&L: hisoblanmadi", message)


if __name__ == "__main__":
    unittest.main()