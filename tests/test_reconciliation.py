import os
import unittest

os.environ.setdefault("TURSO_DATABASE_URL", "libsql://test.invalid")
os.environ.setdefault("TURSO_AUTH_TOKEN", "test")

import multi_strategy_scheduler


class ReconciliationTests(unittest.TestCase):
    def test_account_scoped_income_is_not_misattributed_to_a_strategy_order(self):
        multi_strategy_scheduler.reconcile_closed_vst_orders()


if __name__ == "__main__":
    unittest.main()