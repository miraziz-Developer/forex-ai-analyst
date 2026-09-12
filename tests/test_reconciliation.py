import os
import unittest
from unittest.mock import patch

os.environ.setdefault("TURSO_DATABASE_URL", "libsql://test.invalid")
os.environ.setdefault("TURSO_AUTH_TOKEN", "test")

import multi_strategy_scheduler


class ReconciliationTests(unittest.TestCase):
    @patch("scalping_storage.reconcile_broker_pnl")
    @patch("broker.income_history", return_value=[
        {"incomeType": "REALIZED_PNL", "income": "2.5"},
        {"incomeType": "COMMISSION", "income": "-0.1"},
        {"incomeType": "FUNDING_FEE", "income": "-0.05"},
    ])
    @patch("scalping_storage.closed_paper_signals")
    def test_numeric_bingx_income_is_mapped_to_actual_net_components(self, closed, income, reconcile):
        closed.return_value = [{"fingerprint": "x", "pair": "BTC-USDT", "broker_order_id": "1",
                                "created_at": "2026-09-12T00:00:00+00:00", "reconciled_at": None}]
        multi_strategy_scheduler.reconcile_closed_vst_orders()
        reconcile.assert_called_once_with("x", 2.5, .1, -.05)


if __name__ == "__main__":
    unittest.main()