import sys
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from forex_ai_analyst.forex import mt5_bot, trend_live
from test_mt5_bot import FakeMT5

DAY = 86_400


class Market(FakeMT5):
    """Offers only GOLD (as 'XAUUSD') and 'USOIL'; daily closes follow `path` plus a forming bar."""
    TIMEFRAME_D1 = 16408

    def __init__(self, path):
        super().__init__()
        self.path = path

    def symbol_info(self, name):
        if name not in ("XAUUSD", "USOIL"):
            return None
        return SimpleNamespace(trade_tick_size=0.01, point=0.01, trade_tick_value=1.0, volume_step=0.01,
                               volume_min=0.01, volume_max=100.0, filling_mode=1)

    def symbol_info_tick(self, name):
        last = self.path[-1]
        return SimpleNamespace(ask=last + 0.02, bid=last)

    def copy_rates_from_pos(self, symbol, timeframe, start, count):
        rows = [{"time": 1_700_000_000 + k * DAY, "open": c, "high": c + 0.5, "low": c - 0.5, "close": c}
                for k, c in enumerate(self.path + [self.path[-1]])]          # last row = forming bar
        return rows[-count:]


class TrendLiveTests(unittest.TestCase):
    def setUp(self):
        self.db = mt5_bot.open_db(":memory:")
        self.bot = sys.modules["forex_ai_analyst.forex.mt5_bot"]
        self.now = datetime(2026, 10, 7, 12, tzinfo=timezone.utc)

    def run_on(self, mt5):
        return trend_live.cycle(mt5, self.db, self.now, mt5.account_info(), False, self.bot)

    def test_breakout_buys_once_and_channel_break_closes(self):
        flat = [100.0] * 100
        mt5 = Market(flat + [103.0])                                      # close above the 55-day high
        out = self.run_on(mt5)
        self.assertEqual(len([m for m in out if "📌" in m]), 2)           # gold and oil both offered
        self.assertEqual(self.run_on(mt5), [])                            # same bar: no second entry
        row = self.db.execute("SELECT stop, exit_day FROM trades WHERE market = 'WTI'").fetchone()
        self.assertEqual(row["exit_day"], trend_live.OPEN_ENDED)
        self.assertLess(row["stop"], 103.0)
        mt5.path = flat + [103.0, 98.0]                                   # close below the 20-day low
        closed = self.run_on(mt5)
        self.assertEqual(len([m for m in closed if "yopildi" in m]), 2)

    def test_open_ended_trades_are_not_closed_by_the_weekly_calendar(self):
        mt5 = Market([100.0] * 100 + [103.0])
        self.run_on(mt5)
        self.assertEqual(mt5_bot.close_due(mt5, self.db, "2027-01-01"), [])

    def test_unknown_symbols_are_skipped(self):
        self.assertIsNone(trend_live.resolve(Market([1.0]), "PALLADIUM"))


if __name__ == "__main__":
    unittest.main()


class MarketClosedTests(unittest.TestCase):
    def test_signal_on_a_closed_market_waits_quietly_and_is_taken_when_it_reopens(self):
        db = mt5_bot.open_db(":memory:")
        bot = sys.modules["forex_ai_analyst.forex.mt5_bot"]
        mt5 = Market([100.0] * 100 + [103.0])
        closed = SimpleNamespace(retcode=10018, order=0, price=0.0, comment="Market closed")
        real_send = mt5.order_send
        mt5.order_send = lambda request: closed
        now = datetime(2026, 10, 10, 23, tzinfo=timezone.utc)
        self.assertEqual(trend_live.cycle(mt5, db, now, mt5.account_info(), False, bot), [])     # silent
        self.assertIsNone(db.execute("SELECT 1 FROM trades").fetchone())                          # not journalled
        mt5.order_send = real_send                                                               # market reopens
        out = trend_live.cycle(mt5, db, now, mt5.account_info(), False, bot)
        self.assertEqual(len([m for m in out if "📌" in m]), 2)

    def test_netting_account_is_flagged_at_start(self):
        mt5 = Market([1.0] * 5)
        mt5.account_info = lambda: SimpleNamespace(trade_mode=0, equity=10_000.0, margin_mode=0)
        self.assertIn("NETTING", mt5_bot.startup_report(mt5))
        mt5.account_info = lambda: SimpleNamespace(trade_mode=0, equity=10_000.0, margin_mode=2)
        self.assertNotIn("NETTING", mt5_bot.startup_report(mt5))
