import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from forex_ai_analyst.forex import engine_health as health
from forex_ai_analyst.forex import mt5_bot
from test_mt5_bot import FakeMT5

T0 = datetime(2026, 10, 8, 7, tzinfo=timezone.utc)


class EngineHealthTests(unittest.TestCase):
    def setUp(self):
        self.db = mt5_bot.open_db(":memory:")

    def test_recent_success_is_healthy_and_not_alerted(self):
        health.record_ok(self.db, "trend", T0)
        now = T0 + timedelta(minutes=20)
        self.assertEqual(health.stalled(self.db, ["trend"], now, T0 - timedelta(hours=1)), [])
        self.assertTrue(health.summary(self.db, ["trend"], now)[0].startswith("✅ trend"))

    def test_stalled_engine_is_alerted_once_per_six_hours_with_its_last_error(self):
        health.record_ok(self.db, "index", T0)
        health.record_error(self.db, "index", T0 + timedelta(minutes=15), KeyError("close"))
        now = T0 + timedelta(minutes=60)
        alerts = health.stalled(self.db, ["index"], now, T0 - timedelta(hours=1))
        self.assertEqual(len(alerts), 1)
        self.assertIn("KeyError", alerts[0])
        self.assertEqual(health.stalled(self.db, ["index"], now + timedelta(hours=1), T0), [])
        self.assertEqual(len(health.stalled(self.db, ["index"], now + timedelta(hours=7), T0)), 1)
        self.assertTrue(health.summary(self.db, ["index"], now)[0].startswith("❌ index"))

    def test_fast_engines_have_a_shorter_limit(self):
        health.record_ok(self.db, "rebound", T0)
        now = T0 + timedelta(minutes=12)
        self.assertEqual(len(health.stalled(self.db, ["rebound"], now, T0 - timedelta(hours=1))), 1)

    def test_no_alert_before_the_bot_has_run_long_enough(self):
        self.assertEqual(health.stalled(self.db, ["trend"], T0 + timedelta(minutes=5), T0), [])
        self.assertEqual(len(health.stalled(self.db, ["trend"], T0 + timedelta(minutes=50), T0)), 1)

    def test_connection_lost(self):
        mt5 = FakeMT5()
        self.assertFalse(health.connection_lost(mt5))
        mt5.terminal_info = lambda: SimpleNamespace(connected=False)
        self.assertTrue(health.connection_lost(mt5))
        mt5.account_info = lambda: None
        self.assertTrue(health.connection_lost(mt5))


class ReconcileTests(unittest.TestCase):
    def test_rows_without_a_broker_position_are_closed_unless_their_engine_manages_them(self):
        from unittest.mock import patch
        db = mt5_bot.open_db(":memory:")
        for version, ticket in (("fx_logistic_cot_c01", 11), ("crypto_donchian_h4_v1", 12), ("fx_logistic", 13)):
            db.execute("INSERT INTO trades (model_version, decision_day, market, symbol, side, prob, ticket, volume, "
                       "exit_day, created_at, status) VALUES (?, '2026-10-05', 'EURJPY', 'EURJPY', 1, 0.6, ?, 0.1, "
                       "'2026-10-12', 'x', 'OPEN')", [version, ticket])
        mt5 = FakeMT5()
        mt5.positions[13] = SimpleNamespace(ticket=13, volume=0.1, profit=1.0)        # still at the broker
        with patch.dict("os.environ", {"FX_BOT_ENGINES": "trend,fix,index,crypto,gold,rebound"}):
            out = mt5_bot.reconcile_orphans(mt5, db)
        self.assertEqual(len(out), 1)
        status = dict(db.execute("SELECT ticket, status FROM trades").fetchall())
        self.assertEqual(status, {11: "CLOSED", 12: "OPEN", 13: "OPEN"})


class TickHealthTests(unittest.TestCase):
    def test_tick_records_success_and_failure_per_engine(self):
        db = mt5_bot.open_db(":memory:")
        mt5 = FakeMT5()
        mt5_bot.tick(mt5, db, T0, False, {})
        self.assertIsNotNone(mt5_bot.guards.get(db, "engine_ok:fix"))
        mt5.account_info = lambda: (_ for _ in ()).throw(RuntimeError("terminal busy"))
        mt5_bot.tick(mt5, db, T0 + timedelta(minutes=1), False, {})
        self.assertIn("RuntimeError", mt5_bot.guards.get(db, "engine_err:fix"))

    def test_startup_report_lists_every_engine_and_how_to_enable_the_off_ones(self):
        from unittest.mock import patch
        with patch.dict("os.environ", {"FX_BOT_ENGINES": "trend,fix,index,crypto,gold"}):
            text = mt5_bot.startup_report(FakeMT5())
        for name in ("trend", "gold", "crypto", "index", "rebound", "fix"):
            self.assertIn(f" {name}:", text)
        self.assertIn("⏸ o'chiq rebound", text)
        self.assertIn("FX_BOT_ENGINES=crypto,fix,gold,index,rebound,trend", text)
        self.assertIn("logs", text)


if __name__ == "__main__":
    unittest.main()
