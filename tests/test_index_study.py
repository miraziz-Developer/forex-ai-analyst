import unittest
from unittest.mock import patch

from forex_ai_analyst.forex import index_study


def bars_from(rows, start_ms=1_600_000_000_000):
    return [{"datetime": start_ms + k * 86_400_000, "open": o, "high": h, "low": l, "close": c}
            for k, (o, h, l, c) in enumerate(rows)]


class IndexStudyTests(unittest.TestCase):
    def test_sma(self):
        self.assertEqual(index_study.sma([1, 2, 3, 4], 2), [None, 1.5, 2.5, 3.5])

    def test_exit_at_next_open_after_exit_signal(self):
        bars = bars_from([(100, 101, 99, 100)] * 3 + [(98, 99, 97.5, 98), (99, 101, 98, 100), (101, 102, 100, 101)])
        entry = [False, True, False, False, False, False]
        exit_ = [False, False, False, True, False, False]
        with patch.object(index_study, "signals", return_value=(entry, exit_)), \
                patch.object(index_study, "atr", return_value=[1.0] * 6):
            trades = index_study.backtest(bars, "IDX1", "US500")
        self.assertEqual(len(trades), 1)
        t = trades[0]
        self.assertEqual(t["bars_held"], 3)          # entered bar 2's open, exit signal bar 3, out at bar 4's open
        self.assertAlmostEqual(t["net"], 99 / 100 - 1 - index_study.COST - index_study.SWAP_PER_YEAR * 2 / 365)

    def test_stop_fills_at_stop_or_at_a_gap_open(self):
        rows = [(100, 101, 99, 100)] * 3 + [(100, 100, 96.5, 97), (95, 96, 94, 95)]
        entry = [False, True, False, False, False]
        exit_ = [False] * 5
        with patch.object(index_study, "signals", return_value=(entry, exit_)), \
                patch.object(index_study, "atr", return_value=[1.0] * 5):
            stopped = index_study.backtest(bars_from(rows), "IDX1", "US500")[0]
            gapped = index_study.backtest(bars_from(rows[:3] + [(100, 100, 98, 98), (95, 96, 94, 95)]),
                                          "IDX1", "US500")[0]
        self.assertAlmostEqual(stopped["net"] + index_study.COST + index_study.SWAP_PER_YEAR / 365, -0.03)
        self.assertAlmostEqual(gapped["net"] + index_study.COST + index_study.SWAP_PER_YEAR * 2 / 365, -0.05)


if __name__ == "__main__":
    unittest.main()
