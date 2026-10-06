import copy
import random
import unittest

from forex_ai_analyst.forex import fx_factors as fx


def synthetic(n_months=100, seed=3):
    rng = random.Random(seed)
    months = [f"{2004 + i // 12}-{i % 12 + 1:02d}" for i in range(n_months)]
    spots = {c: {} for c in fx.CURRENCIES}
    rates = {c: {} for c in ("USD",) + fx.CURRENCIES}
    for c in fx.CURRENCIES:
        price = 1.0
        for m in months:
            price *= 1 + rng.gauss(0, 0.02)
            spots[c][m] = price
    for c in rates:
        level = rng.uniform(0, 5)
        for m in months:
            level = max(-0.5, level + rng.gauss(0, 0.1))
            rates[c][m] = level
    return months, spots, rates


class NoLookAheadTests(unittest.TestCase):
    def test_decisions_ignore_everything_after_the_decision_month(self):
        months, spots, rates = synthetic()
        _, excess = fx.build_panel(spots, rates)
        i = 80
        base = fx.signals(i, months, spots, rates, excess)
        spots2, rates2 = copy.deepcopy(spots), copy.deepcopy(rates)
        for m in months[i + 1:]:
            for c in fx.CURRENCIES:
                spots2[c][m] *= 3
        for c in rates2:
            for m in months[i:]:            # the decision month's own rate is not yet published
                rates2[c][m] += 7
        _, excess2 = fx.build_panel(spots2, rates2)
        self.assertEqual(base, fx.signals(i, months, spots2, rates2, excess2))
        self.assertEqual(set(base), {"carry", "trend", "value"})

    def test_rank_weights_are_dollar_neutral(self):
        w = fx._rank_weights({"USD": 0.0, "EUR": 1, "GBP": 2, "JPY": -3, "AUD": 4, "NZD": 5, "CAD": 0.5, "CHF": -1})
        self.assertAlmostEqual(sum(w.values()), 0.0)
        self.assertGreater(w["NZD"], 0)
        self.assertLess(w["JPY"], 0)


if __name__ == "__main__":
    unittest.main()
