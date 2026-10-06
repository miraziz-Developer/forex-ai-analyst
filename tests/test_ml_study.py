import unittest

import numpy as np

from forex_ai_analyst.forex import ml_study


class SpyModel:
    seen: list = []

    def fit(self, X, y):
        SpyModel.seen.append(X[:, 0].copy())
        return self

    def predict_proba(self, X):
        return np.column_stack([np.full(len(X), 0.4), np.full(len(X), 0.6)])


class WalkForwardTests(unittest.TestCase):
    def test_training_never_contains_labels_ending_in_or_after_the_test_year(self):
        meta, rows = [], []
        for year in range(2004, 2012):
            for k in range(200):
                day = f"{year}-{k % 12 + 1:02d}-{k % 27 + 1:02d}"
                exit_day = f"{year + (k % 12 == 11 and k % 27 > 20)}-{k % 12 + 1:02d}-28" if k % 12 != 11 else f"{year}-12-31"
                meta.append({"market": "EURUSD", "day": day, "exit_day": exit_day, "fwd": 0.001, "cost": 0.0})
                rows.append([float(year)])
        X, y = np.array(rows), np.array([1] * len(rows))
        SpyModel.seen = []
        trades = ml_study.walk_forward(X, y, meta, SpyModel)
        self.assertTrue(trades)
        first_test_year = ml_study.FIRST_TEST_YEAR
        for k, seen in enumerate(SpyModel.seen):
            self.assertLess(seen.max(), first_test_year + k)   # only rows from earlier years were used

    def test_only_confident_predictions_trade(self):
        self.assertGreater(ml_study.LONG_T, 0.5)
        self.assertLess(ml_study.SHORT_T, 0.5)


if __name__ == "__main__":
    unittest.main()
