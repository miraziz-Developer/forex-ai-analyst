import unittest

from forex_ai_analyst.lab import rebound_improve as ri
from forex_ai_analyst.lab.engine2_study import capitulation_rebound

H = ri.HOUR


def bars_with_crash():
    out, price = [], 100.0
    for i in range(400):
        if 300 <= i < 312:
            price *= 0.985                                    # ~17% fall over twelve hours
        o = price
        c = price * (1.01 if i == 312 else 1.0)               # hour 312 closes green
        out.append({"datetime": i * H, "open": o, "high": max(o, c) * 1.002, "low": min(o, c) * 0.998,
                    "close": c, "volume": 10.0 if 290 <= i <= 312 else 1.0})
        price = c
    return out


class ReboundImproveTest(unittest.TestCase):
    def test_base_matches_the_engine2_rule(self):
        bars = bars_with_crash()
        mine, theirs = ri.signals(bars, ri.Variant("base"), [], {}), capitulation_rebound(bars)
        self.assertTrue(any(mine.long_entry))
        for i in range(192, len(bars)):
            self.assertEqual(mine.long_entry[i], theirs.long_entry[i])
            if mine.long_entry[i]:
                self.assertAlmostEqual(mine.stop_distance[i], theirs.stop_distance[i])
                self.assertAlmostEqual(mine.target_distance[i], theirs.target_distance[i])

    def test_filters_only_remove_entries(self):
        bars = bars_with_crash()
        base = ri.signals(bars, ri.Variant("base"), [], {}).long_entry
        positive_funding = [(0, 0.0001)]
        f = ri.signals(bars, ri.Variant("F", funding_le_zero=True), positive_funding, {}).long_entry
        self.assertFalse(any(f))
        btc = {b["datetime"]: 100.0 for b in bars}                 # BTC flat: no market-wide crash
        m = ri.signals(bars, ri.Variant("M", market_wide=True), [], btc).long_entry
        self.assertFalse(any(m))
        v = ri.signals(bars, ri.Variant("V", volume_climax=True), [], {}).long_entry
        self.assertEqual(v, base)                                   # volume 10x the prior week: climax

    def test_combine_carries_every_change(self):
        combo = ri.combine([ri.Variant("F", funding_le_zero=True), ri.Variant("T38", target_fraction=0.382)])
        self.assertEqual(combo.name, "F+T38")
        self.assertTrue(combo.funding_le_zero)
        self.assertEqual(combo.target_fraction, 0.382)
        self.assertEqual(combo.stop_atr, 0.5)


if __name__ == "__main__":
    unittest.main()
