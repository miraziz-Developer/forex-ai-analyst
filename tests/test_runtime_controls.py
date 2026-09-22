import json
import os
import unittest
from datetime import datetime, timedelta, timezone
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


class ConfirmDoubleDeliveryTests(unittest.TestCase):
    @patch("forex_ai_analyst.operations.runtime_controls.storage._rows_as_dicts")
    @patch("forex_ai_analyst.operations.runtime_controls.storage._execute")
    def test_confirm_rejects_when_another_delivery_already_consumed_the_pending_row(self, execute, rows_as_dicts):
        future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        rows_as_dicts.return_value = [{"updates_json": json.dumps({"kill_switch": True}), "expires_at": future}]
        execute.side_effect = [
            {},  # SELECT pending row (parsed via the mocked _rows_as_dicts above)
            {"affected_row_count": 0},  # DELETE: a concurrent retried delivery already removed it
            {"affected_row_count": 1},  # INSERT runtime_control_audit (CONFIRMATION_REJECTED)
        ]
        self.assertIsNone(runtime_controls.confirm("42", "ABC123"))

    @patch("forex_ai_analyst.operations.runtime_controls.settings", return_value={"blocked_pairs": []})
    @patch("forex_ai_analyst.operations.runtime_controls.storage._rows_as_dicts")
    @patch("forex_ai_analyst.operations.runtime_controls.storage._execute")
    def test_confirm_applies_when_this_call_consumes_the_pending_row(self, execute, rows_as_dicts, settings):
        future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        rows_as_dicts.return_value = [{"updates_json": json.dumps({"kill_switch": True}), "expires_at": future}]
        execute.side_effect = [
            {},  # SELECT pending row
            {"affected_row_count": 1},  # DELETE: this call wins the row
            {"affected_row_count": 1},  # INSERT runtime_controls (kill_switch)
            {"affected_row_count": 1},  # INSERT runtime_control_audit
        ]
        self.assertEqual(runtime_controls.confirm("42", "abc123"), {"blocked_pairs": []})


class BlockedPairsDeltaTests(unittest.TestCase):
    @patch("forex_ai_analyst.operations.runtime_controls.storage._execute", return_value={"affected_row_count": 1})
    @patch("forex_ai_analyst.operations.runtime_controls.settings")
    def test_apply_merges_an_add_delta_against_the_live_blocked_pairs_value(self, settings, execute):
        settings.return_value = {"blocked_pairs": ["ETH-USDT"]}
        runtime_controls.apply("42", {"blocked_pairs": {"add": ["BTC-USDT"]}})
        insert_calls = [c for c in execute.call_args_list if c.args[0].strip().startswith("INSERT INTO runtime_controls")]
        self.assertEqual(len(insert_calls), 1)
        self.assertEqual(json.loads(insert_calls[0].args[1][1]), ["BTC-USDT", "ETH-USDT"])

    @patch("forex_ai_analyst.operations.runtime_controls.storage._execute", return_value={"affected_row_count": 1})
    @patch("forex_ai_analyst.operations.runtime_controls.settings")
    def test_apply_merges_a_remove_delta_against_the_live_blocked_pairs_value(self, settings, execute):
        settings.return_value = {"blocked_pairs": ["BTC-USDT", "ETH-USDT"]}
        runtime_controls.apply("42", {"blocked_pairs": {"remove": ["ETH-USDT"]}})
        insert_calls = [c for c in execute.call_args_list if c.args[0].strip().startswith("INSERT INTO runtime_controls")]
        self.assertEqual(json.loads(insert_calls[0].args[1][1]), ["BTC-USDT"])

    @patch("forex_ai_analyst.operations.runtime_controls.storage._execute", return_value={"affected_row_count": 1})
    @patch("forex_ai_analyst.operations.runtime_controls.settings")
    def test_apply_still_replaces_blocked_pairs_when_given_a_plain_list(self, settings, execute):
        settings.return_value = {"blocked_pairs": ["SOL-USDT"]}
        runtime_controls.apply("42", {"blocked_pairs": ["XRP-USDT"]})
        insert_calls = [c for c in execute.call_args_list if c.args[0].strip().startswith("INSERT INTO runtime_controls")]
        self.assertEqual(json.loads(insert_calls[0].args[1][1]), ["XRP-USDT"])


if __name__ == "__main__":
    unittest.main()