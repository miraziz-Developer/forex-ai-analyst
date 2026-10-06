import unittest
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from forex_ai_analyst.forex import fix_study

LONDON = ZoneInfo("Europe/London")


def candle(day, hh, mm, bid, spread=0.0001):
    t = int(datetime(day.year, day.month, day.day, hh, mm, tzinfo=LONDON).astimezone(timezone.utc).timestamp() * 1000)
    return {"datetime": t, "bid_open": bid, "ask_open": bid + spread}


class FixStudyTests(unittest.TestCase):
    def test_month_ends_are_last_weekdays(self):
        ends = fix_study.month_ends()
        self.assertEqual(ends[0], date(2016, 1, 29))          # 31 Jan 2016 was a Sunday
        self.assertTrue(all(d.weekday() < 5 for d in ends))
        self.assertEqual(len(ends), 129)

    def test_fades_the_pre_fix_move_using_real_bid_and_ask(self):
        d, nxt = date(2024, 7, 31), date(2024, 8, 1)          # summer: London = UTC+1
        days = {d: [candle(d, 15, 0, 1.1000), candle(d, 15, 55, 1.1030), candle(d, 16, 3, 1.1040)],
                nxt: [candle(nxt, 12, 0, 1.1000)]}
        t = fix_study.trade("EURUSD", d, nxt, load=lambda pair, day: days[day])
        self.assertEqual(t["side"], -1)                        # price rose before the fix: sell
        entry, exit_ = 1.1040, 1.1001                          # sell at the bid, buy back at the ask
        self.assertAlmostEqual(t["net"], -(exit_ / entry - 1) - fix_study.MARKUP)

    def test_missing_candle_skips_the_day(self):
        d, nxt = date(2024, 7, 31), date(2024, 8, 1)
        days = {d: [candle(d, 15, 0, 1.1)], nxt: []}
        self.assertIsNone(fix_study.trade("EURUSD", d, nxt, load=lambda pair, day: days[day]))


if __name__ == "__main__":
    unittest.main()
