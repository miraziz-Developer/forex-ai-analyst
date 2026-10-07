import unittest

from forex_ai_analyst.forex import three_ma_study as s


def bar(i, mid, spread=0.0002):
    return {"datetime": i * s.H4, "bid_open": mid, "ask_open": mid + spread, "close": mid + spread / 2}


class ThreeMaTest(unittest.TestCase):
    def test_trend_up_then_down_gives_one_long_closed_on_the_cross(self):
        prices = [1.0] * 30 + [1.0 + 0.01 * i for i in range(30)] + [1.29 - 0.01 * i for i in range(30)]
        trades = s.trades("EURUSD", [bar(i, p) for i, p in enumerate(prices)])
        self.assertEqual(trades[0]["side"], 1)
        self.assertGreater(trades[0]["gross"], 0)
        self.assertLess(trades[0]["net"], trades[0]["gross"])
        self.assertGreater(trades[0]["exit_ms"], trades[0]["entry_ms"])

    def test_signals_use_only_closed_bars(self):
        prices = [1.0] * 30 + [1.0 + 0.01 * i for i in range(30)]
        bars = [bar(i, p) for i, p in enumerate(prices)]
        full = s.trades("EURUSD", bars + [bar(60 + i, 1.29 - 0.05 * i) for i in range(20)])
        cut = s.trades("EURUSD", bars + [bar(60 + i, 1.29 - 0.05 * i) for i in range(5)])
        self.assertEqual(full[0]["entry_ms"], cut[0]["entry_ms"]) if cut else None

    def test_extra_cost_scales_with_pip_size(self):
        self.assertAlmostEqual(s.extra_cost("EURUSD", 1.0), 0.00007)
        self.assertAlmostEqual(s.extra_cost("USDJPY", 100.0), 0.00007)
        self.assertEqual(s.extra_cost("XAUUSD", 2000.0), 0.0002)


if __name__ == "__main__":
    unittest.main()
