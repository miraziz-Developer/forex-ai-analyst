import random
import unittest

from forex_ai_analyst.forex import regime_system as rs


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
    def test_future_bars_never_change_past_signals_of_any_module(self):
        bars = random_bars()
        t = 700
        mutated = [dict(b) for b in bars]
        rng = random.Random(4)
        for b in mutated[t + 1:]:
            k = rng.uniform(0.7, 1.3)
            for key in ("open", "high", "low", "close"):
                b[key] *= k
        a, b = rs.module_signals(bars), rs.module_signals(mutated)
        for name in rs.MODULES:
            for attr in ("long_entry", "short_entry", "long_exit", "short_exit", "stop_distance"):
                self.assertEqual(getattr(a[name], attr)[:t + 1], getattr(b[name], attr)[:t + 1], (name, attr))

    def test_each_module_trades_only_in_its_regime(self):
        bars = random_bars()
        f = rs.features(bars)
        reg = rs.regimes(f)
        sig = rs.module_signals(bars)
        for i, (lo, sh) in enumerate(zip(sig["trend"].long_entry, sig["trend"].short_entry)):
            if lo or sh:
                self.assertEqual(reg[i], rs.TREND)
        for i, (lo, sh) in enumerate(zip(sig["range"].long_entry, sig["range"].short_entry)):
            if lo or sh:
                self.assertEqual(reg[i], rs.RANGE)


if __name__ == "__main__":
    unittest.main()
