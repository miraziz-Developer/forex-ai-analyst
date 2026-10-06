import unittest
from datetime import datetime, timezone

from forex_ai_analyst.forex import tom_study


def bars_on(days, closes):
    return [{"datetime": int(datetime.fromisoformat(d).replace(tzinfo=timezone.utc).timestamp() * 1000),
             "open": c, "high": c, "low": c, "close": c} for d, c in zip(days, closes)]


class TomTests(unittest.TestCase):
    def test_enters_day_minus_two_and_exits_day_three(self):
        days = ["2024-01-26", "2024-01-29", "2024-01-30", "2024-01-31", "2024-02-01", "2024-02-02", "2024-02-05",
                "2024-02-06"]
        closes = [100, 101, 102, 103, 104, 105, 106, 107]
        t = tom_study.tom_trades(bars_on(days, closes), "US500")
        self.assertEqual(len(t), 1)
        self.assertEqual(t[0]["entry_day"], "2024-01-30")                  # second-to-last trading day
        held = 6                                                            # Jan 30 -> Feb 5 (calendar days)
        self.assertAlmostEqual(t[0]["net"], 106 / 102 - 1 - tom_study.COST - tom_study.SWAP_PER_YEAR * held / 365)
        self.assertEqual(t[0]["month"], "2024-02")


if __name__ == "__main__":
    unittest.main()
