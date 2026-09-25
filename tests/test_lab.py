import random
import unittest
from datetime import datetime, timezone

from forex_ai_analyst.lab import data, strategies, validation
from forex_ai_analyst.lab.engine import Costs, Signals, Sizing, run

HOUR = 3_600_000
FREE = Costs(0.0, 0.0, charge_funding=False)


def bars_from(prices, start=0):
    """Candles where open=previous close; each tuple is (high, low, close)."""
    out, prev = [], prices[0][2]
    for i, (h, l, c) in enumerate(prices):
        out.append({"datetime": start + i * HOUR, "open": prev, "high": h, "low": l, "close": c, "volume": 1.0})
        prev = c
    return out


def signals(n, long_at=(), exit_at=(), stop=5.0, trail=None, max_bars=None):
    return Signals([i in long_at for i in range(n)], [False] * n, [i in exit_at for i in range(n)], [False] * n,
                   [stop] * n, [trail] * n if trail else None, None, max_bars)


class EngineTests(unittest.TestCase):
    def test_entry_is_next_open_and_exit_signal_fills_at_following_open(self):
        bars = bars_from([(101, 99, 100), (101, 99, 100), (111, 100, 110), (111, 109, 110), (121, 110, 120)])
        trade = run(bars, signals(5, long_at={0}, exit_at={2}), costs=FREE, sizing=Sizing(0.01, 100)).trades[0]
        self.assertEqual((trade["entry"], trade["exit"], trade["reason"]), (100, 110, "signal"))
        # risk 1% of 10,000 = 100 USDT over a 5-point stop -> 20 units; +10 points = +200
        self.assertAlmostEqual(trade["qty"], 20)
        self.assertAlmostEqual(trade["net"], 200)
        self.assertAlmostEqual(trade["r"], 2.0)

    def test_stop_is_hit_intrabar_and_a_gap_fills_at_the_worse_open(self):
        bars = bars_from([(101, 99, 100), (101, 96, 97), (98, 90, 91)])
        stopped = run(bars, signals(3, long_at={0}), costs=FREE, sizing=Sizing(0.01, 100)).trades[0]
        self.assertEqual((stopped["exit"], stopped["reason"]), (95, "stop"))
        gap = bars_from([(101, 99, 100), (101, 99, 100), (91, 85, 88)])
        gap[2]["open"] = 90  # gaps below the 95 stop
        trade = run(gap, signals(3, long_at={0}), costs=FREE, sizing=Sizing(0.01, 100)).trades[0]
        self.assertEqual(trade["exit"], 90)

    def test_costs_and_funding_reduce_the_result(self):
        bars = bars_from([(101, 99, 100)] * 6)
        funding = [(bars[3]["datetime"], 0.001)]  # longs pay 0.1% while holding
        trade = run(bars, signals(6, long_at={0}, exit_at={4}), funding, Costs(0.05, 0.0), Sizing(0.01, 100)).trades[0]
        notional = trade["entry"] * trade["qty"]
        self.assertAlmostEqual(trade["fees"], notional * 0.0005 * 2)
        self.assertAlmostEqual(trade["funding"], notional * 0.001)
        self.assertAlmostEqual(trade["net"], -trade["fees"] - trade["funding"])

    def test_leverage_cap_limits_position_size(self):
        bars = bars_from([(101, 99, 100)] * 4)
        trade = run(bars, signals(4, long_at={0}, exit_at={2}, stop=0.01), costs=FREE,
                    sizing=Sizing(0.01, 2.0)).trades[0]
        self.assertAlmostEqual(trade["qty"] * trade["entry"], 20_000)

    def test_trailing_stop_ratchets_up_only(self):
        bars = bars_from([(101, 99, 100), (111, 100, 110), (121, 110, 120), (121, 113, 114)])
        trade = run(bars, signals(4, long_at={0}, stop=5, trail=5), costs=FREE, sizing=Sizing(0.01, 100)).trades[0]
        self.assertEqual((trade["exit"], trade["reason"]), (116, "stop"))

    def test_equity_curve_has_one_point_per_bar(self):
        bars = bars_from([(101, 99, 100)] * 10)
        self.assertEqual(len(run(bars, signals(10), costs=FREE).equity), 10)


class StrategyAndDataTests(unittest.TestCase):
    def test_every_family_builds_and_runs(self):
        prices, p = [], 100.0
        for i in range(400):
            p *= 1.002 if (i // 50) % 2 == 0 else 0.998
            prices.append((p * 1.01, p * 0.99, p))
        bars = bars_from(prices)
        for config in strategies.configs([1]):
            sig = strategies.build_signals(config, bars)
            self.assertEqual(len(sig.long_entry), len(bars), config["key"])
            run(bars, sig)

    def test_donchian_breakout_uses_only_prior_bars(self):
        bars = bars_from([(101, 99, 100)] * 25 + [(106, 100, 105)])
        sig = strategies.donchian(bars, entry_n=20, exit_n=10, stop_atr=2.0, sides="long")
        self.assertTrue(sig.long_entry[-1])
        self.assertFalse(any(sig.long_entry[:-1]))

    def test_resample_keeps_only_complete_buckets(self):
        hourly = bars_from([(100 + i, 90 + i, 95 + i) for i in range(10)])
        four = data.resample(hourly, 4)
        self.assertEqual(len(four), 2)
        self.assertEqual((four[0]["high"], four[0]["low"], four[0]["close"]), (103, 90, 98))

    def test_months_covers_partial_end_month(self):
        start = datetime(2025, 11, 1, tzinfo=timezone.utc)
        self.assertEqual(data.months(start, datetime(2026, 2, 1, tzinfo=timezone.utc)), ["2025-11", "2025-12", "2026-01"])
        self.assertEqual(data.months(start, datetime(2026, 1, 15, tzinfo=timezone.utc)), ["2025-11", "2025-12", "2026-01"])


class ValidationTests(unittest.TestCase):
    def test_walk_forward_windows_are_contiguous_and_train_precedes_test(self):
        windows = validation.walk_forward_windows("2021-01-01", "2024-01-01")
        self.assertEqual(windows[0], ("2021-01-01", "2023-01-01", "2023-01-01", "2023-07-01"))
        self.assertEqual(windows[-1][3], "2024-01-01")
        for train_start, train_end, test_start, _ in windows:
            self.assertLess(train_start, train_end)
            self.assertEqual(train_end, test_start)

    def test_deflated_sharpe_penalises_many_trials(self):
        rng = random.Random(1)
        values = [0.001 + rng.gauss(0, 0.01) for _ in range(700)]
        few = validation.deflated_sharpe(values, [0.2, 0.5, 1.0])
        many = validation.deflated_sharpe(values, [rng.gauss(0, 1.0) for _ in range(500)])
        self.assertGreater(few, many)

    def test_oos_stream_contains_only_test_window_days(self):
        days = [f"2021-{m:02d}-{d:02d}" for m in range(1, 13) for d in range(1, 29)]
        a = {d: (0.01 if d < "2021-07-01" else -0.01) + (0.001 if i % 2 else -0.001) for i, d in enumerate(days)}
        b = {d: 0.0005 + (0.001 if i % 2 else -0.001) for i, d in enumerate(days)}
        configs = [{"key": "a", "family": "f", "tf": 1, "index": (0,)},
                   {"key": "b", "family": "f", "tf": 1, "index": (1,)}]
        result = validation.walk_forward(configs, {"a": a, "b": b},
                                         [("2021-01-01", "2021-07-01", "2021-07-01", "2022-01-01")])
        self.assertEqual(len(result["f"]["selections"]), 1)
        self.assertTrue(all(d >= "2021-07-01" for d in result["f"]["oos"]))

    def test_stats_reports_drawdown(self):
        s = validation.stats({"2021-01-01": 0.1, "2021-01-02": -0.5, "2021-01-03": 0.2})
        self.assertAlmostEqual(s["max_drawdown_pct"], -50.0)


if __name__ == "__main__":
    unittest.main()
