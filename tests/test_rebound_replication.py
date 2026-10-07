import unittest

from forex_ai_analyst.lab import rebound_replication as r


def trade(t, x, market="ZEC-USDT", day=[0]):
    day[0] += 1                                      # spread exits over many days for the bootstrap
    return {"entry_time": t, "exit_time": abs(t) + day[0] * 86_400_000, "r": x, "net": x, "market": market}


class ReboundReplicationTest(unittest.TestCase):
    def test_gate_requires_both_halves(self):
        trades = [trade(r.SPLIT - 1, 1.0) for _ in range(150)] + [trade(r.SPLIT + 1, -0.2) for _ in range(150)]
        report = r.gate(trades, {m: 1.0 for m in "abcdefghij"})
        self.assertFalse(report["passes"])
        self.assertTrue(any("halves" in x for x in report["reasons"]))

    def test_gate_requires_six_markets(self):
        trades = [trade(r.SPLIT + k % 2 * 2 - 1, 0.5) for k in range(400)] + [trade(r.SPLIT + 1, -0.4) for _ in range(100)]
        report = r.gate(trades, {"a": 1.0, "b": -1.0})
        self.assertIn("fewer than 6 of 10 markets positive", report["reasons"])


if __name__ == "__main__":
    unittest.main()
