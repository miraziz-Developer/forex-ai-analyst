import os
import sys
import unittest
from unittest.mock import ANY, Mock, patch

os.environ.setdefault("TURSO_DATABASE_URL", "libsql://test.invalid")
os.environ.setdefault("TURSO_AUTH_TOKEN", "test")

import multi_strategy_scheduler


class ReconciliationTests(unittest.TestCase):
    def test_account_scoped_income_is_not_misattributed_to_a_strategy_order(self):
        with patch("multi_strategy_scheduler.scalping_storage.closed_vst_orders_pending_reconciliation", return_value=[]):
            multi_strategy_scheduler.reconcile_closed_vst_orders()

    @patch("multi_strategy_scheduler.execution_alerts.report")
    @patch("multi_strategy_scheduler.scalping_storage.mark_reconciliation")
    @patch("multi_strategy_scheduler.scalping_storage.closed_vst_orders_pending_reconciliation")
    def test_order_level_values_are_reconciled_only_from_matching_order_ids(self, pending, mark, alert):
        pending.return_value = [{"fingerprint": "f-1", "pair": "BTC-USDT", "broker_order_id": "entry", "broker_close_order_id": "exit"}]
        broker = Mock()
        broker.get_order.side_effect = [{"status": "FILLED", "realized_pnl_usdt": 0.0, "commission_usdt": -.1},
                                        {"status": "FILLED", "realized_pnl_usdt": 2.5, "commission_usdt": -.1}]
        with patch.dict(sys.modules, {"broker": broker}):
            multi_strategy_scheduler.reconcile_closed_vst_orders()
        mark.assert_called_once_with("f-1", "VERIFIED", "exchange order-ID bilan tasdiqlandi", gross_pnl=2.5, fees=.2, funding=0.0)
        alert.assert_not_called()

    @patch("multi_strategy_scheduler.execution_alerts.report")
    @patch("multi_strategy_scheduler.scalping_storage.mark_reconciliation")
    @patch("multi_strategy_scheduler.scalping_storage.closed_vst_orders_pending_reconciliation")
    def test_account_income_is_not_used_when_order_response_lacks_fee_or_pnl(self, pending, mark, alert):
        pending.return_value = [{"fingerprint": "f-2", "pair": "BTC-USDT", "broker_order_id": "entry", "broker_close_order_id": "exit"}]
        broker = Mock()
        broker.get_order.return_value = {"status": "FILLED", "realized_pnl_usdt": None, "commission_usdt": None}
        with patch.dict(sys.modules, {"broker": broker}):
            multi_strategy_scheduler.reconcile_closed_vst_orders()
        mark.assert_called_once_with("f-2", "UNAVAILABLE", ANY)
        alert.assert_called_once()


if __name__ == "__main__":
    unittest.main()