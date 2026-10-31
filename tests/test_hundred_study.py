import math
import unittest

from forex_ai_analyst.lab import hundred_study as h


def bars(n=800):
    out, p = [], 100.0
    for i in range(n):
        p *= 1 + 0.01 * math.sin(i / 15) + 0.002
        out.append({"datetime": 1_600_000_000_000 + i * h.DAY, "open": p * 0.998, "high": p * 1.01,
                    "low": p * 0.99, "close": p, "volume": 1.0})
    return out


class HundredStudyTests(unittest.TestCase):
    def test_every_rule_produces_valid_signals_and_trades(self):
        b = bars()
        for name, rule in h.RULES.items():
            sig = rule(b)
            self.assertEqual(len(sig.long_entry), len(b), name)
            self.assertEqual(len(sig.stop_distance), len(b), name)
        rows = h.run_rule("sma_20_50", "FXM", {"X": (b, [])})
        self.assertTrue(rows)
        self.assertTrue(all("r" in r for r in rows))

    def test_index_group_is_long_only(self):
        rows = h.run_rule("sma_20_50", "IDX", {"X": (bars(), [])})
        self.assertTrue(all(r["side"] > 0 for r in rows))

    def test_split_and_gates(self):
        rows = [{"market": "A", "t": 0, "r": 1.0, "side": 1}, {"market": "A", "t": 10**13, "r": -1.0, "side": 1}]
        self.assertEqual(len(h.split_rows(rows, "CRYPTO", False)), 1)
        self.assertFalse(h.qualifies_dev(h.evaluate(rows)))


if __name__ == "__main__":
    unittest.main()
