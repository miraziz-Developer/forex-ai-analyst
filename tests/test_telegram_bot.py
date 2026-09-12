import os
import unittest
from unittest.mock import patch

os.environ.setdefault("TURSO_DATABASE_URL", "libsql://test.invalid")
os.environ.setdefault("TURSO_AUTH_TOKEN", "test")

import telegram_bot


class TelegramBotTests(unittest.TestCase):
    def test_unauthorized_chat_is_ignored(self):
        with patch.dict(os.environ, {"TELEGRAM_CHAT_ID": "42"}), patch("telegram_bot._reply") as reply:
            telegram_bot.handle_update({"message": {"chat": {"id": 7}, "text": "/help"}})
        reply.assert_not_called()

    @patch("telegram_bot._reply")
    @patch("telegram_bot.knowledge.documents", return_value=[{"id": 3, "file_name": "risk.pdf", "page_count": 4}])
    def test_knowledge_button_lists_documents(self, documents, reply):
        with patch.dict(os.environ, {"TELEGRAM_CHAT_ID": "42"}):
            telegram_bot.handle_update({"callback_query": {"id": "callback-1", "data": "knowledge",
                                       "message": {"chat": {"id": 42}}}})
        self.assertIn("risk.pdf", reply.call_args.args[1])
        self.assertTrue(reply.call_args.kwargs["menu"])

    @patch("telegram_bot._reply")
    def test_start_opens_button_menu(self, reply):
        with patch.dict(os.environ, {"TELEGRAM_CHAT_ID": "42"}):
            telegram_bot.handle_update({"message": {"chat": {"id": 42}, "text": "/start"}})
        self.assertTrue(reply.call_args.kwargs["menu"])
        self.assertIn("boshqaruv paneli", reply.call_args.args[1])

    @patch("telegram_bot._reply")
    @patch("telegram_bot.knowledge.search", return_value=[])
    def test_search_button_waits_for_plain_text_query(self, search, reply):
        with patch.dict(os.environ, {"TELEGRAM_CHAT_ID": "42"}):
            telegram_bot.handle_update({"callback_query": {"id": "callback-2", "data": "search",
                                       "message": {"chat": {"id": 42}}}})
            telegram_bot.handle_update({"message": {"chat": {"id": 42}, "text": "BTC risk"}})
        search.assert_called_once_with("BTC risk")
        self.assertIn("Mos bilim topilmadi", reply.call_args.args[1])

    @patch("telegram_bot._reply")
    @patch("telegram_bot.scalping_storage.open_paper_signals", return_value=[{
        "pair": "BTC-USDT", "direction": "BUY", "entry_price": 100, "target_price": 105,
        "stop_price": 98, "broker_order_id": "vst-7",
    }])
    def test_positions_button_lists_open_vst_position(self, positions, reply):
        with patch.dict(os.environ, {"TELEGRAM_CHAT_ID": "42"}):
            telegram_bot.handle_update({"callback_query": {"id": "callback-3", "data": "positions",
                                       "message": {"chat": {"id": 42}}}})
        self.assertIn("BTC-USDT BUY", reply.call_args.args[1])
        self.assertIn("#vst-7", reply.call_args.args[1])

    @patch("telegram_bot.scalping_storage.recent_candidates", side_effect=RuntimeError("Turso unavailable"))
    def test_signal_database_error_returns_safe_message(self, candidates):
        self.assertIn("vaqtincha", telegram_bot._signals_text())

    @patch("telegram_bot.send_telegram_message", return_value=True)
    @patch("telegram_bot.requests.post")
    def test_reply_splits_messages_that_exceed_telegram_limit(self, post, send):
        post.return_value.raise_for_status.return_value = None
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "token"}):
            telegram_bot._reply("42", "x" * 8001, menu=True)
        self.assertEqual(post.call_args.kwargs["json"]["text"], "x" * 4000)
        self.assertEqual(send.call_count, 2)

    @patch("telegram_bot.send_telegram_message", return_value=True)
    @patch("telegram_bot.requests.post", side_effect=telegram_bot.requests.RequestException("Telegram down"))
    def test_reply_fallback_also_splits_messages_that_exceed_telegram_limit(self, post, send):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "token"}):
            telegram_bot._reply("42", "x" * 8001, menu=True)
        self.assertEqual([call.args[0] for call in send.call_args_list], ["x" * 4000, "x" * 4000, "x"])


if __name__ == "__main__":
    unittest.main()