import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("TURSO_DATABASE_URL", "libsql://test.invalid")
os.environ.setdefault("TURSO_AUTH_TOKEN", "test")

from forex_ai_analyst.shared import turso


def response(rows_read):
    r = Mock()
    r.json.return_value = {"results": [{"type": "ok", "response": {"type": "execute", "result": {
        "cols": [], "rows": [], "rows_read": rows_read, "rows_written": 0}}}, {"type": "ok"}]}
    return r


class UsageTests(unittest.TestCase):
    def setUp(self):
        turso._USAGE.update(day=None, rows_read=0, queries=0)

    @patch("forex_ai_analyst.shared.turso.requests.post", side_effect=[response(40), response(2), response(None)])
    def test_rows_read_are_summed_per_day_from_turso_responses(self, post):
        for _ in range(3):
            turso._execute("SELECT 1")
        usage = turso.usage_today()
        self.assertEqual((usage["rows_read"], usage["queries"]), (42, 3))


if __name__ == "__main__":
    unittest.main()
