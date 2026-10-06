import sys
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from forex_ai_analyst.forex import index_live, mt5_bot
from test_mt5_bot import FakeMT5

DAY = 86_400


class Index(FakeMT5):
    """Offers 'US500' only; completed daily closes follow `path` plus a forming bar."""
    TIMEFRAME_D1 = 16408

    def __init__(self, path):
        super().__init__()
        self.path = path

    def symbol_info(self, name):
        if name != "US500":
            return None
        return SimpleNamespace(trade_tick_size=0.01, point=0.01, trade_tick_value=0.01, volume_step=0.1,
                               volume_min=0.1, volume_max=100.0, filling_mode=1, digits=2)

    def symbol_info_tick(self, name):
        return SimpleNamespace(ask=self.path[-1] + 0.5, bid=self.path[-1])

    def copy_rates_from_pos(self, symbol, timeframe, start, count):
        rows = [{"time": 1_700_000_000 + k * DAY, "open": c, "high": c + 5, "low": c - 5, "close": c}
                for k, c in enumerate(self.path + [self.path[-1]])]
        return rows[-count:]


def uptrend_then_dip():
    up = [4000 + 2 * k for k in range(240)]                  # long uptrend, far above SMA200
    return up + [up[-1] - 40, up[-1] - 80]                    # two sharp down closes: RSI(2) < 10


class IndexLiveTests(unittest.TestCase):
    def setUp(self):
        self.db = mt5_bot.open_db(":memory:")
        self.bot = sys.modules["forex_ai_analyst.forex.mt5_bot"]
        self.now = datetime(2026, 10, 7, 12, tzinfo=timezone.utc)

    def run_on(self, mt5):
        return index_live.cycle(mt5, self.db, self.now, mt5.account_info(), False, self.bot)

    def test_signal_rules(self):
        entry, exit_, stop = index_live.signals([{"open": c, "high": c + 5, "low": c - 5, "close": c}
                                                 for c in uptrend_then_dip()])
        self.assertTrue(entry)
        self.assertFalse(exit_)
        self.assertGreater(stop, 0)

    def test_dip_buys_once_then_exits_on_close_above_sma5(self):
        mt5 = Index(uptrend_then_dip())
        out = self.run_on(mt5)
        self.assertEqual(len([m for m in out if "📌 [index] US500 BUY" in m]), 1)
        self.assertEqual(self.run_on(mt5), [])                                     # same bar: nothing new
        mt5.path = uptrend_then_dip() + [4600.0]                                   # rebound above SMA(5)
        closed = self.run_on(mt5)
        self.assertTrue(any("[index] US500 yopildi" in m for m in closed))

    def test_exit_after_ten_bars_without_rebound(self):
        mt5 = Index(uptrend_then_dip())
        self.run_on(mt5)
        mt5.path = uptrend_then_dip() + [4380.0 - k for k in range(10)]           # keeps sliding, no SMA(5) cross
        closed = self.run_on(mt5)
        self.assertTrue(any("yopildi" in m for m in closed))

    def test_startup_report_lists_indices(self):
        self.assertIn("US500=US500", mt5_bot.startup_report(Index([1.0] * 5)))


if __name__ == "__main__":
    unittest.main()
