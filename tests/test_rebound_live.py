import sys
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from forex_ai_analyst.forex import mt5_bot, rebound_live
from test_mt5_bot import FakeMT5

H1 = 3600


class CrashBroker(FakeMT5):
    TIMEFRAME_H1 = 16385

    def __init__(self, path, opens=None):
        super().__init__()
        self.path, self.opens, self.ask_offset = path, opens or {}, 5.0

    def symbol_info(self, name):
        if name != "BTCUSD":
            return None
        return SimpleNamespace(trade_tick_size=0.01, point=0.01, trade_tick_value=0.01, volume_step=0.01,
                               volume_min=0.01, volume_max=100.0, filling_mode=1, digits=2)

    def symbol_info_tick(self, name):
        return SimpleNamespace(ask=self.path[-1] + self.ask_offset, bid=self.path[-1])

    def copy_rates_from_pos(self, symbol, timeframe, start, count):
        assert timeframe == self.TIMEFRAME_H1
        rows = []
        for k, c in enumerate(self.path + [self.path[-1]]):          # the last row is the forming hour
            o = self.opens.get(k, c)
            rows.append({"time": 1_790_000_000 + k * H1, "open": o, "high": max(o, c) + 20,
                         "low": min(o, c) - 20, "close": c})
        return rows[-count:]


def crash_path():
    flat = [60_000.0] * 150
    fall = [60_000.0 * (1 - 0.007 * k) for k in range(1, 21)]          # -14% over twenty hours
    return flat + fall + [fall[-1] * 1.01], {len(flat) + len(fall): fall[-1]}   # last hour opens low, closes green


class ReboundLiveTests(unittest.TestCase):
    def setUp(self):
        self.db = mt5_bot.open_db(":memory:")
        self.bot = sys.modules["forex_ai_analyst.forex.mt5_bot"]
        self.now = datetime(2026, 10, 7, 12, tzinfo=timezone.utc)

    def run_on(self, mt5):
        return rebound_live.cycle(mt5, self.db, self.now, mt5.account_info(), False, self.bot)

    def test_crash_buys_once_with_stop_and_target_sent(self):
        path, opens = crash_path()
        mt5 = CrashBroker(path, opens)
        out = self.run_on(mt5)
        self.assertTrue(any(m.startswith("📌 [rebound] BTCUSD BUY") for m in out))
        order = mt5.sent[0]
        self.assertLess(order["sl"], order["price"])
        self.assertGreater(order["tp"], order["price"])
        self.assertEqual(self.run_on(mt5), [])                       # same hour: no second order
        self.assertEqual(len(mt5.sent), 1)

    def test_time_exit_after_24_hours(self):
        path, opens = crash_path()
        mt5 = CrashBroker(path, opens)
        self.run_on(mt5)
        mt5.path = path + [path[-1]] * 23
        self.assertEqual([m for m in self.run_on(mt5) if "24 soat" in m], [])
        mt5.path = path + [path[-1]] * 24
        out = self.run_on(mt5)
        self.assertTrue(any("24 soatdan keyin yopildi" in m for m in out))
        self.assertEqual(self.db.execute("SELECT status FROM trades").fetchone()[0], "CLOSED")

    def test_closed_by_broker_stop_or_target(self):
        path, opens = crash_path()
        mt5 = CrashBroker(path, opens)
        self.run_on(mt5)
        mt5.positions.clear()
        out = self.run_on(mt5)
        self.assertTrue(any("stop/target bilan yopildi" in m for m in out))

    def test_stale_signal_is_not_chased(self):
        path, opens = crash_path()
        mt5 = CrashBroker(path, opens)
        mt5.ask_offset = 5_000.0                                      # price already far above the signal close
        out = self.run_on(mt5)
        self.assertTrue(any("quvlamaydi" in m for m in out))
        self.assertEqual(mt5.sent, [])

    def test_engine_is_switchable(self):
        self.assertIn("rebound", mt5_bot.enabled_engines())


if __name__ == "__main__":
    unittest.main()
