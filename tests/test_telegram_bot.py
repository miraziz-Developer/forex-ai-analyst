import os
import sys
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("TURSO_DATABASE_URL", "libsql://test.invalid")
os.environ.setdefault("TURSO_AUTH_TOKEN", "test")

from forex_ai_analyst.interfaces import telegram_bot as telegram_bot
from forex_ai_analyst.interfaces import telegram_chat as telegram_chat


class TelegramBotTests(unittest.TestCase):
    @patch("forex_ai_analyst.interfaces.telegram_chat.context", return_value={"safety": "read-only"})
    def test_ai_chat_uses_azure_openai_deployment_when_configured(self, context):
        completion = Mock()
        completion.choices = [SimpleNamespace(message=SimpleNamespace(content="Azure javob"))]
        client = Mock()
        client.chat.completions.create.return_value = completion
        openai_module = SimpleNamespace(OpenAI=Mock(return_value=client))
        with patch.dict(sys.modules, {"openai": openai_module}), patch.dict(os.environ, {
            "OPENAI_API_KEY": "", "AZURE_OPENAI_API_KEY": "azure-key",
            "AZURE_OPENAI_ENDPOINT": "https://resource.openai.azure.com",
            "AZURE_OPENAI_DEPLOYMENT": "chat-deployment",
        }, clear=False):
            self.assertEqual(telegram_chat.answer("Holat qanday?"), "Azure javob")
        openai_module.OpenAI.assert_called_once_with(
            api_key="azure-key", base_url="https://resource.openai.azure.com/openai/v1/")
        request = client.chat.completions.create.call_args.kwargs
        self.assertEqual(request["model"], "chat-deployment")
        self.assertNotIn("max_tokens", request)

    @patch("forex_ai_analyst.interfaces.telegram_bot.requests.post")
    def test_configure_webhook_registers_callback_updates_and_commands(self, post):
        post.return_value.raise_for_status.return_value = None
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "token", "TELEGRAM_WEBHOOK_SECRET": "secret"}):
            configured = telegram_bot.configure_webhook("https://bot.example.test/")
        self.assertTrue(configured)
        self.assertEqual(post.call_count, 2)
        self.assertEqual(post.call_args_list[0].args[0], "https://api.telegram.org/bottoken/setWebhook")
        self.assertEqual(post.call_args_list[0].kwargs["json"], {
            "url": "https://bot.example.test/telegram/webhook",
            "secret_token": "secret",
            "allowed_updates": ["message", "channel_post", "callback_query"],
            "drop_pending_updates": False,
        })
        self.assertEqual(post.call_args_list[1].kwargs["json"], {"commands": []})

    def test_configure_webhook_requires_complete_https_configuration(self):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "token", "TELEGRAM_WEBHOOK_SECRET": "secret"}):
            self.assertFalse(telegram_bot.configure_webhook("http://bot.example.test"))

    @patch("forex_ai_analyst.interfaces.telegram_bot.requests.post")
    def test_configure_webhook_uses_render_public_hostname_when_url_is_not_set(self, post):
        post.return_value.raise_for_status.return_value = None
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "token", "TELEGRAM_WEBHOOK_SECRET": "secret",
                                     "PUBLIC_BASE_URL": "", "RENDER_EXTERNAL_HOSTNAME": "bot.onrender.com"}, clear=False):
            self.assertTrue(telegram_bot.configure_webhook())
        self.assertEqual(post.call_args_list[0].kwargs["json"]["url"], "https://bot.onrender.com/telegram/webhook")

    def test_unauthorized_chat_is_ignored(self):
        with patch.dict(os.environ, {"TELEGRAM_CHAT_ID": "42"}), patch("forex_ai_analyst.interfaces.telegram_bot._reply") as reply:
            telegram_bot.handle_update({"message": {"chat": {"id": 7}, "text": "/help"}})
        reply.assert_not_called()

    @patch("forex_ai_analyst.interfaces.telegram_bot.telegram_chat.answer", return_value="AI javob")
    @patch("forex_ai_analyst.interfaces.telegram_bot._reply")
    def test_free_text_routes_to_read_only_ai_chat(self, reply, answer):
        with patch.dict(os.environ, {"TELEGRAM_CHAT_ID": "42"}):
            telegram_bot.handle_update({"message": {"chat": {"id": 42}, "text": "Nega signal o'tkazib yuborildi?"}})
        answer.assert_called_once_with("Nega signal o'tkazib yuborildi?")
        self.assertEqual(reply.call_args.args[1], "AI javob")

    @patch("forex_ai_analyst.interfaces.telegram_bot.runtime_controls.create_pending", return_value=("ABC123", datetime.now(timezone.utc)))
    @patch("forex_ai_analyst.interfaces.telegram_bot._reply")
    def test_control_request_requires_preview_confirmation(self, reply, pending):
        with patch.dict(os.environ, {"TELEGRAM_CHAT_ID": "42"}):
            telegram_bot.handle_update({"message": {"chat": {"id": 42}, "text": "STOP"}})
        pending.assert_called_once_with("42", {"kill_switch": True})
        self.assertIn("TASDIQLAYMAN ABC123", reply.call_args.args[1])

    @patch("forex_ai_analyst.interfaces.telegram_bot.runtime_controls.confirm", return_value={"kill_switch": True})
    @patch("forex_ai_analyst.interfaces.telegram_bot._reply")
    def test_confirmation_applies_persisted_preview(self, reply, confirm):
        with patch.dict(os.environ, {"TELEGRAM_CHAT_ID": "42"}):
            telegram_bot.handle_update({"message": {"chat": {"id": 42}, "text": "TASDIQLAYMAN aBc123"}})
        confirm.assert_called_once_with("42", "aBc123")
        self.assertIn("qo‘llandi", reply.call_args.args[1])

    @patch("forex_ai_analyst.interfaces.telegram_bot.runtime_controls.confirm", return_value=None)
    @patch("forex_ai_analyst.interfaces.telegram_bot._reply")
    def test_expired_confirmation_is_rejected(self, reply, confirm):
        with patch.dict(os.environ, {"TELEGRAM_CHAT_ID": "42"}):
            telegram_bot.handle_update({"message": {"chat": {"id": 42}, "text": "TASDIQLAYMAN DEAD00"}})
        self.assertIn("muddati tugagan", reply.call_args.args[1])

    @patch("forex_ai_analyst.interfaces.telegram_bot.telegram_chat.answer", return_value="Bu ruxsat etilmagan.")
    @patch("forex_ai_analyst.interfaces.telegram_bot._reply")
    def test_live_trading_request_is_never_a_control(self, reply, answer):
        with patch.dict(os.environ, {"TELEGRAM_CHAT_ID": "42"}):
            telegram_bot.handle_update({"message": {"chat": {"id": 42}, "text": "live tradingni yoq"}})
        answer.assert_called_once()

    def test_balance_relative_control_requests_require_explicit_percent_commands(self):
        self.assertEqual(telegram_bot._control_request("RISK PCT 1.5"), {"risk_per_trade_pct": 1.5})
        self.assertEqual(telegram_bot._control_request("DAILY LOSS PCT 5"), {"max_daily_loss_pct": 5.0})
        self.assertEqual(telegram_bot._control_request("MARGIN PCT 25"), {"max_margin_utilization_pct": 25.0})
        self.assertIsNone(telegram_bot._control_request("MAX RISK 50"))

    @patch("forex_ai_analyst.interfaces.telegram_bot._reply")
    @patch("forex_ai_analyst.interfaces.telegram_bot.knowledge.documents", return_value=[{"id": 3, "file_name": "risk.pdf", "page_count": 4}])
    def test_knowledge_button_lists_documents(self, documents, reply):
        with patch.dict(os.environ, {"TELEGRAM_CHAT_ID": "42"}):
            telegram_bot.handle_update({"callback_query": {"id": "callback-1", "data": "knowledge",
                                       "message": {"chat": {"id": 42}}}})
        self.assertIn("risk.pdf", reply.call_args.args[1])
        self.assertTrue(reply.call_args.kwargs["menu"])

    @patch("forex_ai_analyst.interfaces.telegram_bot._reply")
    def test_start_opens_button_menu(self, reply):
        with patch.dict(os.environ, {"TELEGRAM_CHAT_ID": "42"}):
            telegram_bot.handle_update({"message": {"chat": {"id": 42}, "text": "/start"}})
        self.assertTrue(reply.call_args.kwargs["menu"])
        self.assertIn("boshqaruv paneli", reply.call_args.args[1])

    @patch("forex_ai_analyst.interfaces.telegram_bot._reply")
    @patch("forex_ai_analyst.interfaces.telegram_bot._status_text", return_value="status")
    def test_status_command_uses_status_handler(self, status, reply):
        with patch.dict(os.environ, {"TELEGRAM_CHAT_ID": "42"}):
            telegram_bot.handle_update({"message": {"chat": {"id": 42}, "text": "/status@my_bot"}})
        reply.assert_called_once_with("42", "status", menu=True)

    @patch("forex_ai_analyst.interfaces.telegram_bot.scalping_storage.risk_state", return_value=(0, 0.0))
    @patch("forex_ai_analyst.interfaces.telegram_bot.scalping_storage.open_paper_positions", return_value=0)
    @patch("forex_ai_analyst.interfaces.telegram_bot.runtime_controls.settings",
           return_value={"kill_switch": True, "demo_execution": True})
    def test_status_includes_persisted_runtime_kill_switch(self, settings, positions, risk_state):
        with patch.dict(os.environ, {"AUTO_EXECUTE_TRADES": "true", "KILL_SWITCH": "false",
                                     "BINGX_API_KEY": "key", "BINGX_SECRET": "secret"}, clear=False):
            status = telegram_bot._status_text()
        self.assertIn("BingX VST execution: 🔴 o‘chiq", status)
        self.assertIn("Kill switch: 🔴 yoqilgan", status)

    @patch("forex_ai_analyst.interfaces.telegram_bot._reply")
    @patch("forex_ai_analyst.interfaces.telegram_bot.knowledge.search", return_value=[])
    def test_search_button_waits_for_plain_text_query(self, search, reply):
        with patch.dict(os.environ, {"TELEGRAM_CHAT_ID": "42"}):
            telegram_bot.handle_update({"callback_query": {"id": "callback-2", "data": "search",
                                       "message": {"chat": {"id": 42}}}})
            telegram_bot.handle_update({"message": {"chat": {"id": 42}, "text": "BTC risk"}})
        search.assert_called_once_with("BTC risk")
        self.assertIn("Mos bilim topilmadi", reply.call_args.args[1])

    @patch("forex_ai_analyst.interfaces.telegram_bot._reply")
    @patch("forex_ai_analyst.interfaces.telegram_bot.scalping_storage.open_paper_signals", return_value=[{
        "pair": "BTC-USDT", "direction": "BUY", "entry_price": 100, "target_price": 105,
        "stop_price": 98, "broker_order_id": "vst-7",
    }])
    def test_positions_button_lists_open_vst_position(self, positions, reply):
        with patch.dict(os.environ, {"TELEGRAM_CHAT_ID": "42"}):
            telegram_bot.handle_update({"callback_query": {"id": "callback-3", "data": "positions",
                                       "message": {"chat": {"id": 42}}}})
        self.assertIn("BTC-USDT BUY", reply.call_args.args[1])
        self.assertIn("#vst-7", reply.call_args.args[1])

    @patch("forex_ai_analyst.interfaces.telegram_bot._reply")
    @patch("forex_ai_analyst.interfaces.telegram_bot.scalping_storage.performance_summary", return_value={
        "closed_orders": 3, "wins": 2, "losses": 1, "expired": 0, "win_rate_pct": 66.7,
        "realized_pnl_usdt": 12.5, "today_pnl_usdt": -1.25,
    })
    def test_performance_button_shows_profit_loss_summary(self, summary, reply):
        with patch.dict(os.environ, {"TELEGRAM_CHAT_ID": "42"}):
            telegram_bot.handle_update({"callback_query": {"id": "callback-4", "data": "performance",
                                        "message": {"chat": {"id": 42}}}})
        self.assertIn("Journal P&L: +12.5 USDT", reply.call_args.args[1])
        self.assertIn("Bugungi journal P&L: -1.25 USDT", reply.call_args.args[1])

    @patch("forex_ai_analyst.trading.infrastructure.bingx_broker.vst_income_summary", return_value={
        "realized_pnl_usdt": 10.0, "fees_usdt": .5, "funding_usdt": -.2, "net_pnl_usdt": 9.3, "entries": 4,
    })
    @patch("forex_ai_analyst.interfaces.telegram_bot.scalping_storage.first_broker_order_time", return_value=datetime(2026, 9, 1, tzinfo=timezone.utc))
    @patch("forex_ai_analyst.interfaces.telegram_bot.scalping_storage.performance_summary", return_value={
        "closed_orders": 1, "wins": 1, "losses": 0, "expired": 0, "win_rate_pct": 100.0,
        "realized_pnl_usdt": 8.0, "today_pnl_usdt": 8.0,
    })
    def test_performance_separates_bingx_account_income_from_journal(self, summary, first_order, income):
        with patch.dict(os.environ, {"BINGX_API_KEY": "key", "BINGX_SECRET": "secret"}, clear=False):
            text = telegram_bot._performance_text()
        self.assertIn("Journal P&L: +8 USDT", text)
        self.assertIn("BingX VST account income", text)
        self.assertIn("Actual net: +9.3 USDT", text)

    @patch("forex_ai_analyst.interfaces.telegram_bot._reply")
    @patch("forex_ai_analyst.interfaces.telegram_bot.scalping_storage.closed_paper_signals", return_value=[{
        "pair": "BNB-USDT", "direction": "SELL", "entry_price": 731.0, "outcome_price": 727.0,
        "status": "WIN", "realized_pnl_usdt": 43.48, "broker_order_id": "vst-8",
    }])
    def test_closed_orders_button_shows_exit_and_profit_loss(self, orders, reply):
        with patch.dict(os.environ, {"TELEGRAM_CHAT_ID": "42"}):
            telegram_bot.handle_update({"callback_query": {"id": "callback-5", "data": "closed_orders",
                                        "message": {"chat": {"id": 42}}}})
        self.assertIn("Exit: 727", reply.call_args.args[1])
        self.assertIn("+43.48 USDT", reply.call_args.args[1])

    @patch("forex_ai_analyst.interfaces.telegram_bot.scalping_storage.recent_candidates", side_effect=RuntimeError("Turso unavailable"))
    def test_signal_database_error_returns_safe_message(self, candidates):
        self.assertIn("vaqtincha", telegram_bot._signals_text())

    @patch("forex_ai_analyst.interfaces.telegram_bot.send_telegram_message", return_value=True)
    @patch("forex_ai_analyst.interfaces.telegram_bot.requests.post")
    def test_reply_splits_messages_that_exceed_telegram_limit(self, post, send):
        post.return_value.raise_for_status.return_value = None
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "token"}):
            telegram_bot._reply("42", "x" * 8001, menu=True)
        self.assertEqual(post.call_args.kwargs["json"]["text"], "x" * 4000)
        self.assertEqual(send.call_count, 2)

    @patch("forex_ai_analyst.interfaces.telegram_bot.send_telegram_message", return_value=True)
    @patch("forex_ai_analyst.interfaces.telegram_bot.requests.post", side_effect=telegram_bot.requests.RequestException("Telegram down"))
    def test_reply_fallback_also_splits_messages_that_exceed_telegram_limit(self, post, send):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "token"}):
            telegram_bot._reply("42", "x" * 8001, menu=True)
        self.assertEqual([call.args[0] for call in send.call_args_list], ["x" * 4000, "x" * 4000, "x"])


if __name__ == "__main__":
    unittest.main()