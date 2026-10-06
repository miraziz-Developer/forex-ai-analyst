import math
import random
import unittest
from datetime import datetime, timezone

from forex_ai_analyst.forex import h1_ml_study as h1

START = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)     # Monday 00:00 UTC


def synthetic(n, seed=3, spread=0.0001):
    rng, p, bars = random.Random(seed), 1.1, []
    for k in range(n):
        o = p
        p *= math.exp(rng.gauss(0, 0.001))
        hi, lo = max(o, p) * 1.0002, min(o, p) * 0.9998
        bars.append({"datetime": START + k * h1.HOUR, "bid_open": o, "bid_high": hi, "bid_low": lo, "bid_close": p,
                     "ask_open": o + spread, "ask_high": hi + spread, "ask_low": lo + spread, "ask_close": p + spread})
    return bars


class H1StudyTests(unittest.TestCase):
    def test_features_ignore_bars_after_the_decision(self):
        bars = synthetic(400)
        rows = h1.market_rows("EURUSD", bars, {}, 0)
        first = rows[0]
        later = [dict(b) for b in bars]
        for b in later:
            if b["datetime"] < first[2]["entry_ms"]:
                continue
            for key in ("bid_open", "bid_high", "bid_low", "bid_close", "ask_open", "ask_high", "ask_low", "ask_close"):
                b[key] *= 1.3
        again = h1.market_rows("EURUSD", later, {}, 0)
        self.assertEqual(first[0], again[0][0])

    def test_trade_pays_the_real_spread_both_ways(self):
        bars = synthetic(400, spread=0.0002)
        feats, label, meta = h1.market_rows("EURUSD", bars, {}, 0)[0]
        k = next(k for k, b in enumerate(bars) if b["datetime"] == meta["entry_ms"])
        entry, exit_ = bars[k], bars[k + h1.HOLD]
        self.assertAlmostEqual(meta["long"], exit_["bid_open"] / entry["ask_open"] - 1 - h1.MARKUP)
        self.assertAlmostEqual(meta["short"], 1 - exit_["ask_open"] / entry["bid_open"] - h1.MARKUP)
        self.assertLess(meta["long"] + meta["short"], 0)        # round trip of both sides costs two spreads

    def test_decisions_only_every_four_hours_on_weekdays(self):
        rows = h1.market_rows("EURUSD", synthetic(24 * 8), {}, 0)
        hours = {datetime.fromisoformat(r[2]["day"]).weekday() for r in rows}
        self.assertTrue(hours <= {0, 1, 2, 3, 4})
        self.assertTrue(all(sum(r[0][-15 - 6:-15]) == 1 for r in rows))     # one hour-of-day slot set


if __name__ == "__main__":
    unittest.main()


class ConfidenceTests(unittest.TestCase):
    def test_bands_use_distance_from_one_half_for_both_sides(self):
        trades = [{"prob": 0.56, "net": 0.001}, {"prob": 0.44, "net": -0.001}, {"prob": 0.70, "net": 0.002}]
        bands = {b["confidence"]: b for b in h1.by_confidence(trades)}
        self.assertEqual(bands["0.55-0.57"]["trades"], 2)
        self.assertEqual(bands["0.65-1.00"]["hit_rate"], 1.0)
