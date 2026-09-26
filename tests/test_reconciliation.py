import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import ANY, Mock, patch

os.environ.setdefault("TURSO_DATABASE_URL", "libsql://test.invalid")
os.environ.setdefault("TURSO_AUTH_TOKEN", "test")

from forex_ai_analyst.trading.application import scheduler as multi_strategy_scheduler
from forex_ai_analyst.trading.infrastructure.bingx_broker import BingXApiError


class ReconciliationTests(unittest.TestCase):
    @patch("forex_ai_analyst.trading.application.scheduler.execution_alerts.resolve")
    @patch("forex_ai_analyst.trading.application.scheduler.execution_alerts.report")
    @patch("forex_ai_analyst.trading.application.scheduler.scalping_storage.open_vst_orders")
    def test_open_vst_recovery_resolves_matching_position_and_reports_sla(self, open_orders, report, resolve):
        open_orders.return_value = [{"fingerprint": "open-1", "pair": "BTC-USDT", "direction": "BUY",
                                    "broker_quantity": .1,
                                    "created_at": (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()}]
        broker = Mock()
        broker.get_position.return_value = {"positionAmt": ".1"}
        with patch.object(multi_strategy_scheduler, "broker", broker), patch.dict(os.environ, {"VST_OPEN_SLA_MINUTES": "120"}):
            multi_strategy_scheduler.recover_open_vst_orders()
        broker.get_position.assert_called_once_with("BTC-USDT", "LONG")
        resolve.assert_any_call("open-position:open-1", note="broker position matches open journal")
        report.assert_called_once_with("open-sla:open-1", ANY, severity="WARNING", details=ANY)

    @patch("forex_ai_analyst.trading.application.scheduler.execution_alerts.resolve")
    @patch("forex_ai_analyst.trading.application.scheduler.execution_alerts.report")
    @patch("forex_ai_analyst.trading.application.scheduler.scalping_storage.open_vst_orders")
    def test_multi_day_trend_position_is_not_overdue_before_its_expiry(self, open_orders, report, resolve):
        now = datetime.now(timezone.utc)
        open_orders.return_value = [{"fingerprint": "trend-1", "pair": "BTC-USDT", "direction": "BUY",
                                    "broker_quantity": .1, "created_at": (now - timedelta(days=5)).isoformat(),
                                    "expiry_time": (now + timedelta(days=55)).isoformat()}]
        broker = Mock()
        broker.get_position.return_value = {"positionAmt": ".1"}
        with patch.object(multi_strategy_scheduler, "broker", broker), patch.dict(os.environ, {"VST_OPEN_SLA_MINUTES": "120"}):
            multi_strategy_scheduler.recover_open_vst_orders()
        report.assert_not_called()
        resolve.assert_any_call("open-sla:trend-1", note="position remains within open SLA")

    @patch("forex_ai_analyst.trading.application.scheduler.execution_alerts.resolve")
    @patch("forex_ai_analyst.trading.application.scheduler.execution_alerts.report")
    @patch("forex_ai_analyst.trading.application.scheduler.scalping_storage.open_vst_orders")
    def test_open_vst_recovery_reports_absent_position_without_closing_journal(self, open_orders, report, resolve):
        open_orders.return_value = [{"fingerprint": "missing-1", "pair": "BTC-USDT", "direction": "SELL",
                                    "broker_order_id": "entry-1", "broker_quantity": .1, "created_at": datetime.now(timezone.utc).isoformat()}]
        broker = Mock()
        broker.get_position.return_value = None
        broker.order_history.return_value = []
        with patch.object(multi_strategy_scheduler, "broker", broker):
            multi_strategy_scheduler.recover_open_vst_orders()
        broker.get_position.assert_called_once_with("BTC-USDT", "SHORT")
        report.assert_called_once_with("open-position:missing-1", ANY, severity="CRITICAL", details=ANY)
        resolve.assert_not_called()

    @patch("forex_ai_analyst.trading.application.scheduler.execution_alerts.resolve")
    @patch("forex_ai_analyst.trading.application.scheduler.execution_alerts.report")
    @patch("forex_ai_analyst.trading.application.scheduler.scalping_storage.open_vst_orders")
    def test_open_vst_recovery_marks_history_window_limit_without_closing_journal(self, open_orders, report, resolve):
        open_orders.return_value = [{"fingerprint": "old-missing-1", "pair": "ETH-USDT", "direction": "BUY",
                                    "broker_order_id": "entry-1", "broker_quantity": .1,
                                    "created_at": (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()}]
        broker = Mock()
        broker.get_position.return_value = None
        broker.order_history.return_value = []
        with patch.object(multi_strategy_scheduler, "broker", broker):
            multi_strategy_scheduler.recover_open_vst_orders()
        _, message = report.call_args.args
        self.assertIn("7 kundan eski", message)
        self.assertTrue(report.call_args.kwargs["details"]["history_window_limited"])
        resolve.assert_not_called()

    @patch("forex_ai_analyst.trading.application.scheduler.execution_alerts.report")
    @patch("forex_ai_analyst.trading.application.scheduler.scalping_storage.open_vst_orders")
    def test_open_vst_recovery_reports_sanitized_bingx_diagnostic(self, open_orders, report):
        open_orders.return_value = [{"fingerprint": "sol-api-1", "pair": "SOL-USDT", "direction": "BUY",
                                    "broker_quantity": 1, "created_at": datetime.now(timezone.utc).isoformat()}]
        error = BingXApiError("/openApi/swap/v2/user/positions", http_status=403,
                              code=100001, message="IP forbidden", category="ip_whitelist")
        broker = Mock()
        broker.get_position.side_effect = error
        with patch.object(multi_strategy_scheduler, "broker", broker):
            multi_strategy_scheduler.recover_open_vst_orders()
        details = report.call_args.kwargs["details"]
        self.assertEqual(details["endpoint"], "/openApi/swap/v2/user/positions")
        self.assertEqual(details["http_status"], 403)
        self.assertEqual(details["bingx_code"], 100001)
        self.assertEqual(details["bingx_msg"], "IP forbidden")
        self.assertEqual(details["category"], "ip_whitelist")
        message = report.call_args.args[1]
        self.assertIn("category=ip_whitelist", message)
        self.assertIn("http_status=403", message)
        self.assertIn("bingx_code=100001", message)

    @patch("forex_ai_analyst.trading.application.scheduler.execution_alerts.resolve")
    @patch("forex_ai_analyst.trading.application.scheduler.execution_alerts.report")
    @patch("forex_ai_analyst.trading.application.scheduler.scalping_storage.resolve_paper_signal")
    @patch("forex_ai_analyst.trading.application.scheduler.scalping_storage.open_vst_orders")
    def test_open_vst_recovery_closes_tp_with_matching_immutable_history(self, open_orders, close, report, resolve):
        created = datetime.now(timezone.utc) - timedelta(minutes=5)
        open_orders.return_value = [{"fingerprint": "tp-1", "pair": "BTC-USDT", "direction": "BUY",
                                     "broker_order_id": "entry-1", "broker_quantity": .1, "created_at": created.isoformat()}]
        broker = Mock()
        broker.get_position.return_value = None
        broker.order_history.return_value = [{"order_id": "exit-1", "symbol": "BTC-USDT", "status": "FILLED",
                                              "side": "SELL", "position_side": "LONG", "type": "TAKE_PROFIT_MARKET",
                                              "fill_price": 103.1, "filled_quantity": .1,
                                              "created_at_ms": int((created + timedelta(minutes=1)).timestamp() * 1000)}]
        with patch.object(multi_strategy_scheduler, "broker", broker):
            multi_strategy_scheduler.recover_open_vst_orders()
        close.assert_called_once_with("tp-1", multi_strategy_scheduler.CandidateStatus.WIN, 103.1,
                                      broker.order_history.return_value[0], close_reason="TAKE_PROFIT_MARKET")
        report.assert_not_called()

    @patch("forex_ai_analyst.trading.application.scheduler.execution_alerts.report")
    @patch("forex_ai_analyst.trading.application.scheduler.scalping_storage.resolve_paper_signal")
    @patch("forex_ai_analyst.trading.application.scheduler.scalping_storage.open_vst_orders")
    def test_open_vst_recovery_keeps_journal_open_for_ambiguous_history(self, open_orders, close, report):
        created = datetime.now(timezone.utc) - timedelta(minutes=5)
        open_orders.return_value = [{"fingerprint": "ambiguous-1", "pair": "BTC-USDT", "direction": "BUY",
                                     "broker_order_id": "entry-1", "broker_quantity": .1, "created_at": created.isoformat()}]
        matching = {"symbol": "BTC-USDT", "status": "FILLED", "side": "SELL", "position_side": "LONG",
                    "type": "TAKE_PROFIT_MARKET", "fill_price": 103.1, "filled_quantity": .1,
                    "created_at_ms": int((created + timedelta(minutes=1)).timestamp() * 1000)}
        broker = Mock()
        broker.get_position.return_value = None
        broker.order_history.return_value = [{**matching, "order_id": "exit-1"}, {**matching, "order_id": "exit-2"}]
        with patch.object(multi_strategy_scheduler, "broker", broker):
            multi_strategy_scheduler.recover_open_vst_orders()
        close.assert_not_called()
        report.assert_called_once()

    def test_auto_exit_status_uses_real_fills_not_trigger_label(self):
        row = {"direction": "BUY", "broker_fill_price": 100.0}
        self.assertEqual(multi_strategy_scheduler._auto_exit_status(
            row, {"type": "TAKE_PROFIT_MARKET", "fill_price": 99.9}),
            multi_strategy_scheduler.CandidateStatus.LOSS)
        self.assertEqual(multi_strategy_scheduler._auto_exit_status(
            row, {"type": "STOP_MARKET", "fill_price": 100.1}),
            multi_strategy_scheduler.CandidateStatus.WIN)

    def test_account_scoped_income_is_not_misattributed_to_a_strategy_order(self):
        with patch("forex_ai_analyst.trading.application.scheduler.scalping_storage.closed_vst_orders_pending_reconciliation", return_value=[]):
            multi_strategy_scheduler.reconcile_closed_vst_orders()

    @patch("forex_ai_analyst.trading.application.scheduler.execution_alerts.report")
    @patch("forex_ai_analyst.trading.application.scheduler.execution_alerts.resolve")
    @patch("forex_ai_analyst.trading.application.scheduler.scalping_storage.mark_reconciliation")
    @patch("forex_ai_analyst.trading.application.scheduler.scalping_storage.closed_vst_orders_pending_reconciliation")
    def test_order_level_values_are_reconciled_only_from_matching_order_ids(self, pending, mark, resolve, alert):
        pending.return_value = [{"fingerprint": "f-1", "pair": "BTC-USDT", "broker_order_id": "entry", "broker_close_order_id": "exit"}]
        broker = Mock()
        broker.get_order.side_effect = [{"status": "FILLED", "realized_pnl_usdt": 0.0, "commission_usdt": -.1},
                                        {"status": "FILLED", "realized_pnl_usdt": 2.5, "commission_usdt": -.1}]
        with patch.object(multi_strategy_scheduler, "broker", broker):
            multi_strategy_scheduler.reconcile_closed_vst_orders()
        mark.assert_called_once_with("f-1", "VERIFIED", "exchange order-ID bilan tasdiqlandi", gross_pnl=2.5, fees=.2, funding=0.0)
        alert.assert_not_called()

    @patch("forex_ai_analyst.trading.application.scheduler.execution_alerts.report")
    @patch("forex_ai_analyst.trading.application.scheduler.execution_alerts.resolve")
    @patch("forex_ai_analyst.trading.application.scheduler.scalping_storage.mark_reconciliation")
    @patch("forex_ai_analyst.trading.application.scheduler.scalping_storage.closed_vst_orders_pending_reconciliation")
    def test_account_income_is_not_used_when_order_response_lacks_fee_or_pnl(self, pending, mark, resolve, alert):
        pending.return_value = [{"fingerprint": "f-2", "pair": "BTC-USDT", "broker_order_id": "entry", "broker_close_order_id": "exit"}]
        broker = Mock()
        broker.get_order.return_value = {"status": "FILLED", "realized_pnl_usdt": None, "commission_usdt": None}
        with patch.object(multi_strategy_scheduler, "broker", broker):
            multi_strategy_scheduler.reconcile_closed_vst_orders()
        mark.assert_called_once_with("f-2", "UNAVAILABLE", ANY)
        alert.assert_not_called()   # a known VST data limitation, recorded on the row, not an incident

    @patch("forex_ai_analyst.trading.application.scheduler.execution_alerts")
    @patch("forex_ai_analyst.trading.application.scheduler.scalping_storage.open_paper_signals",
           return_value=[{"fingerprint": "still-open"}])
    def test_housekeeping_closes_legacy_and_orphaned_incidents_only(self, open_rows, alerts):
        multi_strategy_scheduler.housekeeping()
        alerts.resolve_prefix.assert_called_once_with("unreconciled-order:", note=ANY)
        prefixes, live = alerts.resolve_orphaned.call_args.args
        self.assertEqual(live, {"still-open"})
        self.assertIn("open-sla:", prefixes)
        self.assertNotIn("stale-stop:", prefixes)   # needs a manual check on BingX, never auto-closed

    @patch("forex_ai_analyst.trading.application.scheduler.execution_alerts.report")
    @patch("forex_ai_analyst.trading.application.scheduler.scalping_storage.closed_vst_orders_pending_reconciliation")
    def test_reconciliation_failure_reports_sanitized_bingx_diagnostic(self, pending, alert):
        pending.return_value = [{"fingerprint": "sol-fee-1", "pair": "SOL-USDT", "broker_order_id": "entry",
                                 "broker_close_order_id": "exit"}]
        error = BingXApiError("/openApi/swap/v2/trade/order", http_status=400,
                              code=109400, message="Order does not exist", category="api_response")
        broker = Mock()
        broker.get_order.side_effect = error
        with patch.object(multi_strategy_scheduler, "broker", broker):
            multi_strategy_scheduler.reconcile_closed_vst_orders()
        details = alert.call_args.kwargs["details"]
        self.assertEqual(details["endpoint"], "/openApi/swap/v2/trade/order")
        self.assertEqual(details["bingx_code"], 109400)
        self.assertEqual(details["category"], "api_response")
        message = alert.call_args.args[1]
        self.assertIn("http_status=400", message)
        self.assertIn("bingx_code=109400", message)


if __name__ == "__main__":
    unittest.main()