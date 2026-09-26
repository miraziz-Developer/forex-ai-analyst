import os
import unittest
from unittest.mock import patch

os.environ.setdefault("TURSO_DATABASE_URL", "libsql://test.invalid")
os.environ.setdefault("TURSO_AUTH_TOKEN", "test")

from forex_ai_analyst.operations import incidents as execution_alerts


class ExecutionAlertTests(unittest.TestCase):
    @patch("forex_ai_analyst.operations.incidents.storage._execute")
    @patch("forex_ai_analyst.operations.incidents.storage._rows_as_dicts", return_value=[{
        "state": "OPEN", "notified_at": "2000-01-01T00:00:00+00:00"}])
    @patch("forex_ai_analyst.operations.incidents.send_telegram_message")
    def test_non_reminding_incident_notifies_only_once_while_open(self, send, rows_as_dicts, execute):
        self.assertFalse(execution_alerts.report("vst-account-context-unavailable", "unsafe", remind_after_minutes=None))
        send.assert_not_called()
        args = execute.call_args.args[1]
        self.assertIsNone(args[-2])
        self.assertEqual(args[-1], 0)

    @patch("forex_ai_analyst.operations.incidents.storage._execute")
    @patch("forex_ai_analyst.operations.incidents.storage._rows_as_dicts")
    @patch("forex_ai_analyst.operations.incidents.send_telegram_message")
    def test_resolved_incident_reopens_and_notifies_immediately(self, send, rows_as_dicts, execute):
        rows_as_dicts.return_value = [{"state": "RESOLVED", "notified_at": "2999-01-01T00:00:00+00:00"}]
        self.assertTrue(execution_alerts.report("k", "unsafe"))
        args = execute.call_args.args[1]
        self.assertIsNotNone(args[-2])
        self.assertEqual(args[-1], 1)

    @patch("forex_ai_analyst.operations.incidents.storage._execute", return_value={"affected_row_count": 1})
    def test_resolution_marks_only_open_incidents_closed(self, execute):
        self.assertTrue(execution_alerts.resolve("k", note="broker recovered"))
        self.assertIn("state = 'RESOLVED'", execute.call_args.args[0])
        self.assertEqual(execute.call_args.args[1][-1], "k")

    @patch("forex_ai_analyst.operations.incidents.storage._rows_as_dicts")
    @patch("forex_ai_analyst.operations.incidents.storage._execute", return_value={"affected_row_count": 1})
    def test_orphaned_row_incidents_close_but_live_ones_stay(self, execute, rows_as_dicts):
        rows_as_dicts.return_value = [{"incident_key": "open-sla:gone"}, {"incident_key": "open-sla:live"}]
        closed = execution_alerts.resolve_orphaned(("open-sla:",), {"live"}, note="row closed")
        self.assertEqual(closed, 1)
        sql, params = execute.call_args.args
        self.assertIn("IN (?)", sql)
        self.assertEqual(params[2:], ["open-sla:gone"])

    @patch("forex_ai_analyst.operations.incidents.storage._execute")
    @patch("forex_ai_analyst.operations.incidents.storage._rows_as_dicts")
    def test_status_breaks_open_incidents_down_by_type(self, rows_as_dicts, execute):
        rows_as_dicts.side_effect = [[{"open_incidents": 3, "last_incident_at": "t"}],
                                     [{"kind": "open-sla", "n": 2}, {"kind": "kill-switch", "n": 1}]]
        self.assertEqual(execution_alerts.status(), {"open_incidents": 3, "last_incident_at": "t",
                                                     "by_type": {"open-sla": 2, "kill-switch": 1}})

    @patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "token", "TELEGRAM_CHAT_ID": "chat"})
    @patch("forex_ai_analyst.operations.incidents.storage._execute", return_value={"affected_row_count": 1})
    @patch("forex_ai_analyst.operations.incidents.send_telegram_message")
    def test_requested_resolution_notification_is_sent_once_when_condition_clears(self, send, execute):
        self.assertTrue(execution_alerts.resolve("k", note="account recovered", notify=True))
        send.assert_called_once_with("✅ VST RECOVERED: account recovered", "token", "chat")

    @patch("forex_ai_analyst.operations.incidents.storage._execute", return_value={"affected_row_count": 2})
    def test_resolve_prefix_closes_every_open_incident_sharing_the_prefix(self, execute):
        self.assertEqual(execution_alerts.resolve_prefix("risk-control:BTC-USDT:", note="permission restored"), 2)
        sql, args = execute.call_args.args
        self.assertIn("LIKE ? ESCAPE", sql)
        self.assertIn("state = 'OPEN'", sql)
        self.assertEqual(args[-1], "risk-control:BTC-USDT:%")

    @patch("forex_ai_analyst.operations.incidents.storage._execute", return_value={"affected_row_count": 0})
    def test_resolve_prefix_escapes_like_wildcards_in_the_prefix(self, execute):
        execution_alerts.resolve_prefix("risk-control:BTC_USDT:")
        self.assertEqual(execute.call_args.args[1][-1], "risk-control:BTC\\_USDT:%")


if __name__ == "__main__":
    unittest.main()