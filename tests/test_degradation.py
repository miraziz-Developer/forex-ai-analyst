import os
import unittest
from unittest.mock import patch

os.environ.setdefault("TURSO_DATABASE_URL", "libsql://test.invalid")
os.environ.setdefault("TURSO_AUTH_TOKEN", "test")

from forex_ai_analyst.trading.application import degradation


class AssessTests(unittest.TestCase):
    def test_normal_trend_following_losses_do_not_alarm(self):
        # Mostly small losses paid for by an occasional large winner: what the backtest looks like.
        r = ([-1.0] * 6 + [8.0]) * 4
        self.assertIsNone(degradation.assess(r)[0])

    def test_loss_run_beyond_the_backtest_is_critical(self):
        level, metrics = degradation.assess([2.0] + [-0.5] * 27)
        self.assertEqual(level, "CRITICAL")
        self.assertEqual(metrics["current_loss_run"], 27)

    def test_sixteen_losses_in_a_row_is_a_warning(self):
        self.assertEqual(degradation.assess([2.0] * 3 + [-0.5] * 16)[0], "WARNING")
        self.assertIsNone(degradation.assess([2.0] * 3 + [-0.5] * 15)[0])

    def test_deep_drawdown_is_flagged_even_with_scattered_wins(self):
        r = ([-1.0] * 4 + [0.5]) * 10         # -35R, never 16 losses in a row
        self.assertEqual(degradation.assess(r)[0], "WARNING")
        self.assertEqual(degradation.assess(r * 2)[0], "CRITICAL")   # -70R: beyond the backtest's worst

    def test_a_recovered_drawdown_no_longer_alarms(self):
        level, metrics = degradation.assess([-1.0] * 12 + [20.0])
        self.assertIsNone(level)
        self.assertEqual(metrics["worst_loss_run"], 12)


class TradeRTests(unittest.TestCase):
    def test_reconciled_net_pnl_is_preferred_over_the_journal_estimate(self):
        self.assertEqual(degradation.trade_r({"risk_usdt": 10, "realized_pnl_usdt": 20, "actual_net_pnl_usdt": 18}), 1.8)
        self.assertEqual(degradation.trade_r({"risk_usdt": 10, "realized_pnl_usdt": -10}), -1.0)

    def test_rows_without_risk_are_ignored(self):
        self.assertIsNone(degradation.trade_r({"risk_usdt": None, "realized_pnl_usdt": 5}))
        self.assertIsNone(degradation.trade_r({"risk_usdt": 0, "realized_pnl_usdt": 5}))


@patch("forex_ai_analyst.trading.application.degradation.execution_alerts")
@patch("forex_ai_analyst.trading.application.degradation.scalping_storage")
class CheckTests(unittest.TestCase):
    def test_breach_reports_one_deduplicated_incident(self, storage, alerts):
        storage.closed_strategy_trades.return_value = [{"risk_usdt": 5, "realized_pnl_usdt": -5}] * 27
        degradation.check()
        storage.closed_strategy_trades.assert_called_once_with("donchian_4h", 500)
        key, _ = alerts.report.call_args.args
        self.assertEqual(key, "degradation:donchian_4h")
        self.assertEqual(alerts.report.call_args.kwargs["severity"], "CRITICAL")
        alerts.resolve.assert_not_called()

    def test_healthy_results_resolve_the_incident(self, storage, alerts):
        storage.closed_strategy_trades.return_value = [{"risk_usdt": 5, "realized_pnl_usdt": 10}]
        degradation.check()
        alerts.report.assert_not_called()
        alerts.resolve.assert_called_once()


if __name__ == "__main__":
    unittest.main()
