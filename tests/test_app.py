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
        with patch.dict(os.environ, {"MULTI_STRATEGY_PROVIDER": "bingx"}):
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
        })

    def test_auto_execute_requires_both_bingx_vst_credentials(self):
        with patch.object(app, "AUTO_EXECUTE_TRADES_CONFIGURED", True):
            with patch.dict(os.environ, {"AUTO_EXECUTE_TRADES": "true", "BINGX_API_KEY": "", "BINGX_SECRET": ""}):
                response = app.app.test_client().get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["auto_execute_trades_configured"])
        self.assertFalse(response.get_json()["auto_execute_trades"])

    def test_auto_execute_uses_bingx_vst_only_when_fully_configured(self):
        with patch.dict(os.environ, {"AUTO_EXECUTE_TRADES": "true", "BINGX_API_KEY": "key", "BINGX_SECRET": "secret"}):
            response = app.app.test_client().get("/health")
        self.assertTrue(response.get_json()["auto_execute_trades"])

    @patch("app.demo_execution_enabled", return_value=False)
    def test_bingx_vst_order_is_not_attempted_when_execution_is_disabled(self, enabled):
        decision = AITradeDecision("PROPOSE_TRADE", "test", "test", 80, risk_usdt=.75, leverage=3,
                                   direction=Direction.BUY, entry_price=100, stop_price=98, target_price=103)
        self.assertIsNone(app.execute_bingx_vst_order(self._candidate(), decision))

    @patch("broker.place_market_order", return_value={"order_id": "vst-1", "fill_price": 100.25})
    @patch("broker.round_quantity", return_value=.3)
    @patch("app.demo_execution_enabled", return_value=True)
    def test_bingx_vst_order_uses_signal_levels_and_rounded_quantity(self, enabled, rounded, place_order):
        candidate = self._candidate()
        decision = AITradeDecision("PROPOSE_TRADE", "test", "test", 80, risk_usdt=.75, leverage=4,
                                   direction=Direction.BUY, entry_price=100, stop_price=98, target_price=103)
        result = app.execute_bingx_vst_order(candidate, decision)
        place_order.assert_called_once_with("BTC-USDT", "BUY", .3, 103, 98, leverage=4)
        self.assertEqual(result, {"order_id": "vst-1", "fill_price": 100.25, "quantity": .3})

    def test_signals_api_requires_dashboard_token_when_configured(self):
        with patch.object(app, "DASHBOARD_TOKEN", "secret"):
            response = app.app.test_client().get("/api/signals")
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()