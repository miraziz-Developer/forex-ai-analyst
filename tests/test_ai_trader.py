import os
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

os.environ.setdefault("TURSO_DATABASE_URL", "libsql://test.invalid")
os.environ.setdefault("TURSO_AUTH_TOKEN", "test")

from ai_trader import AITradeDecision, decide
from scalping_core import Direction, MarketRegime


class AITraderTests(unittest.TestCase):
    def test_invalid_trade_levels_fail_closed(self):
        decision = AITradeDecision("PROPOSE_TRADE", "x", "x", 80, risk_usdt=1, leverage=2,
                                   direction=Direction.BUY, entry_price=100, stop_price=101, target_price=103)
        self.assertEqual(decision.validation_error(), "AI BUY levels are invalid")

    def test_valid_trade_becomes_contextual_candidate(self):
        decision = AITradeDecision("PROPOSE_TRADE", "trend confirmed", "break below 98", 80, risk_usdt=1,
                                   leverage=3, cooldown_minutes=30, direction=Direction.BUY,
                                   entry_price=100, stop_price=98, target_price=104)
        candidate = decision.to_candidate("btc-usdt", MarketRegime.TRENDING_UP, 1, datetime.now(timezone.utc))
        self.assertEqual(candidate.strategy, "contextual_ai")
        self.assertEqual(candidate.features["ai_leverage"], 3)

    def test_missing_api_key_skips_without_network_call(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False):
            result = decide({}, [], [])
        self.assertEqual(result.action, "SKIP")

    @patch("openai.OpenAI")
    def test_invalid_model_payload_skips(self, client_class):
        message = Mock(content='{"action":"PROPOSE_TRADE","confidence":80}')
        client_class.return_value.chat.completions.create.return_value.choices = [Mock(message=message)]
        with patch.dict(os.environ, {"OPENAI_API_KEY": "key"}, clear=False):
            result = decide({}, [], [])
        self.assertEqual(result.action, "SKIP")


if __name__ == "__main__":
    unittest.main()