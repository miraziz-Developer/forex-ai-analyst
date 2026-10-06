import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from forex_ai_analyst.forex import mt5_bot
from forex_ai_analyst.forex.regime_system_study import FX_ONLY


class FakeMT5:
    ACCOUNT_TRADE_MODE_DEMO, ACCOUNT_TRADE_MODE_REAL = 0, 2
    TRADE_ACTION_DEAL, ORDER_TYPE_BUY, ORDER_TYPE_SELL = 1, 0, 1
    ORDER_TIME_GTC, ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 0, 1, 2
    TRADE_RETCODE_DONE = 10009

    def __init__(self, mode=0):
        self.mode, self.sent, self.positions = mode, [], {}

    def account_info(self):
        return SimpleNamespace(trade_mode=self.mode, equity=10_000.0)

    def last_error(self):
        return (0, "ok")

    def symbol_info(self, name):
        if name not in [m.name for m in FX_ONLY]:
            return None
        return SimpleNamespace(trade_tick_size=0.00001, point=0.00001, trade_tick_value=1.0, volume_step=0.01,
                               volume_min=0.01, volume_max=100.0, filling_mode=1)

    def symbol_select(self, name, flag):
        return True

    def symbol_info_tick(self, name):
        return SimpleNamespace(ask=1.1002, bid=1.1000)

    def order_send(self, request):
        self.sent.append(request)
        ticket = 1000 + len(self.sent)
        if "position" not in request:
            self.positions[ticket] = SimpleNamespace(ticket=ticket, volume=request["volume"], profit=12.5)
        return SimpleNamespace(retcode=self.TRADE_RETCODE_DONE, order=ticket, price=request["price"], comment="done")

    def positions_get(self, ticket=None):
        p = self.positions.get(ticket)
        return (p,) if p else ()


def weekday_days(start, n):
    from datetime import date, timedelta
    out, d = [], date.fromisoformat(start)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def context(days):
    caches = {m.name: SimpleNamespace(days=days, f={"atr": [0.005] * len(days)}) for m in FX_ONLY}
    return SimpleNamespace(caches=caches, cot=None)


MODEL = {"version": "fx_logistic_test", "family": "fx_logistic", "uses_cot": False,
         "long_threshold": 0.55, "short_threshold": 0.45}


class Mt5BotTests(unittest.TestCase):
    def setUp(self):
        self.db = mt5_bot.open_db(":memory:")

    def test_refuses_a_real_account(self):
        with self.assertRaises(SystemExit):
            mt5_bot.require_demo(FakeMT5(mode=FakeMT5.ACCOUNT_TRADE_MODE_REAL))

    def test_volume_makes_the_stop_cost_the_risk_budget(self):
        mt5 = FakeMT5()
        # 1 lot: 1.0 money per 0.00001 -> 100,000 per 1.0 price; stop 0.015 -> 1,500 per lot; risk 50 -> 0.03 lot
        self.assertEqual(mt5_bot.volume_for(mt5, "EURUSD", 0.015, 50.0), 0.03)
        self.assertEqual(mt5_bot.volume_for(mt5, "EURUSD", 0.015, 1.0), 0.0)     # below the 0.01 minimum

    def test_monday_signals_open_once_and_close_after_five_trading_days(self):
        mt5 = FakeMT5()
        days = weekday_days("2026-10-05", 12)       # Monday 2026-10-05 onwards
        probs = iter([0.62, 0.31] + [0.5] * 50)
        with patch.object(mt5_bot, "load_context", return_value=context(days)), \
                patch.object(mt5_bot, "feature_row", return_value={"x": 1}), \
                patch.object(mt5_bot.ml_model, "load_all", return_value=[MODEL]), \
                patch.object(mt5_bot.ml_model, "probability_up", side_effect=lambda m, f: next(probs)):
            first = mt5_bot.cycle(mt5, self.db, datetime(2026, 10, 6, 1, 0, tzinfo=timezone.utc))
            again = mt5_bot.cycle(mt5, self.db, datetime(2026, 10, 6, 1, 15, tzinfo=timezone.utc))
            self.assertEqual(len([m for m in first if "📌" in m]), 2)
            self.assertEqual(again, [])
            self.assertEqual(mt5.sent[0]["type"], FakeMT5.ORDER_TYPE_BUY)
            self.assertLess(mt5.sent[0]["sl"], mt5.sent[0]["price"])
            self.assertEqual(mt5.sent[1]["type"], FakeMT5.ORDER_TYPE_SELL)
            # exit day = 5th weekday after Monday 10-05 = Monday 10-12; closes on the first cycle on 10-13
            not_yet = mt5_bot.cycle(mt5, self.db, datetime(2026, 10, 12, 23, 0, tzinfo=timezone.utc))
            self.assertFalse([m for m in not_yet if "yopildi" in m])
            closed = mt5_bot.close_due(mt5, self.db, "2026-10-13")
        self.assertEqual(len([m for m in closed if "yopildi" in m]), 2)
        statuses = {r[0] for r in self.db.execute("SELECT status FROM trades WHERE ticket IS NOT NULL")}
        self.assertEqual(statuses, {"CLOSED"})

    def test_signal_skipped_for_a_small_account_is_retried_the_next_day_only(self):
        small, big = FakeMT5(), FakeMT5()
        small.account_info = lambda: SimpleNamespace(trade_mode=0, equity=100.0)
        days = weekday_days("2026-10-05", 12)
        patches = (patch.object(mt5_bot, "load_context", return_value=context(days)),
                   patch.object(mt5_bot, "feature_row", return_value={"x": 1}),
                   patch.object(mt5_bot.ml_model, "load_all", return_value=[MODEL]),
                   patch.object(mt5_bot.ml_model, "probability_up", return_value=0.62))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        skipped = mt5_bot.cycle(small, self.db, datetime(2026, 10, 6, 1, 0, tzinfo=timezone.utc))
        self.assertTrue(all("o'tkazildi" in m for m in skipped))
        opened = mt5_bot.cycle(big, self.db, datetime(2026, 10, 6, 9, 0, tzinfo=timezone.utc))      # same Tuesday
        self.assertEqual(len([m for m in opened if "📌" in m]), len(FX_ONLY))
        self.assertEqual(mt5_bot.cycle(big, self.db, datetime(2026, 10, 6, 9, 15, tzinfo=timezone.utc)), [])
        late = FakeMT5()
        db2 = mt5_bot.open_db(":memory:")
        mt5_bot.cycle(small, db2, datetime(2026, 10, 6, 1, 0, tzinfo=timezone.utc))
        self.assertEqual(mt5_bot.cycle(late, db2, datetime(2026, 10, 7, 9, 0, tzinfo=timezone.utc)), [])   # Wednesday: too late

    def test_min_equity_explains_skipped_signals(self):
        # 0.01 lot * 0.015 stop * 100,000 per price unit = 15 money; at 0.5% that needs 3,000 equity
        self.assertAlmostEqual(mt5_bot.min_equity_for(FakeMT5(), "EURUSD", 0.015, 0.5), 3000.0)

    def test_two_model_families_trade_independently_and_cot_outage_only_skips_v2c(self):
        mt5 = FakeMT5()
        days = weekday_days("2026-10-05", 12)
        v2c = {**MODEL, "version": "fx_logistic_cot_c01_test", "family": "fx_logistic_cot_c01", "uses_cot": True}
        calls = []

        def fake_context(markets, with_cot=False):
            calls.append(with_cot)
            if with_cot:
                raise RuntimeError("CFTC down")
            return context(days)
        with patch.object(mt5_bot, "load_context", side_effect=fake_context), \
                patch.object(mt5_bot, "feature_row", return_value={"x": 1}), \
                patch.object(mt5_bot.ml_model, "load_all", return_value=[MODEL, v2c]), \
                patch.object(mt5_bot.ml_model, "probability_up", return_value=0.62):
            mt5_bot._COT_WARNED.clear()
            out = mt5_bot.cycle(mt5, self.db, datetime(2026, 10, 6, 1, 0, tzinfo=timezone.utc))
        self.assertTrue(any("CFTC" in m for m in out))
        self.assertEqual(len([m for m in out if m.startswith("[v1] 📌")]), len(FX_ONLY))
        self.assertFalse([m for m in out if m.startswith("[v2c]")])
        versions = {r[0] for r in self.db.execute("SELECT model_version FROM trades")}
        self.assertEqual(versions, {"fx_logistic_test"})

    def test_nth_weekday_after_skips_weekends(self):
        self.assertEqual(mt5_bot.nth_weekday_after("2026-10-05", 5), "2026-10-12")
        self.assertEqual(mt5_bot.nth_weekday_after("2026-10-09", 1), "2026-10-12")


if __name__ == "__main__":
    unittest.main()
