import os
import sqlite3
import unittest
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("TURSO_DATABASE_URL", "libsql://test.invalid")
os.environ.setdefault("TURSO_AUTH_TOKEN", "test")

from forex_ai_analyst.forex import ml_shadow
from forex_ai_analyst.forex.regime_system_study import FX_ONLY


class FakeTurso:
    def __init__(self):
        self.db = sqlite3.connect(":memory:")

    def execute(self, sql, args=None):
        cur = self.db.execute(sql, args or [])
        self.db.commit()
        cols = [{"name": d[0]} for d in (cur.description or [])]
        return {"cols": cols, "rows": [list(r) for r in cur.fetchall()], "affected_row_count": cur.rowcount}

    @staticmethod
    def rows(result):
        return [dict(zip([c["name"] for c in result["cols"]], row)) for row in result["rows"]]


def weekday_days(start: date, n: int) -> list[str]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def fake_context(days):
    caches = {}
    for k, m in enumerate(FX_ONLY):
        bars = [{"open": 100.0 + j, "close": 100.5 + j} for j in range(len(days))]
        caches[m.name] = SimpleNamespace(days=days, bars=bars, cost=0.0)
    return SimpleNamespace(caches=caches)


MODEL = {"version": "fx_logistic_test", "horizon_days": 5, "long_threshold": 0.55, "short_threshold": 0.45}


class ShadowTests(unittest.TestCase):
    def setUp(self):
        self.turso = FakeTurso()
        p1 = patch.object(ml_shadow.storage, "_execute", self.turso.execute)
        p2 = patch.object(ml_shadow.storage, "_rows_as_dicts", FakeTurso.rows)
        p3 = patch.object(ml_shadow.ml_model, "load", return_value=MODEL)
        p4 = patch.object(ml_shadow, "_notify")
        for p in (p1, p2, p3, p4):
            p.start()
            self.addCleanup(p.stop)
        ml_shadow.init_db()

    def test_monday_decision_then_resolution_matches_the_study_rule(self):
        days = weekday_days(date(2026, 10, 5), 12)          # starts Monday 2026-10-05
        ctx = fake_context(days)
        probs = iter([0.70, 0.30] + [0.50] * 20)
        with patch.object(ml_shadow, "load_context", return_value=ctx), \
                patch.object(ml_shadow, "feature_row", return_value={"x": 1}), \
                patch.object(ml_shadow.ml_model, "probability_up", side_effect=lambda m, f: next(probs)):
            first = ml_shadow.run(datetime(2026, 10, 6, 0, 45, tzinfo=timezone.utc))      # Tuesday
            again = ml_shadow.run(datetime(2026, 10, 6, 3, 0, tzinfo=timezone.utc))       # same day: no duplicates
        self.assertEqual(first["decision_day"], "2026-10-05")
        self.assertEqual([(m, s) for m, s, _ in first["decided"]], [(FX_ONLY[0].name, 1), (FX_ONLY[1].name, -1)])
        self.assertEqual(again["decided"], [])
        with patch.object(ml_shadow, "load_context", return_value=ctx), \
                patch.object(ml_shadow, "feature_row", return_value=None):              # next Monday: no new signals
            done = ml_shadow.run(datetime(2026, 10, 13, 0, 45, tzinfo=timezone.utc))      # 5th trading day closed
        nets = {m: n for m, _, n in done["resolved"]}
        long_net = (100.5 + 5) / (100.0 + 1) - 1                     # entry next open, exit 5th close
        self.assertAlmostEqual(nets[FX_ONLY[0].name], long_net)
        self.assertAlmostEqual(nets[FX_ONLY[1].name], -long_net)
        self.assertEqual(ml_shadow.stats()["closed"], 2)

    def test_no_decision_on_other_weekdays(self):
        days = weekday_days(date(2026, 10, 6), 8)            # starts Tuesday
        with patch.object(ml_shadow, "load_context", return_value=fake_context(days)), \
                patch.object(ml_shadow, "feature_row", return_value={"x": 1}), \
                patch.object(ml_shadow.ml_model, "probability_up", return_value=0.9):
            result = ml_shadow.run(datetime(2026, 10, 8, 0, 45, tzinfo=timezone.utc))   # last complete: Wednesday
        self.assertEqual(result["decided"], [])


if __name__ == "__main__":
    unittest.main()
