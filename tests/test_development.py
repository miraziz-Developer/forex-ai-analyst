import random
import unittest

from forex_ai_analyst.forex import development as dev
from forex_ai_analyst.forex.regime_system import features


def random_bars(n=900, seed=8):
    rng, price, out = random.Random(seed), 1.10, []
    drift = 0.0
    for i in range(n):
        if i % 150 == 0:
            drift = rng.choice([-0.002, 0.0, 0.002])
        o = price
        price *= 1 + drift + rng.gauss(0, 0.004)
        out.append({"datetime": i * 86_400_000, "open": o, "high": max(o, price) * (1 + abs(rng.gauss(0, 0.002))),
                    "low": min(o, price) * (1 - abs(rng.gauss(0, 0.002))), "close": price, "volume": 0})
    return out


class NoLookAheadTests(unittest.TestCase):
    def test_every_grid_config_ignores_future_bars(self):
        bars = random_bars()
        t = 700
        mutated = [dict(b) for b in bars]
        rng = random.Random(9)
        for b in mutated[t + 1:]:
            k = rng.uniform(0.7, 1.3)
            for key in ("open", "high", "low", "close"):
                b[key] *= k
        fa, fb = features(bars), features(mutated)
        sa, sb = dev._sweeps(bars), dev._sweeps(mutated)
        va, vb = dev._fvg_touch(bars), dev._fvg_touch(mutated)
        for cfg in dev.grid(dev.RANGE_GRID):
            a, b = dev.range_signals(bars, fa, cfg, sa), dev.range_signals(mutated, fb, cfg, sb)
            self.assertEqual(a.long_entry[:t + 1], b.long_entry[:t + 1], cfg)
            self.assertEqual(a.short_entry[:t + 1], b.short_entry[:t + 1], cfg)
        for cfg in dev.grid(dev.TREND_GRID):
            a, b = dev.trend_signals(bars, fa, cfg, va), dev.trend_signals(mutated, fb, cfg, vb)
            self.assertEqual(a.long_entry[:t + 1], b.long_entry[:t + 1], cfg)
            self.assertEqual(a.short_entry[:t + 1], b.short_entry[:t + 1], cfg)

    def test_grid_sizes_match_the_pre_registration(self):
        self.assertEqual(len(dev.grid(dev.RANGE_GRID)), 72)
        self.assertEqual(len(dev.grid(dev.TREND_GRID)), 72)


if __name__ == "__main__":
    unittest.main()
