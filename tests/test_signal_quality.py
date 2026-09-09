import unittest

from signal_quality import (
    confidence_meets_minimum,
    optimize_policy_holdout,
    performance_metrics,
    reward_risk_ratio,
    rows_for_policy,
)


class SignalQualityTests(unittest.TestCase):
    def test_default_floor_rejects_low_or_missing_confidence(self):
        self.assertFalse(confidence_meets_minimum("PAST", "ORTA"))
        self.assertFalse(confidence_meets_minimum(None, "ORTA"))
        self.assertTrue(confidence_meets_minimum("ORTA", "ORTA"))
        self.assertTrue(confidence_meets_minimum("YUQORI", "ORTA"))

    def test_invalid_minimum_fails_fast(self):
        with self.assertRaises(ValueError):
            confidence_meets_minimum("YUQORI", "HIGH")

    def test_reward_risk_uses_executable_prices(self):
        self.assertAlmostEqual(reward_risk_ratio(100.0, 106.0, 98.0), 3.0)
        self.assertEqual(reward_risk_ratio(100.0, 106.0, 100.0), 0.0)

    def test_performance_metrics_exposes_payoff_and_drawdown(self):
        metrics = performance_metrics([
            {"checkpoint_ms": 1, "outcome": "WIN", "actual_rr": 2.0},
            {"checkpoint_ms": 2, "outcome": "LOSS", "actual_rr": 2.5},
            {"checkpoint_ms": 3, "outcome": "LOSS", "actual_rr": 1.5},
            {"checkpoint_ms": 4, "outcome": "EXPIRED", "actual_rr": 2.0},
        ])
        self.assertEqual(metrics["trades"], 3)
        self.assertEqual(metrics["win_rate_pct"], 33.3)
        self.assertEqual(metrics["net_r"], 0.0)
        self.assertEqual(metrics["profit_factor"], 1.0)
        self.assertEqual(metrics["expectancy_r"], 0.0)
        self.assertEqual(metrics["max_drawdown_r"], 2.0)

    def test_rows_for_policy_applies_both_thresholds(self):
        rows = [
            {"outcome": "WIN", "confidence": "YUQORI", "actual_rr": 2.0},
            {"outcome": "LOSS", "confidence": "ORTA", "actual_rr": 2.5},
            {"outcome": "WIN", "confidence": "YUQORI", "actual_rr": 1.4},
        ]
        selected = rows_for_policy(rows, "YUQORI", 1.5)
        self.assertEqual(selected, [rows[0]])

    def test_optimizer_selects_on_train_and_reports_unseen_holdout(self):
        rows = []
        # Older train: ORTA adds four losses; YUQORI keeps four 2R wins.
        for i in range(4):
            rows.append({"checkpoint_ms": i * 2, "outcome": "WIN",
                         "confidence": "YUQORI", "actual_rr": 2.0})
            rows.append({"checkpoint_ms": i * 2 + 1, "outcome": "LOSS",
                         "confidence": "ORTA", "actual_rr": 2.0})
        # Newer holdout is evaluated only after selection.
        rows.extend([
            {"checkpoint_ms": 8, "outcome": "WIN", "confidence": "YUQORI", "actual_rr": 2.0},
            {"checkpoint_ms": 9, "outcome": "LOSS", "confidence": "ORTA", "actual_rr": 2.0},
        ])
        result = optimize_policy_holdout(
            rows, holdout_ratio=0.2, min_train_trades=4, rr_grid=(1.5, 2.0))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["selected_policy"]["minimum_confidence"], "YUQORI")
        self.assertEqual(result["holdout_metrics"]["trades"], 1)
        self.assertEqual(result["holdout_metrics"]["net_r"], 2.0)

    def test_optimizer_refuses_tiny_samples(self):
        result = optimize_policy_holdout([
            {"checkpoint_ms": 1, "outcome": "WIN", "confidence": "YUQORI", "actual_rr": 2.0},
            {"checkpoint_ms": 2, "outcome": "LOSS", "confidence": "ORTA", "actual_rr": 2.0},
        ], min_train_trades=20)
        self.assertEqual(result["status"], "insufficient_data")


if __name__ == "__main__":
    unittest.main()