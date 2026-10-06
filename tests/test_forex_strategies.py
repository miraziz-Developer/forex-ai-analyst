import random
import unittest

from forex_ai_analyst.forex import strategies


def random_bars(n=700, seed=5):
    rng, price, out = random.Random(seed), 1.10, []
    for i in range(n):
        o = price
        price *= 1 + rng.gauss(0, 0.002)
        out.append({"datetime": i * 3_600_000, "open": o, "high": max(o, price) * (1 + abs(rng.gauss(0, 0.0007))),
                    "low": min(o, price) * (1 - abs(rng.gauss(0, 0.0007))), "close": price, "volume": 0})
    return out


class NoLookAheadTests(unittest.TestCase):
    def test_future_bars_never_change_past_signals(self):
        bars = random_bars()
        t = 500
        mutated = [dict(b) for b in bars]
        rng = random.Random(2)
        for b in mutated[t + 1:]:
            k = rng.uniform(0.8, 1.2)
            for key in ("open", "high", "low", "close"):
                b[key] *= k
        for name, (rule, _) in strategies.STRATEGIES.items():
            a, b = rule(bars, 0.00001), rule(mutated, 0.00001)
            for left, right in ((a.long_entry, b.long_entry), (a.short_entry, b.short_entry),
                                (a.stop_distance, b.stop_distance)):
                self.assertEqual(left[:t + 1], right[:t + 1], name)


class RuleTests(unittest.TestCase):
    def test_pin_bar_at_resistance_is_a_short_with_stop_beyond_the_wick(self):
        bars = [{"datetime": i, "open": 1.0, "high": 1.01, "low": 0.99, "close": 1.0, "volume": 0} for i in range(40)]
        bars.append({"datetime": 40, "open": 1.000, "high": 1.0105, "low": 0.999, "close": 1.0002, "volume": 0})
        s = strategies.pinbar_snr(bars, 0.00001)
        self.assertTrue(s.short_entry[40])
        self.assertAlmostEqual(s.stop_distance[40], 1.0105 + 0.00025 - 1.0002)
        self.assertAlmostEqual(s.target_distance[40], 2.5 * s.stop_distance[40])

    def test_fvg_long_needs_price_back_inside_the_gap(self):
        flat = {"open": 1.0, "high": 1.001, "low": 0.999, "close": 1.0, "volume": 0}
        bars = [dict(flat, datetime=i) for i in range(8)]
        bars += [dict(flat, datetime=8, high=1.0, low=0.999),           # j-2
                 dict(flat, datetime=9, open=1.0, high=1.02, low=1.0, close=1.019),
                 dict(flat, datetime=10, open=1.019, high=1.025, low=1.005, close=1.02),  # gap 1.0 .. 1.005
                 dict(flat, datetime=11, open=1.02, high=1.021, low=1.002, close=1.003)]  # back inside
        s = strategies.ict_fvg(bars, 0.00001)
        self.assertTrue(s.long_entry[11])
        self.assertFalse(s.long_entry[10])


class DailyStampTests(unittest.TestCase):
    def test_fx_daily_bar_stamped_23utc_belongs_to_the_next_trading_day(self):
        import tempfile
        from datetime import datetime, timezone
        from pathlib import Path
        from unittest.mock import patch
        from forex_ai_analyst.forex import data
        payload = {"chart": {"result": [{"timestamp": [1759618800],        # Sun 2025-10-04 23:00 UTC = Mon London
                                         "indicators": {"quote": [{"open": [1.1], "high": [1.2], "low": [1.0],
                                                                   "close": [1.15], "volume": [0]}]}}]}}
        with tempfile.TemporaryDirectory() as cache, patch.object(data, "_get", return_value=payload), \
                patch.object(data, "CACHE_DIR", Path(cache)):
            bars = data.load_yahoo(data.Market("T", "TEST=X", 0.0), "1d")
        self.assertEqual(datetime.fromtimestamp(bars[0]["datetime"] / 1000, timezone.utc).date().isoformat(), "2025-10-05")


if __name__ == "__main__":
    unittest.main()
