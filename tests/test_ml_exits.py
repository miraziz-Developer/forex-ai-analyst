import unittest

from forex_ai_analyst.forex.ml_exits import replay


def bar(o, h, l, c):
    return {"open": o, "high": h, "low": l, "close": c}


FLAT = [bar(100, 100, 100, 100)]


class ReplayTests(unittest.TestCase):
    def test_time_exit_without_levels(self):
        bars = FLAT + [bar(100, 101, 99, 100.5)] * 4 + [bar(100.5, 102, 100, 102)]
        self.assertAlmostEqual(replay(bars, 0, 1, 1.0), 0.02)

    def test_take_profit_hit_midweek_instead_of_giving_it_back(self):
        bars = (FLAT + [bar(100, 101, 99, 100), bar(100, 102, 99.5, 101), bar(101, 101, 95, 96)]
                + [bar(96, 96, 96, 96)] * 2)
        self.assertAlmostEqual(replay(bars, 0, 1, 1.0, tp=1.5), 0.015)
        self.assertAlmostEqual(replay(bars, 0, 1, 1.0), -0.03)      # the 3 ATR stop at 97

    def test_stop_counts_first_when_both_levels_touched_on_one_day(self):
        bars = FLAT + [bar(100, 102, 96, 100)] + [bar(100, 100, 100, 100)] * 4
        self.assertAlmostEqual(replay(bars, 0, 1, 1.0, tp=1.5), -0.03)

    def test_trailing_stop_locks_profit_for_a_short(self):
        bars = FLAT + [bar(100, 100, 97, 97), bar(97, 97, 95, 95), bar(95, 97, 95, 97)] + [bar(97, 97, 97, 97)] * 2
        self.assertAlmostEqual(replay(bars, 0, -1, 1.0, trail=1.5, arm=1.0), 0.035)

    def test_gap_through_stop_fills_at_open(self):
        bars = FLAT + [bar(100, 100, 100, 100), bar(95, 96, 94, 95)] + [bar(95, 95, 95, 95)] * 3
        self.assertAlmostEqual(replay(bars, 0, 1, 1.0), -0.05)


if __name__ == "__main__":
    unittest.main()
