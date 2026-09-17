import os
import unittest
from unittest.mock import patch

os.environ.setdefault("TURSO_DATABASE_URL", "libsql://test.invalid")
os.environ.setdefault("TURSO_AUTH_TOKEN", "test")

from forex_ai_analyst.operations import runtime_controls as runtime_controls


class BalanceRelativeRiskTests(unittest.TestCase):
    def test_balance_risk_limit_uses_equity_and_remaining_daily_loss_budget(self):
        controls = {"risk_per_trade_pct": 2.0, "max_daily_loss_pct": 5.0,
                    "max_margin_utilization_pct": 25.0}
        self.assertEqual(runtime_controls.balance_risk_limit(
            {"equity_usdt": 100.0, "daily_strategy_pnl_usdt": -1.0}, controls), 2.0)
        self.assertEqual(runtime_controls.balance_risk_limit(
            {"equity_usdt": 100.0, "daily_strategy_pnl_usdt": -4.0}, controls), 1.0)

    def test_balance_risk_limit_fails_closed_without_valid_equity(self):
        self.assertIsNone(runtime_controls.balance_risk_limit(
            {"equity_usdt": 0, "daily_strategy_pnl_usdt": 0}, runtime_controls.DEFAULTS))

    @patch("forex_ai_analyst.operations.runtime_controls.settings", return_value={"kill_switch": False, "blocked_pairs": []})
    def test_trade_permission_has_no_fixed_risk_leverage_or_cooldown_cap(self, settings):
        self.assertIsNone(runtime_controls.trade_permitted("BTC-USDT", 999, 125, 0))


if __name__ == "__main__":
    unittest.main()