import unittest

from forex_ai_analyst.trading.application.learning import MIN_SAMPLE_SIZE, summarize


class LearningTests(unittest.TestCase):
    def test_insufficient_samples_are_not_promoted_as_evidence(self):
        rows = [{"pair": "BTC-USDT", "regime": "TRENDING_UP", "direction": "BUY", "status": "WIN",
                 "realized_pnl_usdt": 1}] * (MIN_SAMPLE_SIZE - 1)
        summary = summarize(rows, "BTC-USDT")
        self.assertEqual(summary["eligible_patterns"], [])
        self.assertEqual(summary["insufficient_sample_patterns"], 1)

    def test_eligible_sample_is_only_soft_guidance(self):
        rows = [{"pair": "BTC-USDT", "regime": "TRENDING_UP", "direction": "BUY", "status": "WIN",
                 "realized_pnl_usdt": 1}] * MIN_SAMPLE_SIZE
        summary = summarize(rows, "BTC-USDT")
        self.assertEqual(summary["eligible_patterns"][0]["guidance"], "softly supportive")
        self.assertIn("cannot change", summary["policy"])


if __name__ == "__main__":
    unittest.main()