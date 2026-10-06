import sys
import unittest
from datetime import date, datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from forex_ai_analyst.forex import fix_live, mt5_bot
from test_mt5_bot import FakeMT5

LONDON = ZoneInfo("Europe/London")


def at(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=LONDON).astimezone(timezone.utc)


class Ticks(FakeMT5):
    """FakeMT5 whose EURUSD mid follows a script; other pairs stay flat."""
    def __init__(self):
        super().__init__()
        self.mid = 1.1000

    def symbol_info_tick(self, name):
        mid = self.mid if name == "EURUSD" else 1.0
        return SimpleNamespace(ask=mid + 0.00005, bid=mid - 0.00005)


class FixLiveTests(unittest.TestCase):
    def setUp(self):
        self.db = mt5_bot.open_db(":memory:")
        self.bot = sys.modules["forex_ai_analyst.forex.mt5_bot"]

    def run_at(self, mt5, when):
        return fix_live.cycle(mt5, self.db, when, mt5.account_info(), False, self.bot)

    def test_last_weekday(self):
        self.assertEqual(fix_live.last_weekday(date(2026, 10, 6)), date(2026, 10, 30))
        self.assertEqual(fix_live.last_weekday(date(2026, 1, 15)), date(2026, 1, 30))      # 31st is a Saturday

    def test_fades_the_pre_fix_move_and_closes_next_noon(self):
        mt5 = Ticks()
        self.assertEqual(self.run_at(mt5, at(2026, 10, 29, 15, 0)), [])                   # not the last weekday
        self.run_at(mt5, at(2026, 10, 30, 15, 1))
        mt5.mid = 1.1030                                                                  # +0.27% into the fix
        self.run_at(mt5, at(2026, 10, 30, 15, 55))
        out = self.run_at(mt5, at(2026, 10, 30, 16, 3))
        eur = [m for m in out if "EURUSD" in m]
        self.assertTrue(eur and "SELL" in eur[0])
        self.assertEqual(self.run_at(mt5, at(2026, 10, 30, 16, 4)), [])                   # once only
        self.assertEqual([m for m in self.run_at(mt5, at(2026, 11, 2, 11, 59)) if "yopildi" in m], [])
        closed = self.run_at(mt5, at(2026, 11, 2, 12, 0))                                 # Monday noon London
        self.assertTrue(any("EURUSD yopildi" in m for m in closed))
        row = self.db.execute("SELECT status, side, prob FROM trades WHERE market = 'EURUSD'").fetchone()
        self.assertEqual((row["status"], row["side"]), ("CLOSED", -1))
        self.assertAlmostEqual(row["prob"], 1.1030 / 1.1000 - 1)

    def test_missing_samples_are_reported_not_traded(self):
        mt5 = Ticks()
        out = self.run_at(mt5, at(2026, 10, 30, 16, 5))
        self.assertTrue(all("yozilmagan" in m for m in out))
        self.assertEqual(mt5.sent, [])


if __name__ == "__main__":
    unittest.main()
