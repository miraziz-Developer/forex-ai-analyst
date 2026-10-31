import sys
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from forex_ai_analyst.forex import fomc_live, mt5_bot
from test_mt5_bot import FakeMT5

NY = ZoneInfo("America/New_York")
PAGE = '''<h4>2026 FOMC Meetings</h4>
<div class="fomc-meeting__month col-xs-5"><strong>October</strong></div><div class="fomc-meeting__date">27-28</div>
<div class="fomc-meeting__month col-xs-5"><strong>December</strong></div><div class="fomc-meeting__date">8-9*</div>
<h4>2027 FOMC Meetings</h4>
<div class="fomc-meeting__month"><strong>Jan/Feb</strong></div><div class="fomc-meeting__date">31-1</div>
<div class="fomc-meeting__month"><strong>August</strong></div><div class="fomc-meeting__date">22 (notation vote)</div>'''


class IndexBroker(FakeMT5):
    TIMEFRAME_D1 = 16408

    def symbol_info(self, name):
        if name != "US500":
            return None
        return SimpleNamespace(trade_tick_size=0.01, point=0.01, trade_tick_value=0.01, volume_step=0.01,
                               volume_min=0.01, volume_max=100.0, filling_mode=1, digits=2)

    def symbol_info_tick(self, name):
        return SimpleNamespace(ask=6000.5, bid=6000.0)

    def copy_rates_from_pos(self, symbol, timeframe, start, count):
        return [{"time": 1_780_000_000 + k * 86_400, "open": 6000.0, "high": 6040.0, "low": 5960.0, "close": 6000.0}
                for k in range(200)][-count:]


class FomcLiveTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch
        import requests
        no_network = patch.object(fomc_live.requests, "get", side_effect=requests.RequestException("offline"))
        no_network.start()
        self.addCleanup(no_network.stop)
        self.db = mt5_bot.open_db(":memory:")
        self.bot = sys.modules["forex_ai_analyst.forex.mt5_bot"]
        for day in ("2026-10-27", "2026-10-28"):                     # no network: schedule already checked
            mt5_bot.guards.put(self.db, f"fomc_schedule_checked:{day}", 1)
        mt5_bot.guards.put(self.db, "fomc_schedule", "2026-10-28,2026-12-09")

    def run_at(self, mt5, y, mo, d, h, mi):
        now = datetime(y, mo, d, h, mi, tzinfo=NY).astimezone(timezone.utc)
        return fomc_live.cycle(mt5, self.db, now, mt5.account_info(), False, self.bot)

    def test_parse_calendar_takes_the_second_day_and_skips_notation_votes(self):
        days = [d.isoformat() for d in fomc_live.parse_calendar(PAGE)]
        self.assertEqual(days, ["2026-10-28", "2026-12-09", "2027-02-01"])

    def test_buys_the_day_before_at_14_and_sells_before_the_release(self):
        mt5 = IndexBroker()
        self.assertEqual(self.run_at(mt5, 2026, 10, 27, 13, 30), [])            # too early
        out = self.run_at(mt5, 2026, 10, 27, 14, 5)
        self.assertTrue(any(m.startswith("📌 [fomc] US500 BUY") for m in out))
        self.assertLess(mt5.sent[0]["sl"], 6000.5)
        self.assertEqual(self.run_at(mt5, 2026, 10, 27, 14, 30), [])            # once only
        self.assertEqual(self.run_at(mt5, 2026, 10, 28, 13, 50), [])            # still holding
        out = self.run_at(mt5, 2026, 10, 28, 13, 56)
        self.assertTrue(any("FOMC e'lonidan oldin yopildi" in m for m in out))
        self.assertEqual(self.db.execute("SELECT status FROM trades").fetchone()[0], "CLOSED")

    def test_no_trade_on_ordinary_days(self):
        self.assertEqual(self.run_at(IndexBroker(), 2026, 10, 20, 14, 5), [])

    def test_stop_closure_is_journalled(self):
        mt5 = IndexBroker()
        self.run_at(mt5, 2026, 10, 27, 14, 5)
        mt5.positions.clear()
        out = self.run_at(mt5, 2026, 10, 27, 20, 0)
        self.assertTrue(any("himoya stopi" in m for m in out))


if __name__ == "__main__":
    unittest.main()
