import sys
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from forex_ai_analyst.forex import crypto_live, mt5_bot
from test_mt5_bot import FakeMT5

H4 = 4 * 3600


class CryptoBroker(FakeMT5):
    TIMEFRAME_H4 = 16388

    def __init__(self, path):
        super().__init__()
        self.path = path

    def symbol_info(self, name):
        if name not in ("BTCUSD", "ETHUSD"):
            return None
        return SimpleNamespace(trade_tick_size=0.01, point=0.01, trade_tick_value=0.01, volume_step=0.01,
                               volume_min=0.01, volume_max=100.0, filling_mode=1, digits=2)

    def symbol_info_tick(self, name):
        return SimpleNamespace(ask=self.path[-1] + 5, bid=self.path[-1])

    def copy_rates_from_pos(self, symbol, timeframe, start, count):
        assert timeframe == self.TIMEFRAME_H4
        rows = [{"time": 1_790_000_000 + k * H4, "open": c, "high": c + 50, "low": c - 50, "close": c}
                for k, c in enumerate(self.path + [self.path[-1]])]
        return rows[-count:]


class CryptoLiveTests(unittest.TestCase):
    def setUp(self):
        self.db = mt5_bot.open_db(":memory:")
        self.bot = sys.modules["forex_ai_analyst.forex.mt5_bot"]

    def run_on(self, mt5, now):
        return crypto_live.cycle(mt5, self.db, now, mt5.account_info(), False, self.bot)

    def test_h4_breakout_once_per_bar_and_channel_exit(self):
        flat = [60_000.0] * 150
        mt5 = CryptoBroker(flat + [61_000.0])
        now = datetime(2026, 10, 7, 12, tzinfo=timezone.utc)
        out = self.run_on(mt5, now)
        self.assertEqual(len([m for m in out if m.startswith("📌 [crypto]")]), 2)      # BTC and ETH
        self.assertEqual(self.run_on(mt5, now), [])
        key = self.db.execute("SELECT decision_day FROM trades WHERE market = 'BTCUSD'").fetchone()[0]
        self.assertIn("T", key)                                                       # H4 bars keyed by time
        mt5.path = flat + [61_000.0, 59_000.0]                                        # below the 20-bar low
        closed = self.run_on(mt5, now)
        self.assertEqual(len([m for m in closed if "yopildi" in m]), 2)

    def test_crypto_stress_move_is_forty_percent(self):
        self.assertEqual(mt5_bot.guards.STRESS_MOVE["BTCUSD"], 0.40)

    def test_startup_report_lists_crypto(self):
        self.assertIn("BTCUSD=BTCUSD", mt5_bot.startup_report(CryptoBroker([1.0] * 5)))


if __name__ == "__main__":
    unittest.main()


class MetalsLiveTests(unittest.TestCase):
    def test_gold_h4_engine_uses_its_own_journal_and_risk(self):
        from forex_ai_analyst.forex import metals_live

        class Gold(CryptoBroker):
            def symbol_info(self, name):
                return super().symbol_info("BTCUSD") if name == "XAUUSD" else None
        db = mt5_bot.open_db(":memory:")
        mt5 = Gold([2000.0] * 150 + [2100.0])
        out = metals_live.cycle(mt5, db, datetime(2026, 10, 7, tzinfo=timezone.utc), mt5.account_info(), False,
                                sys.modules["forex_ai_analyst.forex.mt5_bot"])
        self.assertTrue(any(m.startswith("📌 [gold] XAUUSD BUY") for m in out))
        self.assertEqual(db.execute("SELECT model_version FROM trades").fetchone()[0], "metals_donchian_h4_v1")
        self.assertEqual(metals_live.METALS.default_risk_pct, 0.75)
