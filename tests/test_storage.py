import os
import unittest
from unittest.mock import patch

os.environ.setdefault("TURSO_DATABASE_URL", "libsql://test.invalid")
os.environ.setdefault("TURSO_AUTH_TOKEN", "test")

import storage


class StorageMonitoringTests(unittest.TestCase):
    def test_estimated_pnl_for_executed_buy(self):
        pnl = storage.estimate_signal_pnl({
            "direction": "BUY", "entry_price": 100.0, "outcome_price": 110.0,
            "broker_qty": 2.0, "funding_rate_pct": 0.01,
            "signal_time": "2026-01-01T00:00:00+00:00",
            "outcome_time": "2026-01-01T04:00:00+00:00",
        })
        self.assertAlmostEqual(pnl, 19.79, places=2)

    def test_analysis_only_signal_has_no_real_pnl(self):
        self.assertIsNone(storage.estimate_signal_pnl({
            "direction": "SELL", "entry_price": 100.0,
            "outcome_price": 90.0, "broker_qty": None,
        }))

    @patch("storage._execute")
    def test_get_signals_uses_allowlisted_filters(self, execute):
        execute.side_effect = [
            {"cols": [{"name": "n"}], "rows": [[{"type": "integer", "value": "0"}]]},
            {"cols": [], "rows": []},
        ]
        result = storage.get_signals(status="WIN", pair="btc-usdt", direction="buy", limit=999)
        self.assertEqual(result["limit"], 250)
        count_sql, count_args = execute.call_args_list[0].args
        self.assertIn("outcome = ?", count_sql)
        self.assertEqual(count_args, ["WIN", "BTC-USDT", "BUY"])

    def test_get_signals_rejects_unknown_status(self):
        with self.assertRaises(ValueError):
            storage.get_signals(status="DROP TABLE signals")


if __name__ == "__main__":
    unittest.main()