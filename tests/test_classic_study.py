import unittest
from datetime import datetime, timezone

from forex_ai_analyst.forex import classic_study as s

H = 3_600_000


def bar(ms, o, h, l, c, spread=0.0002):
    return {"datetime": ms, "bid_open": o, "bid_high": h, "bid_low": l, "bid_close": c,
            "ask_open": o + spread, "ask_high": h + spread, "ask_low": l + spread, "ask_close": c + spread}


def ms(y, m, d, h):
    return int(datetime(y, m, d, h, tzinfo=timezone.utc).timestamp() * 1000)


class ClassicTest(unittest.TestCase):
    def test_fx_day_rolls_at_new_york_five_pm(self):
        self.assertEqual(str(s.fx_day(ms(2025, 1, 6, 21))), "2025-01-06")      # 16:00 New York (winter)
        self.assertEqual(str(s.fx_day(ms(2025, 1, 6, 22))), "2025-01-07")      # 17:00 New York

    def test_book_checks_stop_before_target(self):
        book = s.Book("EURUSD", "t")
        book.open(1, 1.1000, 1.0990, 1.1020, 0)
        book.step(bar(H, 1.1000, 1.1030, 1.0980, 1.1000))
        self.assertEqual(book.trades[0]["how"], "stop")
        self.assertLess(book.trades[0]["r"], -1.0)                              # stop plus the extra cost

    def test_pivot_break_buys_through_pp_and_reaches_r2(self):
        prev = [bar(ms(2025, 1, 7, h), 1.10, 1.11 if h == 5 else 1.10, 1.09 if h == 9 else 1.10, 1.10)
                for h in range(0, 21)]
        # prev day: H 1.1100, L 1.0900, C 1.1000 -> PP 1.1000, R1 1.1100, R2 1.1200, S1 1.0900
        today = [bar(ms(2025, 1, 7, 22), 1.0990, 1.0995, 1.0985, 1.0992),
                 bar(ms(2025, 1, 7, 23), 1.0992, 1.1010, 1.0990, 1.1008),
                 bar(ms(2025, 1, 8, 0), 1.1008, 1.1150, 1.1005, 1.1140),
                 bar(ms(2025, 1, 8, 1), 1.1140, 1.1250, 1.1130, 1.1240)]
        trades = s.pivot_break("EURUSD", prev + today)
        self.assertEqual(trades[0]["side"], 1)
        self.assertEqual(trades[0]["how"], "target")
        self.assertGreater(trades[0]["r"], 0)

    def test_trendline_bounce_buys_a_touch_of_a_rising_line(self):
        prices = []
        for leg in range(4):                    # rising zigzag: swing lows at 1.000, 1.010, 1.020 ...
            base = 1.0 + 0.01 * leg
            prices += [base + 0.002 * k for k in range(12)] + [base + 0.022 - 0.0012 * k for k in range(10)]
        bars = [bar(i * H, p, p + 0.0003, p - 0.0003, p, spread=0.00001) for i, p in enumerate(prices)]
        trades = s.trendline_bounce("EURUSD", bars)
        self.assertTrue(any(t["side"] == 1 for t in trades))

    def test_report_gate_needs_both_halves(self):
        rows = [{"market": "EURUSD", "entry_ms": s.SPLIT - 1, "r": 1.0, "r_gross": 1.0}] * 200 + \
               [{"market": "EURUSD", "entry_ms": s.SPLIT + 1, "r": -0.5, "r_gross": -0.5}] * 200
        self.assertFalse(s.report(rows)["passes"])


if __name__ == "__main__":
    unittest.main()
