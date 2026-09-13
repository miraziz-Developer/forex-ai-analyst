import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch


for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TURSO_DATABASE_URL", "TURSO_AUTH_TOKEN"):
    os.environ.setdefault(key, "test")

import app
from ai_trader import AITradeDecision
from scalping_core import CandidateSignal, Direction, MarketRegime


class HealthTests(unittest.TestCase):
    @staticmethod
    def _candidate():
        return CandidateSignal("test", "BTC-USDT", Direction.BUY, MarketRegime.TRENDING_UP,
                               100, 98, 103, datetime.now(timezone.utc) + timedelta(hours=1),
                               "5m", "15m", 1, 80, (), "test")

    def test_health_identifies_the_single_paper_only_service(self):
        with patch("app.execution_alerts.status", return_value={"open_incidents": 0, "last_incident_at": None}), patch.dict(os.environ, {"MULTI_STRATEGY_PROVIDER": "bingx"}):
            response = app.app.test_client().get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {
            "status": "ok",
            "service": "multi-strategy-paper",
            "paper_only": True,
            "demo_only": True,
            "provider": "bingx",
            "auto_execute_trades": False,
            "auto_execute_trades_configured": app.AUTO_EXECUTE_TRADES_CONFIGURED,
            "execution_alerts": {"open_incidents": 0, "last_incident_at": None},
        })

    def test_auto_execute_requires_both_bingx_vst_credentials(self):
        with patch("app.execution_alerts.status", return_value={}), patch.object(app, "AUTO_EXECUTE_TRADES_CONFIGURED", True):
            with patch.dict(os.environ, {"AUTO_EXECUTE_TRADES": "true", "BINGX_API_KEY": "", "BINGX_SECRET": ""}):
                response = app.app.test_client().get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["auto_execute_trades_configured"])
        self.assertFalse(response.get_json()["auto_execute_trades"])

    @patch("app.runtime_controls.settings", return_value={"kill_switch": False, "demo_execution": None})
    def test_auto_execute_uses_bingx_vst_only_when_fully_configured(self, controls):
        with patch("app.execution_alerts.status", return_value={}), patch.dict(os.environ, {"AUTO_EXECUTE_TRADES": "true", "BINGX_API_KEY": "key", "BINGX_SECRET": "secret"}):
            response = app.app.test_client().get("/health")
        self.assertTrue(response.get_json()["auto_execute_trades"])

    @patch("app.demo_execution_enabled", return_value=False)
    def test_bingx_vst_order_is_not_attempted_when_execution_is_disabled(self, enabled):
        decision = AITradeDecision("PROPOSE_TRADE", "test", "test", 80, risk_usdt=.75, leverage=3,
                                   direction=Direction.BUY, entry_price=100, stop_price=98, target_price=103)
        self.assertIsNone(app.execute_bingx_vst_order(self._candidate(), decision))

    @patch("app.runtime_controls.settings", return_value={"kill_switch": False, "demo_execution": None})
    @patch("broker.place_market_order", return_value={"order_id": "vst-1", "fill_price": 100.25})
    @patch("broker.round_quantity", return_value=.3)
    @patch("app.demo_execution_enabled", return_value=True)
    def test_bingx_vst_order_uses_signal_levels_and_rounded_quantity(self, enabled, rounded, place_order, controls):
        candidate = self._candidate()
        decision = AITradeDecision("PROPOSE_TRADE", "test", "test", 80, risk_usdt=.75, leverage=4,
                                   direction=Direction.BUY, entry_price=100, stop_price=98, target_price=103)
        result = app.execute_bingx_vst_order(candidate, decision)
        place_order.assert_called_once_with("BTC-USDT", "BUY", .3, 103, 98, leverage=4)
        self.assertEqual(result, {"order_id": "vst-1", "fill_price": 100.25, "quantity": .3})

    @patch("app.runtime_controls.settings", return_value={"kill_switch": True, "demo_execution": True})
    def test_runtime_kill_switch_prevents_vst_execution(self, controls):
        with patch.dict(os.environ, {"AUTO_EXECUTE_TRADES": "true", "BINGX_API_KEY": "key", "BINGX_SECRET": "secret"}):
            self.assertFalse(app.demo_execution_enabled())

    def test_ai_execution_values_are_constrained_to_live_vst_equity_and_margin(self):
        decision = AITradeDecision("PROPOSE_TRADE", "test", "test", 80, risk_usdt=80, leverage=25,
                                   cooldown_minutes=1, direction=Direction.BUY, entry_price=100, stop_price=98,
                                   target_price=103)
        constrained = app._constrain_ai_decision(decision, {
            "risk_per_trade_pct": 1.0, "max_daily_loss_pct": 5.0, "max_margin_utilization_pct": 25.0,
        }, {"equity_usdt": 100.0, "available_usdt": 80.0, "daily_strategy_pnl_usdt": 0.0})
        self.assertEqual((constrained.risk_usdt, constrained.leverage, constrained.cooldown_minutes), (1.0, 25, 1))

    def test_ai_risk_is_zero_when_daily_balance_loss_budget_is_exhausted(self):
        decision = AITradeDecision("PROPOSE_TRADE", "test", "test", 80, risk_usdt=1, leverage=5,
                                    direction=Direction.BUY, entry_price=100, stop_price=98, target_price=103)
        constrained = app._constrain_ai_decision(decision, {
            "risk_per_trade_pct": 1.0, "max_daily_loss_pct": 5.0, "max_margin_utilization_pct": 25.0,
        }, {"equity_usdt": 100.0, "available_usdt": 80.0, "daily_strategy_pnl_usdt": -5.0})
        self.assertEqual(constrained.risk_usdt, 0.0)

    @patch("app.scalping_storage.mark_accepted")
    @patch("app.execute_bingx_vst_order")
    @patch("app.decide")
    @patch("app.runtime_controls.settings", return_value={
        "risk_per_trade_pct": 1.0, "max_daily_loss_pct": 5.0, "max_margin_utilization_pct": 25.0,
    })
    @patch("app.scalping_storage.recent_ai_reviews", return_value=[])
    @patch("app.knowledge.search", return_value=[])
    @patch("app._vst_account_context", return_value={"available": False})
    @patch("app.scalping_storage.closed_paper_signals", return_value=[])
    @patch("app.learning_summary", return_value={})
    @patch("app.context_for_pair", return_value={})
    @patch("app.fetch_institutional_context", return_value={})
    @patch("app.scalping_storage.log_market_snapshot")
    @patch("app.classify_market_regime")
    def test_scan_pair_skips_unavailable_vst_balance_without_journaling_or_execution(
            self, regime, log_snapshot, institutional, intelligence, learning, closed_signals, account_context,
            search, reviews, controls, decide, execute_order, mark_accepted):
        now = datetime.now(timezone.utc)
        bars = [{"datetime": 1}]
        provider = unittest.mock.Mock()
        provider.fetch_closed_bars.return_value = bars
        regime.return_value = unittest.mock.Mock(regime=MarketRegime.TRENDING_UP, features={})
        decide.return_value = AITradeDecision(
            "PROPOSE_TRADE", "test", "test", 80, risk_usdt=1, leverage=3,
            direction=Direction.BUY, entry_price=100, stop_price=98, target_price=103,
        )

        self.assertEqual(app.scan_pair("BTC-USDT", provider, now), [{
            "status": "SKIP", "reason": "VST balance state unavailable; order yuborilmadi",
        }])
        mark_accepted.assert_not_called()
        execute_order.assert_not_called()

    @patch("app.scalping_storage.risk_state", return_value=(2, -1.25))
    @patch("app.scalping_storage.open_paper_positions", return_value=1)
    @patch("broker.get_vst_usdt_balance", return_value={"equity_usdt": 100.0, "available_usdt": 80.0,
                                                          "unrealized_pnl_usdt": -0.5})
    def test_vst_account_context_exposes_only_normalized_sizing_values(self, balance, positions, risk_state):
        with patch.dict(os.environ, {"BINGX_API_KEY": "key", "BINGX_SECRET": "secret"}):
            self.assertEqual(app._vst_account_context(), {
                "available": True, "open_strategy_positions": 1, "daily_strategy_pnl_usdt": -1.25,
                "equity_usdt": 100.0, "available_usdt": 80.0, "unrealized_pnl_usdt": -0.5,
            })

    def test_signals_api_requires_dashboard_token_when_configured(self):
        with patch.object(app, "DASHBOARD_TOKEN", "secret"):
            response = app.app.test_client().get("/api/signals")
        self.assertEqual(response.status_code, 401)

    @patch("app.handle_update")
    def test_telegram_webhook_dispatches_authorized_update(self, handle_update):
        with patch.dict(os.environ, {"TELEGRAM_WEBHOOK_SECRET": "secret"}):
            response = app.app.test_client().post(
                "/telegram/webhook", json={"message": {"text": "/start"}},
                headers={"X-Telegram-Bot-Api-Secret-Token": "secret"},
            )
        self.assertEqual(response.status_code, 200)
        handle_update.assert_called_once_with({"message": {"text": "/start"}})

    @patch("app.handle_update")
    def test_telegram_webhook_rejects_wrong_secret(self, handle_update):
        with patch.dict(os.environ, {"TELEGRAM_WEBHOOK_SECRET": "secret"}):
            response = app.app.test_client().post("/telegram/webhook", json={}, headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"})
        self.assertEqual(response.status_code, 401)
        handle_update.assert_not_called()


if __name__ == "__main__":
    unittest.main()