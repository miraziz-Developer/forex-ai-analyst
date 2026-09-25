import os
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

os.environ.setdefault("TURSO_DATABASE_URL", "libsql://test.invalid")
os.environ.setdefault("TURSO_AUTH_TOKEN", "test")

from forex_ai_analyst.trading.application.ai_trader import AITradeDecision, decide
from forex_ai_analyst.trading.domain.models import Direction, MarketRegime

WATCH = '{"action":"WATCH","rationale":"wait","invalidation":"none","confidence":40}'


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
        with patch.dict(os.environ, {"OPENAI_API_KEY": "", "AZURE_OPENAI_API_KEY": "",
                                     "AZURE_OPENAI_ENDPOINT": "", "AZURE_OPENAI_DEPLOYMENT": ""}, clear=False):
            result = decide({}, [], [])
        self.assertEqual(result.action, "SKIP")

    @patch("openai.OpenAI")
    def test_invalid_model_payload_skips(self, client_class):
        client_class.return_value.responses.create.return_value.output_text = '{"action":"PROPOSE_TRADE","confidence":80}'
        with patch.dict(os.environ, {"OPENAI_API_KEY": "key"}, clear=False):
            result = decide({}, [], [])
        self.assertEqual(result.action, "SKIP")

    @patch("openai.OpenAI")
    def test_azure_foundry_credentials_use_openai_compatible_endpoint(self, client_class):
        client_class.return_value.responses.create.return_value.output_text = WATCH
        azure_environment = {
            "OPENAI_API_KEY": "",
            "AZURE_OPENAI_API_KEY": "azure-key",
            "AZURE_OPENAI_ENDPOINT": "https://example.services.ai.azure.com/openai/v1",
            "AZURE_OPENAI_DEPLOYMENT": "trader-deployment",
            "AZURE_OPENAI_API_VERSION": "2024-10-21",
        }
        with patch.dict(os.environ, azure_environment, clear=False):
            result = decide({}, [], [])
        self.assertEqual(result.action, "WATCH")
        client_class.assert_called_once_with(
            api_key="azure-key", base_url="https://example.services.ai.azure.com/openai/v1/",
        )
        request = client_class.return_value.responses.create.call_args.kwargs
        self.assertEqual(request["model"], "trader-deployment")
        self.assertNotIn("temperature", request)  # reasoning deployments reject it
        self.assertTrue(request["text"]["format"]["strict"])

    def test_azure_base_url_adds_openai_v1_for_resource_endpoint(self):
        from forex_ai_analyst.shared.llm import _azure_openai_base_url
        self.assertEqual(_azure_openai_base_url("https://example.openai.azure.com/"),
                         "https://example.openai.azure.com/openai/v1/")

    @patch("openai.OpenAI")
    def test_balance_relative_execution_envelope_is_included_in_ai_context(self, client_class):
        client_class.return_value.responses.create.return_value.output_text = WATCH
        limits = {"risk_per_trade_pct": 1.0, "max_daily_loss_pct": 5.0, "max_margin_utilization_pct": 25.0}
        with patch.dict(os.environ, {"OPENAI_API_KEY": "key"}, clear=False):
            decide({}, [], [], limits)
        payload = client_class.return_value.responses.create.call_args.kwargs["input"]
        self.assertEqual(__import__("json").loads(payload)["execution_limits"], limits)


CLAUDE_ENV = {"AZURE_ANTHROPIC_ENDPOINT": "https://proj.services.ai.azure.com/anthropic/",
              "AZURE_ANTHROPIC_API_KEY": "foundry-key", "AZURE_ANTHROPIC_DEPLOYMENT": "claude-opus-5"}
TRADE = ('{"action":"PROPOSE_TRADE","rationale":"breakout","invalidation":"close below 98","confidence":78,'
         '"direction":"BUY","entry_price":100,"stop_price":98,"target_price":104,"risk_usdt":1,"leverage":2,'
         '"cooldown_minutes":60,"citations":[]}')


def claude_response(text, stop_reason="end_turn"):
    from types import SimpleNamespace
    return SimpleNamespace(stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=text)])


class ClaudeFoundryTests(unittest.TestCase):
    @patch("forex_ai_analyst.shared.llm._claude_client")
    def test_claude_is_primary_and_uses_structured_output_schema(self, client):
        client.return_value.messages.create.return_value = claude_response(TRADE)
        with patch.dict(os.environ, {**CLAUDE_ENV, "OPENAI_API_KEY": "fallback-key"}, clear=False):
            result = decide({}, [], [])
        self.assertEqual((result.action, result.direction, result.raw["provider"]), ("PROPOSE_TRADE", Direction.BUY, "claude"))
        request = client.return_value.messages.create.call_args.kwargs
        self.assertEqual(request["model"], "claude-opus-5")
        self.assertEqual(request["output_config"]["format"]["type"], "json_schema")
        self.assertIn("action", request["output_config"]["format"]["schema"]["required"])

    @patch("openai.OpenAI")
    @patch("forex_ai_analyst.shared.llm._claude_client")
    def test_refusal_falls_back_to_openai(self, client, openai_class):
        client.return_value.messages.create.return_value = claude_response("", stop_reason="refusal")
        openai_class.return_value.responses.create.return_value.output_text = WATCH
        with patch.dict(os.environ, {**CLAUDE_ENV, "OPENAI_API_KEY": "fallback-key"}, clear=False):
            result = decide({}, [], [])
        self.assertEqual((result.action, result.raw["provider"]), ("WATCH", "openai"))

    @patch("forex_ai_analyst.shared.llm._claude_client")
    def test_claude_api_error_without_fallback_fails_closed(self, client):
        import anthropic
        import httpx2
        request = httpx2.Request("POST", "https://proj.services.ai.azure.com/anthropic/v1/messages")
        client.return_value.messages.create.side_effect = anthropic.APIConnectionError(request=request)
        env = {**CLAUDE_ENV, "OPENAI_API_KEY": "", "AZURE_OPENAI_API_KEY": "", "AZURE_OPENAI_ENDPOINT": "",
               "AZURE_OPENAI_DEPLOYMENT": ""}
        with patch.dict(os.environ, env, clear=False):
            result = decide({}, [], [])
        self.assertEqual(result.action, "SKIP")

    @patch("forex_ai_analyst.shared.llm._claude_client")
    def test_truncated_claude_answer_is_never_acted_on(self, client):
        client.return_value.messages.create.return_value = claude_response(TRADE[:40], stop_reason="max_tokens")
        env = {**CLAUDE_ENV, "OPENAI_API_KEY": "", "AZURE_OPENAI_API_KEY": ""}
        with patch.dict(os.environ, env, clear=False):
            self.assertEqual(decide({}, [], []).action, "SKIP")


if __name__ == "__main__":
    unittest.main()