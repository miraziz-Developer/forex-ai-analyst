import unittest

from forex_ai_analyst.forex.ml_exits import replay, replay_smart, swing_levels


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



def climb():
    """Prior context with a swing low at 99 (index 2), then a long that rises and later fails."""
    pre = [bar(101, 102, 100, 101), bar(100.5, 101, 99.5, 100), bar(99.5, 100, 99, 99.5),
           bar(99.5, 100.5, 99.5, 100), bar(100, 101, 99.8, 100)]
    return pre


class SmartTrailTests(unittest.TestCase):
    def test_swing_low_known_only_two_bars_later(self):
        bars = climb()
        levels = swing_levels(bars, 1)
        self.assertEqual(levels[3], None)
        self.assertEqual(levels[4], 99)

    def test_wick_through_level_that_closes_back_does_not_exit(self):
        bars = climb() + [bar(100, 100.5, 97.5, 100.2)] + [bar(100.2, 100.4, 100, 100.3)] * 4
        # soft stop 99 - 0.5 = 98.5; the 97.5 wick sweeps it but closes at 100.2: held to the time exit
        self.assertAlmostEqual(replay_smart(bars, 4, 1, 1.0), 100.3 / 100 - 1)

    def test_close_below_structure_exits_at_that_close(self):
        bars = climb() + [bar(100, 100.5, 98, 98.2)] + [bar(98.2, 99, 98, 99)] * 4
        self.assertAlmostEqual(replay_smart(bars, 4, 1, 1.0), 98.2 / 100 - 1)

    def test_profit_lock_moves_soft_stop_to_breakeven_after_one_atr(self):
        bars = climb() + [bar(100, 101.5, 99.8, 101.2), bar(101.2, 101.3, 99.6, 99.7)] + [bar(99.7, 99.7, 99.7, 99.7)] * 3
        self.assertAlmostEqual(replay_smart(bars, 4, 1, 1.0, arm=1.0), 99.7 / 100 - 1)
        self.assertAlmostEqual(replay_smart(bars, 4, 1, 1.0), 99.7 / 100 - 1)      # SM1: 98.5 never closed below


if __name__ == "__main__":
    unittest.main()
