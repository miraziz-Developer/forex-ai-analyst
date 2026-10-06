import math
import random
import unittest
from unittest.mock import patch

from forex_ai_analyst.forex import ml_xs


def fake_closes(n, seed=1):
    rng = random.Random(seed)
    out = {"USD": [1.0] * n}
    for c in ml_xs.CCYS[1:]:
        p, series = 1.0, []
        for _ in range(n):
            p *= math.exp(rng.gauss(0, 0.005))
            series.append(p)
        out[c] = series
    return out


class CrossSectionTests(unittest.TestCase):
    def test_relative_prices_sum_to_zero_each_day(self):
        rel = ml_xs.relative_log(fake_closes(30))
        for i in range(30):
            self.assertAlmostEqual(sum(rel[c][i] for c in ml_xs.CCYS), 0.0)

    def test_features_at_day_i_ignore_later_prices(self):
        closes = fake_closes(320)
        days = [f"2020-{1 + k // 28:02d}-{1 + k % 28:02d}" for k in range(320)]
        rates = {c: {f"2020-{m:02d}": float(k) for m in range(1, 13)} for k, c in enumerate(ml_xs.CCYS)}
        cot_row = {"cot_net": 0.1, "cot_pct52": 50.0, "cot_chg4": 0.0}
        with patch.object(ml_xs, "cot_for", return_value=cot_row):
            before = ml_xs.raw_features(ml_xs.relative_log(closes), rates, {}, days, 300)
            for c in ml_xs.CCYS[1:]:
                closes[c][301:] = [x * 1.5 for x in closes[c][301:]]
            after = ml_xs.raw_features(ml_xs.relative_log(closes), rates, {}, days, 300)
        self.assertEqual(before, after)

    def test_portfolio_is_long_top_short_bottom_and_charges_non_usd_legs(self):
        meta = [{"day": "2020-01-06", "exit_day": "2020-01-13", "ccy": c, "usd_ret": r}
                for c, r in zip(ml_xs.CCYS, [0.0, 0.01, -0.01, 0.02, -0.02, 0.0, 0.0, 0.0])]
        scores = [0, 5, -5, 6, -6, 1, 1, 1]          # longs JPY, EUR; shorts AUD, GBP
        costs = {c: 0.0001 for c in ml_xs.CCYS}
        costs["USD"] = 0.0
        week = ml_xs.portfolio(meta, scores, costs)[0]
        self.assertEqual(sorted(week["longs"]), ["EUR", "JPY"])
        self.assertEqual(sorted(week["shorts"]), ["AUD", "GBP"])
        self.assertAlmostEqual(week["gross"], 0.5 * (0.01 + 0.02) - 0.5 * (-0.01 - 0.02))
        swap = ml_xs.SWAP_PER_YEAR * 2 * 7 / 365
        self.assertAlmostEqual(week["net"], week["gross"] - 0.5 * 4 * 0.0001 - swap)


if __name__ == "__main__":
    unittest.main()
