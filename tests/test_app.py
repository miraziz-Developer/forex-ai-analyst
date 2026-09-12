import os
import unittest
from unittest.mock import patch


for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TURSO_DATABASE_URL", "TURSO_AUTH_TOKEN"):
    os.environ.setdefault(key, "test")

import app


class HealthTests(unittest.TestCase):
    def test_health_identifies_the_single_paper_only_service(self):
        with patch.dict(os.environ, {"MULTI_STRATEGY_PROVIDER": "binance_futures"}):
            response = app.app.test_client().get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {
            "status": "ok",
            "service": "multi-strategy-paper",
            "paper_only": True,
            "provider": "binance_futures",
            "auto_execute_trades": False,
        })

    def test_signals_api_requires_dashboard_token_when_configured(self):
        with patch.object(app, "DASHBOARD_TOKEN", "secret"):
            response = app.app.test_client().get("/api/signals")
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()