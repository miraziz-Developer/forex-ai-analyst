import random
import unittest

from forex_ai_analyst.lab import candidates
from forex_ai_analyst.lab.validation import deflated_sharpe

DAY = 86_400_000


def random_bars(n=900, seed=5, start=1_600_000_000_000, step=4 * 3_600_000):
    rng, price, out = random.Random(seed), 100.0, []
    for i in range(n):
        o = price
        price *= 1 + rng.gauss(0.0003, 0.02)
        out.append({"datetime": start + i * step, "open": o, "high": max(o, price) * (1 + abs(rng.gauss(0, 0.005))),
                    "low": min(o, price) * (1 - abs(rng.gauss(0, 0.005))), "close": price, "volume": 1.0})
    return out


def signal_lists(signals):
    return [signals.long_entry, signals.long_exit, signals.stop_distance, signals.target_distance or []]


class NoLookAheadTests(unittest.TestCase):
    def test_future_bars_never_change_past_signals(self):
        bars = random_bars()
        t = 700
        mutated = [dict(b) for b in bars]
        rng = random.Random(1)
        for b in mutated[t + 1:]:
            scale = rng.uniform(0.5, 2.0)
            for k in ("open", "high", "low", "close"):
                b[k] *= scale
        for name, fn in candidates.CANDIDATES.items():
            for a, b in zip(signal_lists(fn(bars)), signal_lists(fn(mutated))):
                self.assertEqual(a[:t + 1], b[:t + 1], name)

    def test_pivot_is_known_only_k_bars_after_it(self):
        bars = [{"high": h, "low": h - 1} for h in (1, 2, 3, 9, 3, 2, 1, 1, 1)]
        last_high, _, _ = candidates.confirmed_pivots(bars, k=3)
        self.assertIsNone(last_high[5])          # the peak at index 3 needs bars 4..6 to close
        self.assertEqual(last_high[6], 9)


class RuleTests(unittest.TestCase):
    def test_rsi_is_bounded_and_extreme_on_one_way_moves(self):
        up = candidates.rsi([float(i) for i in range(1, 30)], 2)
        self.assertEqual(up[-1], 100.0)
        mixed = candidates.rsi([100 + (i % 3) - (i % 5) for i in range(60)], 2)
        self.assertTrue(all(v is None or 0 <= v <= 100 for v in mixed))

    def test_sweep_needs_a_break_below_and_a_close_back_above_the_swing_low(self):
        bars = random_bars(400, seed=9)
        signals = candidates.liquidity_sweep(bars)
        _, last_low, _ = candidates.confirmed_pivots(bars)
        for i, fired in enumerate(signals.long_entry):
            if fired:
                self.assertLess(bars[i]["low"], last_low[i])
                self.assertGreater(bars[i]["close"], last_low[i])
                self.assertAlmostEqual(signals.target_distance[i], 2 * signals.stop_distance[i])

    def test_momentum_rotation_decides_from_past_closes_only(self):
        daily = {p: random_bars(120, seed=s, step=DAY) for s, p in enumerate(("A", "B", "C", "D"))}
        base = candidates.xs_momentum_returns(daily, {}, lookback=10, hold=2, rebalance_days=5)
        cut = sorted(base)[60]
        mutated = {p: [dict(b, close=b["close"] * (3 if candidates._day(b["datetime"]) > cut else 1)) for b in bars]
                   for p, bars in daily.items()}
        after = candidates.xs_momentum_returns(mutated, {}, lookback=10, hold=2, rebalance_days=5)
        self.assertEqual({d: r for d, r in base.items() if d <= cut}, {d: r for d, r in after.items() if d <= cut})


class RegimeTests(unittest.TestCase):
    def test_regime_uses_only_completed_daily_closes(self):
        daily = [{"datetime": i * DAY, "close": 100.0} for i in range(5)] + [{"datetime": 5 * DAY, "close": 200.0}]
        bars = [{"datetime": 5 * DAY + h * 4 * 3_600_000} for h in range(7)]
        regime = candidates.btc_bull_regime(bars, daily, sma_days=3)
        self.assertFalse(regime[0])        # day 5 (close 200) has not closed during the first bars of day 5
        self.assertTrue(regime[5])         # its close is known once the 4h bar ending at day 6 closes

    def test_filter_blocks_longs_in_bear_and_shorts_in_bull(self):
        bars = random_bars(600, seed=4)
        bull = [i % 2 == 0 for i in range(len(bars))]
        s = candidates.regime_donchian(bars, bull, shorts=True)
        self.assertTrue(all(bull[i] for i, e in enumerate(s.long_entry) if e))
        self.assertTrue(all(not bull[i] for i, e in enumerate(s.short_entry) if e))


class DeflatedSharpeTests(unittest.TestCase):
    def test_more_trials_can_only_lower_the_probability(self):
        rng = random.Random(3)
        values = [rng.gauss(0.001, 0.01) for _ in range(800)]
        trials = [0.2, 0.8, 1.1, -0.3, 0.5]
        few, many = deflated_sharpe(values, trials), deflated_sharpe(values, trials, n_trials=179)
        self.assertLessEqual(many, few)


if __name__ == "__main__":
    unittest.main()
