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
    def test_knowledge_command_lists_documents(self, documents, reply):
        with patch.dict(os.environ, {"TELEGRAM_CHAT_ID": "42"}):
            telegram_bot.handle_update({"message": {"chat": {"id": 42}, "text": "/knowledge"}})
        self.assertIn("risk.pdf", reply.call_args.args[1])


if __name__ == "__main__":
    unittest.main()