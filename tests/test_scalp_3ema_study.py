import unittest

from forex_ai_analyst.forex import scalp_3ema_study as s

START = 1_700_000_000_000 // s.H1 * s.H1


def minute(i, mid, half_range=0.00005, spread=0.00002):
    return {"datetime": START + i * s.M1,
            "bid_open": mid, "bid_high": mid + half_range, "bid_low": mid - half_range, "bid_close": mid,
            "ask_open": mid + spread, "ask_high": mid + half_range + spread, "ask_low": mid - half_range + spread,
            "ask_close": mid + spread}


def uptrend_then(tail):
    """Thirty hours of a steady climb (stacks the H1 EMAs up), then the given mid prices minute by minute."""
    bars = [minute(i, 1.1 + i * 0.000002) for i in range(30 * 60)]
    return bars + [minute(len(bars) + j, p) for j, p in enumerate(tail)]


class Scalp3EmaTest(unittest.TestCase):
    def test_stacked(self):
        self.assertEqual(s.stacked(3, 2, 1), 1)
        self.assertEqual(s.stacked(1, 2, 3), -1)
        self.assertEqual(s.stacked(2, 3, 1), 0)

    def test_aggregate_uses_mid_prices_and_bucket_bounds(self):
        bars = s.aggregate([minute(i, 1.0 + i * 0.001) for i in range(10)], s.M5)
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[0]["end"] - bars[0]["start"], s.M5)
        self.assertAlmostEqual(bars[0]["close"], 1.004 + 0.00001)
        self.assertAlmostEqual(bars[1]["high"], 1.009 + 0.00005 + 0.00001)

    def test_buy_stop_fills_on_ask_and_hits_target(self):
        last = 1.1 + (30 * 60 - 1) * 0.000002
        trades = s.simulate("EURUSD", uptrend_then([last + 0.0001 * k for k in range(1, 200)]), s.Params())
        self.assertTrue(trades)
        self.assertEqual(trades[0]["side"], 1)
        self.assertAlmostEqual(trades[0]["r"], 1.0, places=6)

    def test_stop_is_checked_first_within_a_minute(self):
        last = 1.1 + (30 * 60 - 1) * 0.000002
        # a jump fills the buy stop, then the next minute spans both stop and target: counted as a loss
        bars = uptrend_then([last + 0.0012] * 61)
        bars[-60]["bid_low"], bars[-60]["bid_high"] = last - 0.01, last + 0.01
        trades = s.simulate("EURUSD", bars, s.Params())
        self.assertTrue(trades)
        self.assertLess(trades[0]["r"], 0)

    def test_unfilled_order_expires(self):
        last = 1.1 + (30 * 60 - 1) * 0.000002
        trades = s.simulate("EURUSD", uptrend_then([last] * 300), s.Params(offset_pips=50))
        self.assertEqual(trades, [])

    def test_evaluate(self):
        trades = [{"pair": "EURUSD", "day": "2025-01-0%d" % (i % 5 + 1), "net": 0.001 if i % 2 else -0.0005,
                   "r": 1.0 if i % 2 else -1.0, "risk_pips": 10, "spread_pips": 0.2} for i in range(20)]
        report = s.evaluate(trades)
        self.assertEqual(report["trades"], 20)
        self.assertEqual(report["profit_factor"], 2.0)
        self.assertEqual(report["hit_rate"], 0.5)


if __name__ == "__main__":
    unittest.main()
