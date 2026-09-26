import re
import unittest
from pathlib import Path
from unittest.mock import Mock

from forex_ai_analyst.research.edge_lab import data
from forex_ai_analyst.research.edge_lab.models import MarketEvent, RawRecord, content_hash, point_in_time
from forex_ai_analyst.research.edge_lab.quality import check_series, normalize

FIVE = 300_000


def rec(t, available=None, **values):
    values = values or {"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5}
    return RawRecord("binance", "perpetual", "BTC-USDT", t, t + FIVE if available is None else available, t + FIVE,
                     "test", values)


class RecordTests(unittest.TestCase):
    def test_contract_rejects_availability_before_event_and_unknown_market(self):
        self.assertIsNone(rec(0).validation_error())
        self.assertIn("precedes", rec(10, available=5).validation_error())
        bad = RawRecord("binance", "options", "BTC-USDT", 0, 0, 0, "test", {})
        self.assertIn("unknown market", bad.validation_error())

    def test_point_in_time_hides_records_not_yet_available(self):
        records = [rec(0), rec(FIVE), rec(2 * FIVE)]
        self.assertEqual([r.event_time_ms for r in point_in_time(records, 2 * FIVE)], [0, FIVE])

    def test_event_fingerprint_is_deterministic_and_version_sensitive(self):
        kwargs = dict(event_id="e", hypothesis_id="h_v1", pair="BTC-USDT", direction="SHORT", event_time_ms=1,
                      decision_time_ms=2, reference_price=1.0, feature_version="v1", features={}, data_quality={})
        a, b = MarketEvent(**kwargs), MarketEvent(**{**kwargs, "features": {"x": 1}})
        self.assertEqual(a.fingerprint, b.fingerprint)
        self.assertNotEqual(a.fingerprint, MarketEvent(**{**kwargs, "feature_version": "v2"}).fingerprint)
        self.assertEqual(content_hash({"b": 1, "a": 2}), content_hash({"a": 2, "b": 1}))


class QualityTests(unittest.TestCase):
    def test_identical_duplicates_collapse_and_conflicts_are_dropped_not_guessed(self):
        records = [rec(0), rec(0), rec(FIVE), rec(FIVE, open=9.0, high=9.0, low=9.0, close=9.0)]
        kept, identical, conflicting = normalize(records)
        self.assertEqual([r.event_time_ms for r in kept], [0])
        self.assertEqual((identical, conflicting), (1, [FIVE]))

    def test_input_order_does_not_change_normalized_output(self):
        records = [rec(2 * FIVE), rec(0), rec(FIVE)]
        self.assertEqual(normalize(records)[0], normalize(list(reversed(records)))[0])

    def test_report_counts_gaps_disorder_future_negative_ohlc_and_missing(self):
        records = [rec(FIVE), rec(0), rec(4 * FIVE), rec(10**15), rec(5 * FIVE, oi=-1.0, open=None),
                   rec(6 * FIVE, open=5.0, high=4.0, low=1.0, close=2.0)]
        report = check_series(records, dataset="t", expected_interval_ms=FIVE, now_ms=10**13, ohlc=True,
                              non_negative=("oi",))
        self.assertEqual(report.out_of_order, 2)  # FIVE->0 and the future record -> 5*FIVE
        self.assertEqual(report.future_records, 1)
        self.assertEqual(report.negative_values, 1)
        self.assertEqual(report.missing_values, 1)
        self.assertGreaterEqual(report.ohlc_inconsistent, 2)
        self.assertGreaterEqual(report.gaps, 1)
        self.assertFalse(report.clean)

    def test_missing_values_stay_none(self):
        record = rec(0, oi=None)
        normalize([record])
        self.assertIsNone(record.values["oi"])

    def test_clean_series_with_full_coverage(self):
        records = [rec(i * FIVE) for i in range(12)]
        report = check_series(records, dataset="t", expected_interval_ms=FIVE, now_ms=10**13, start_ms=0,
                              end_ms=12 * FIVE, ohlc=True)
        self.assertEqual(report.coverage_pct, 100.0)
        self.assertTrue(report.clean, report.issues)


class FeasibilityTests(unittest.TestCase):
    def test_edge_periods_cover_the_last_complete_month_or_day(self):
        monthly = next(s for s in data.SOURCES if s.name == "funding")
        daily = next(s for s in data.SOURCES if s.name == "open_interest_5m")
        self.assertEqual(data.edge_periods(monthly, "2021-01-01", "2026-09-01"), ("2021-01", "2026-08"))
        self.assertEqual(data.edge_periods(monthly, "2021-01-01", "2026-01-01"), ("2021-01", "2025-12"))
        self.assertEqual(data.edge_periods(daily, "2021-01-01", "2026-09-01"), ("2021-01-01", "2026-08-31"))

    def test_feasibility_marks_missing_archives_without_network(self):
        session = Mock()
        session.head.side_effect = lambda url, **_: Mock(status_code=404 if "liquidation" in url else 200)
        report = data.feasibility(["BTC-USDT"], "2021-01-01", "2026-09-01", session=session)
        self.assertEqual(report["summary"]["liquidations"]["pairs_available"], 0)
        self.assertEqual(report["summary"]["open_interest_5m"]["pairs_available"], 1)


class IsolationTests(unittest.TestCase):
    FORBIDDEN = ("bingx_broker", "interfaces", "scheduler", "signal_repository", "shared.turso", "shared.llm",
                 "notifier", "runtime_controls", "trend_engine", "ai_trader")

    def test_edge_lab_never_imports_live_execution_modules(self):
        root = Path(__file__).resolve().parents[1] / "src" / "forex_ai_analyst" / "research" / "edge_lab"
        for path in root.rglob("*.py"):
            for line in path.read_text().splitlines():
                if re.match(r"\s*(from|import)\s", line):
                    for name in self.FORBIDDEN:
                        self.assertNotIn(name, line, f"{path.name}: {line.strip()}")


if __name__ == "__main__":
    unittest.main()
