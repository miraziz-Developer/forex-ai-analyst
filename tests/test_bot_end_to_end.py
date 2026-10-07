"""A month-end day minute by minute with every engine running, including a broken ML data source."""
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from forex_ai_analyst.forex import mt5_bot
from test_mt5_bot import FakeMT5

LONDON = ZoneInfo("Europe/London")


class Broker(FakeMT5):
    TIMEFRAME_D1, TIMEFRAME_H4 = 16408, 16388

    def __init__(self):
        super().__init__()
        self.mid = 1.1000

    def symbol_info(self, name):
        if name in ("XAUUSD", "USOIL") or name in ("EURUSD", "GBPUSD", "USDJPY", "AUDUSD"):
            return SimpleNamespace(trade_tick_size=0.00001, point=0.00001, trade_tick_value=1.0, volume_step=0.01,
                                   volume_min=0.01, volume_max=100.0, filling_mode=1)
        return None

    def symbol_info_tick(self, name):
        return SimpleNamespace(ask=self.mid + 0.00005, bid=self.mid - 0.00005)

    def copy_rates_from_pos(self, symbol, timeframe, start, count):
        closes = [1.0] * 100 + [1.05, 1.05]                    # a breakout on the last completed bar
        return [{"time": 1_790_000_000 + k * 86_400, "open": c, "high": c + 0.001, "low": c - 0.001, "close": c}
                for k, c in enumerate(closes)][-count:]


class EndToEndTests(unittest.TestCase):
    def test_month_end_day_with_a_failing_ml_source(self):
        db = mt5_bot.open_db(":memory:")
        mt5 = Broker()
        alerts, sent = {}, []
        start = datetime(2026, 10, 30, 14, 50, tzinfo=LONDON).astimezone(timezone.utc)
        env = {"FX_BOT_MAX_TOTAL_RISK_PCT": "100", "FX_BOT_MAX_STRESS_PCT": "1000",
               "FX_BOT_MAX_STRESS_TRADE_PCT": "100", "FX_BOT_MAX_SPREAD_FRAC": "1"}
        with patch.dict("os.environ", env), \
                patch.object(mt5_bot, "load_context", side_effect=ConnectionError("yahoo down")), \
                patch.object(mt5_bot.guards, "calendar_events", return_value=[]):
            for minute in range(80):                           # 14:50 .. 16:09 London
                now = start + timedelta(minutes=minute)
                if now.astimezone(LONDON).hour == 15 and now.astimezone(LONDON).minute == 30:
                    mt5.mid = 1.1040                           # the pre-fix move
                sent += mt5_bot.tick(mt5, db, now, slow=minute % 15 == 0, alerts=alerts)
            next_noon = datetime(2026, 11, 2, 12, 0, tzinfo=LONDON).astimezone(timezone.utc)
            sent += mt5_bot.tick(mt5, db, next_noon, slow=True, alerts=alerts)
        ml_errors = [m for m in sent if m.startswith("⚠️ ml")]
        self.assertEqual(len(ml_errors), 1)                    # reported once, not every 15 minutes
        fix = [m for m in sent if m.startswith("📌 [fix]")]
        self.assertEqual(len(fix), 4)                          # EURUSD, GBPUSD, USDJPY, AUDUSD
        self.assertTrue(all("SELL" in m for m in fix))         # faded the rise
        trend = [m for m in sent if m.startswith("📌 [trend]")]
        self.assertEqual(len(trend), 2)                        # gold and oil, once each
        self.assertEqual(len([m for m in sent if "[fix]" in m and "yopildi" in m]), 4)
        statuses = {(r["model_version"][:4], r["status"]) for r in db.execute("SELECT model_version, status FROM trades")}
        self.assertIn(("fix_", "CLOSED"), statuses)
        self.assertIn(("comm", "OPEN"), statuses)


if __name__ == "__main__":
    unittest.main()


class SwitchAndStartupTests(unittest.TestCase):
    def test_ml_engine_can_be_switched_off_but_closing_still_runs(self):
        db = mt5_bot.open_db(":memory:")
        mt5 = Broker()
        calls = []
        with patch.dict("os.environ", {"FX_BOT_ENGINES": "trend,fix"}), \
                patch.object(mt5_bot, "decide", side_effect=lambda *a, **k: calls.append("ml") or []), \
                patch.object(mt5_bot, "close_due", side_effect=lambda *a, **k: calls.append("close") or []), \
                patch.object(mt5_bot.guards, "calendar_events", return_value=[]):
            mt5_bot.tick(mt5, db, datetime(2026, 10, 7, 12, tzinfo=timezone.utc), slow=True, alerts={})
        self.assertEqual(calls, ["close"])

    def test_startup_report_names_found_and_missing_markets(self):
        text = mt5_bot.startup_report(Broker())
        self.assertIn("XAUUSD=XAUUSD", text)
        self.assertIn("WTI=USOIL", text)
        self.assertIn("topilmadi: XAGUSD", text)


class DailyStatusTests(unittest.TestCase):
    def test_one_status_message_a_day_with_nearest_signals(self):
        from forex_ai_analyst.forex import status_report
        db = mt5_bot.open_db(":memory:")
        mt5 = Broker()
        engines = {"crypto", "gold", "trend", "index", "fix", "rebound"}
        early = datetime(2026, 10, 7, 5, tzinfo=timezone.utc)
        self.assertIsNone(status_report.daily_status(mt5, db, early, engines))          # before 06:00 UTC
        now = datetime(2026, 10, 7, 7, tzinfo=timezone.utc)
        text = status_report.daily_status(mt5, db, now, engines)
        self.assertIn("Kunlik holat", text)
        self.assertIn("[trend] yorilishgacha: ", text)
        self.assertIn("XAUUSD", text)
        self.assertIn("[fix] oy oxiriga 23 kun", text)                                  # Oct 7 -> Oct 30
        self.assertIn("[rebound]", text)
        self.assertIsNone(status_report.daily_status(mt5, db, now, engines))            # once a day
