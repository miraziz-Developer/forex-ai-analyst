import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch


for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TURSO_DATABASE_URL", "TURSO_AUTH_TOKEN"):
    os.environ.setdefault(key, "test")

from forex_ai_analyst.interfaces import http as app
from forex_ai_analyst.trading.infrastructure.bingx_broker import BingXApiError


class PositionGateTests(unittest.TestCase):
    NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)

    def test_existing_open_position_on_the_same_pair_is_blocked(self):
        self.assertIsNotNone(app.position_gate_rejection("BTC-USDT", [{"pair": "BTC-USDT"}], [], self.NOW))

    def test_other_pairs_open_do_not_block_until_the_concurrent_cap(self):
        opens = [{"pair": "ETH-USDT"}, {"pair": "SOL-USDT"}]
        self.assertIsNone(app.position_gate_rejection("BTC-USDT", opens, [], self.NOW))
        opens.append({"pair": "XRP-USDT"})
        self.assertIsNotNone(app.position_gate_rejection("BTC-USDT", opens, [], self.NOW))

    @patch.dict(os.environ, {"MAX_CONCURRENT_POSITIONS": "5"})
    def test_concurrent_cap_is_configurable(self):
        opens = [{"pair": p} for p in ("ETH-USDT", "SOL-USDT", "XRP-USDT", "BNB-USDT")]
        self.assertIsNone(app.position_gate_rejection("BTC-USDT", opens, [], self.NOW))

    def test_recently_closed_pair_is_in_cooldown_then_allowed(self):
        recent = {"pair": "BTC-USDT", "outcome_time": (self.NOW - timedelta(minutes=10)).isoformat()}
        old = {"pair": "BTC-USDT", "outcome_time": (self.NOW - timedelta(minutes=90)).isoformat()}
        self.assertIsNotNone(app.position_gate_rejection("BTC-USDT", [], [recent], self.NOW))
        self.assertIsNone(app.position_gate_rejection("BTC-USDT", [], [old], self.NOW))

class HealthTests(unittest.TestCase):
    def test_health_identifies_the_single_paper_only_service(self):
        with patch("forex_ai_analyst.interfaces.http.execution_alerts.status", return_value={"open_incidents": 0, "last_incident_at": None}), patch.dict(os.environ, {
            "MULTI_STRATEGY_PROVIDER": "bingx", "AUTO_EXECUTE_TRADES": "false",
            "BINGX_API_KEY": "", "BINGX_SECRET": "",
        }), patch("forex_ai_analyst.interfaces.http.runtime_controls.settings",
                  return_value={"kill_switch": False, "demo_execution": None}):
            response = app.app.test_client().get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {
            "status": "ok",
            "service": "multi-strategy-paper",
            "paper_only": True,
            "demo_only": True,
            "provider": "bingx",
            "auto_execute_trades": False,
            "auto_execute_trades_configured": False,
            "trade_readiness": {
                "ready": False,
                "auto_execute_trades_configured": False,
                "blockers": ["auto_execute_trades_disabled", "bingx_api_key_missing", "bingx_secret_missing"],
                "mode": "bingx_vst_demo_only",
            },
            "execution_alerts": {"open_incidents": 0, "last_incident_at": None},
            "vst_account": {"available": None, "last_checked_at": None},
        })

    def test_auto_execute_requires_both_bingx_vst_credentials(self):
        with patch("forex_ai_analyst.interfaces.http.execution_alerts.status", return_value={}):
            with patch.dict(os.environ, {"AUTO_EXECUTE_TRADES": "true", "BINGX_API_KEY": "", "BINGX_SECRET": ""}):
                response = app.app.test_client().get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["auto_execute_trades_configured"])
        self.assertFalse(response.get_json()["auto_execute_trades"])

    @patch("forex_ai_analyst.interfaces.http.runtime_controls.settings", return_value={"kill_switch": False, "demo_execution": None})
    def test_auto_execute_needs_no_ai_provider(self, controls):
        with patch("forex_ai_analyst.interfaces.http.execution_alerts.status", return_value={}), patch.dict(os.environ, {
            "AUTO_EXECUTE_TRADES": "true", "BINGX_API_KEY": "key", "BINGX_SECRET": "secret",
            "OPENAI_API_KEY": "", "AZURE_ANTHROPIC_API_KEY": "",
        }):
            response = app.app.test_client().get("/health")
        self.assertTrue(response.get_json()["auto_execute_trades"])

    @patch("forex_ai_analyst.interfaces.http.runtime_controls.settings",
           return_value={"kill_switch": True, "demo_execution": True})
    def test_trade_readiness_reports_runtime_blockers_without_secrets(self, controls):
        with patch.dict(os.environ, {
            "AUTO_EXECUTE_TRADES": "true", "KILL_SWITCH": "false",
            "BINGX_API_KEY": "private-key", "BINGX_SECRET": "private-secret",
        }, clear=False):
            readiness = app.trade_readiness()
        self.assertEqual(readiness["blockers"], ["runtime_kill_switch"])
        self.assertNotIn("private-key", str(readiness))
        self.assertNotIn("private-secret", str(readiness))

    @patch("forex_ai_analyst.interfaces.http.runtime_controls.settings", return_value={"kill_switch": True, "demo_execution": True})
    def test_runtime_kill_switch_prevents_vst_execution(self, controls):
        with patch.dict(os.environ, {"AUTO_EXECUTE_TRADES": "true", "BINGX_API_KEY": "key", "BINGX_SECRET": "secret"}):
            self.assertFalse(app.demo_execution_enabled())

    @patch("forex_ai_analyst.interfaces.http.scalping_storage.risk_state", return_value=(2, -1.25))
    @patch("forex_ai_analyst.interfaces.http.scalping_storage.open_paper_positions", return_value=1)
    @patch("forex_ai_analyst.trading.infrastructure.bingx_broker.get_vst_usdt_balance", return_value={"equity_usdt": 100.0, "available_usdt": 80.0,
                                                          "unrealized_pnl_usdt": -0.5})
    def test_vst_account_context_exposes_only_normalized_sizing_values(self, balance, positions, risk_state):
        with patch.dict(os.environ, {"BINGX_API_KEY": "key", "BINGX_SECRET": "secret"}):
            self.assertEqual(app._vst_account_context(), {
                "available": True, "open_strategy_positions": 1, "daily_strategy_pnl_usdt": -1.25,
                "equity_usdt": 100.0, "available_usdt": 80.0, "unrealized_pnl_usdt": -0.5,
            })

    @patch("forex_ai_analyst.interfaces.http.scalping_storage.risk_state", return_value=(0, 0.0))
    @patch("forex_ai_analyst.interfaces.http.scalping_storage.open_paper_positions", return_value=0)
    @patch("forex_ai_analyst.trading.infrastructure.bingx_broker.get_vst_usdt_balance")
    def test_vst_account_context_exposes_safe_balance_schema_on_schema_failure(self, balance, positions, risk_state):
        error = BingXApiError("/openApi/swap/v2/user/balance", code=0,
                              message="USDT balance row missing", category="account_schema")
        error.diagnostic["balance_schema"] = {"row_count": 1, "row_fields": ["coin"], "asset_labels": ["VST"]}
        balance.side_effect = error
        with patch.dict(os.environ, {"BINGX_API_KEY": "key", "BINGX_SECRET": "secret"}):
            context = app._vst_account_context()
        self.assertFalse(context["available"])
        self.assertEqual(context["balance_schema"], error.diagnostic["balance_schema"])

    @patch("forex_ai_analyst.interfaces.http.execution_alerts.report")
    @patch("forex_ai_analyst.interfaces.http.scan_pair")
    @patch("forex_ai_analyst.interfaces.http._vst_account_context", return_value={"available": False, "category": "credentials",
                                                       "http_status": 401, "bingx_code": 100001,
                                                       "bingx_msg": "API key invalid"})
    def test_configured_scan_stops_before_pair_work_when_account_is_unavailable(self, context, scan_pair, report):
        app.scan_configured_pairs(unittest.mock.Mock())

        scan_pair.assert_not_called()
        report.assert_called_once_with("vst-account-context-unavailable",
                                       "BingX VST account holati olinmadi; scan va yangi orderlar fail-closed to‘xtatildi.",
                                       details={"category": "credentials", "http_status": 401,
                                                "bingx_code": 100001, "bingx_msg": "API key invalid"},
                                       remind_after_minutes=None)

    @patch("forex_ai_analyst.interfaces.http.execution_alerts.resolve")
    @patch("forex_ai_analyst.interfaces.http._vst_account_context", return_value={"available": True, "equity_usdt": 100, "available_usdt": 80})
    @patch("forex_ai_analyst.interfaces.http.scan_pair")
    def test_configured_scan_reuses_one_successful_account_snapshot_for_all_pairs(self, scan_pair, context, resolve):
        provider = unittest.mock.Mock()
        app.scan_configured_pairs(provider)

        self.assertEqual(context.call_count, 1)
        self.assertEqual(scan_pair.call_count, len(app.configured_pairs()))
        for call in scan_pair.call_args_list:
            self.assertIs(call.kwargs["account_state"], context.return_value)
        resolve.assert_called_once_with("vst-account-context-unavailable",
                                        note="BingX VST account holati tiklandi; scan qayta yoqildi.", notify=True)

    @patch("forex_ai_analyst.interfaces.http.execution_alerts.resolve")
    def test_retired_static_risk_alerts_are_closed_for_each_configured_pair(self, resolve):
        app.resolve_retired_static_risk_alerts()

        self.assertEqual(resolve.call_count, len(app.configured_pairs()) * 3)

    def test_signals_api_requires_dashboard_token_when_configured(self):
        with patch.object(app, "DASHBOARD_TOKEN", "secret"):
            response = app.app.test_client().get("/api/signals")
        self.assertEqual(response.status_code, 401)

    @patch("forex_ai_analyst.interfaces.http.handle_update")
    def test_telegram_webhook_dispatches_authorized_update(self, handle_update):
        with patch.dict(os.environ, {"TELEGRAM_WEBHOOK_SECRET": "secret"}):
            response = app.app.test_client().post(
                "/telegram/webhook", json={"message": {"text": "/start"}},
                headers={"X-Telegram-Bot-Api-Secret-Token": "secret"},
            )
        self.assertEqual(response.status_code, 200)
        handle_update.assert_called_once_with({"message": {"text": "/start"}})

    @patch("forex_ai_analyst.interfaces.http.handle_update")
    def test_telegram_webhook_rejects_wrong_secret(self, handle_update):
        with patch.dict(os.environ, {"TELEGRAM_WEBHOOK_SECRET": "secret"}):
            response = app.app.test_client().post("/telegram/webhook", json={}, headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"})
        self.assertEqual(response.status_code, 401)
        handle_update.assert_not_called()


if __name__ == "__main__":
    unittest.main()