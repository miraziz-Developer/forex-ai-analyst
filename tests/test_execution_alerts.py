import os
import unittest
from unittest.mock import patch

os.environ.setdefault("TURSO_DATABASE_URL", "libsql://test.invalid")
os.environ.setdefault("TURSO_AUTH_TOKEN", "test")

import execution_alerts


class ExecutionAlertTests(unittest.TestCase):
    @patch("execution_alerts.storage._execute")
    @patch("execution_alerts.storage._rows_as_dicts", return_value=[{
        "state": "OPEN", "notified_at": "2000-01-01T00:00:00+00:00"}])
    @patch("execution_alerts.send_telegram_message")
    def test_non_reminding_incident_notifies_only_once_while_open(self, send, rows_as_dicts, execute):
        self.assertFalse(execution_alerts.report("vst-account-context-unavailable", "unsafe", remind_after_minutes=None))
        send.assert_not_called()
        args = execute.call_args.args[1]
        self.assertIsNone(args[-2])
        self.assertEqual(args[-1], 0)

    @patch("execution_alerts.storage._execute")
    @patch("execution_alerts.storage._rows_as_dicts")
    @patch("execution_alerts.send_telegram_message")
    def test_resolved_incident_reopens_and_notifies_immediately(self, send, rows_as_dicts, execute):
        rows_as_dicts.return_value = [{"state": "RESOLVED", "notified_at": "2999-01-01T00:00:00+00:00"}]
        self.assertTrue(execution_alerts.report("k", "unsafe"))
        args = execute.call_args.args[1]
        self.assertIsNotNone(args[-2])
        self.assertEqual(args[-1], 1)

    @patch("execution_alerts.storage._execute", return_value={"affected_row_count": 1})
    def test_resolution_marks_only_open_incidents_closed(self, execute):
        self.assertTrue(execution_alerts.resolve("k", note="broker recovered"))
        self.assertIn("state = 'RESOLVED'", execute.call_args.args[0])
        self.assertEqual(execute.call_args.args[1][-1], "k")

    @patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "token", "TELEGRAM_CHAT_ID": "chat"})
    @patch("execution_alerts.storage._execute", return_value={"affected_row_count": 1})
    @patch("execution_alerts.send_telegram_message")
    def test_requested_resolution_notification_is_sent_once_when_condition_clears(self, send, execute):
        self.assertTrue(execution_alerts.resolve("k", note="account recovered", notify=True))
        send.assert_called_once_with("✅ VST RECOVERED: account recovered", "token", "chat")


if __name__ == "__main__":
    unittest.main()