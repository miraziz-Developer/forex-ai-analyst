import unittest

from forex_ai_analyst.forex import quiet_range_study as s

H = 3_600_000
START = 1_420_070_400_000            # 2015-01-01 00:00 UTC


def bar(i, c, o=None, spread=0.2):
    o = c if o is None else o
    h, low = max(o, c) + 1, min(o, c) - 1
    return {"datetime": START + i * H, "bid_open": o, "bid_high": h, "bid_low": low, "bid_close": c,
            "ask_open": o + spread, "ask_high": h + spread, "ask_low": low + spread, "ask_close": c + spread}


def rising_days(n_hours, step=0.4):
    """A slow, steady climb with small hourly wiggles: daily uptrend, low and even volatility."""
    return [bar(i, 1000 + i * step + (2 if i % 2 else -2)) for i in range(n_hours)]


class QuietRangeTests(unittest.TestCase):
    def test_daily_context_uses_completed_days_only(self):
        bars = rising_days(24 * 300)
        ctx = s.daily_context(bars)
        last_day = sorted(ctx)[-1]
        trend, quiet = ctx[last_day]
        self.assertTrue(trend)
        first = sorted(ctx)[0]
        self.assertEqual(ctx[first], (False, False))             # no 200-day history yet

    def test_dip_in_an_uptrend_buys_and_exits_at_the_middle_band(self):
        bars = rising_days(24 * 300)
        n = len(bars)
        base = bars[-1]["bid_close"]
        dip = [bar(n + k, base - 40 * (k + 1)) for k in range(2)]
        back = [bar(n + 2 + k, base - 80 + 30 * (k + 1)) for k in range(6)]
        trades = s.trades_for("US500", bars + dip + back, quiet_filter=False)
        self.assertTrue(trades)
        self.assertEqual(trades[-1]["how"], "middle")
        self.assertGreater(trades[-1]["r_gross"], 0)

    def test_no_trade_without_the_daily_uptrend(self):
        bars = [bar(i, 2000 - i * 0.4 + (2 if i % 2 else -2)) for i in range(24 * 300)]
        n, base = len(bars), bars[-1]["bid_close"]
        dip = [bar(n + k, base - 40 * (k + 1)) for k in range(2)]
        self.assertEqual(s.trades_for("US500", bars + dip + [bar(n + 2, base)] * 3, quiet_filter=False), [])

    def test_stats(self):
        rows = [{"market": "US500", "entry_ms": START + k * 40 * 24 * H, "r": 0.5 if k % 3 else -1.0,
                 "r_gross": 0.5} for k in range(30)]
        st = s.stats(rows)
        self.assertEqual(st["trades"], 30)
        self.assertEqual(st["markets_positive"], 0)


if __name__ == "__main__":
    unittest.main()
