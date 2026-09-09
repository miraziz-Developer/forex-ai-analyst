import os
import unittest
from unittest.mock import patch


for key in (
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_DEPLOYMENT",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "TURSO_DATABASE_URL",
    "TURSO_AUTH_TOKEN",
):
    os.environ.setdefault(key, "test")

import app


class HealthTests(unittest.TestCase):
    def test_health_identifies_deployed_commit_and_promoted_policy(self):
        with patch.dict(os.environ, {"RENDER_GIT_COMMIT": "abc123"}):
            response = app.app.test_client().get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {
            "status": "ok",
            "commit": "abc123",
            "policy": {
                "strategy": "volatility_breakout",
                "atr_stop": 2.0,
                "reward_risk": 2.5,
            },
        })

    def test_health_fails_closed_without_promoted_policy(self):
        with patch("app.load_promoted_policy", return_value=None):
            response = app.app.test_client().get("/health")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["status"], "degraded")
        self.assertIsNone(response.get_json()["policy"])


if __name__ == "__main__":
    unittest.main()