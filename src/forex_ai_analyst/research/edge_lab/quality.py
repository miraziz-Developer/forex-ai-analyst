"""Data-quality checks. Nothing here fills, interpolates or zeroes missing data."""
from __future__ import annotations

from dataclasses import dataclass, field

from forex_ai_analyst.research.edge_lab.models import SCHEMA_VERSION, RawRecord, canonical_json


@dataclass
class QualityReport:
    dataset: str
    rows: int
    duplicates_identical: int = 0
    duplicates_conflicting: int = 0
    out_of_order: int = 0
    future_records: int = 0
    gaps: int = 0
    largest_gap_ms: int = 0
    missing_values: int = 0
    negative_values: int = 0
    ohlc_inconsistent: int = 0
    schema_mismatch: int = 0
    invalid_records: int = 0
    coverage_pct: float | None = None
    first_event_ms: int | None = None
    last_event_ms: int | None = None
    issues: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.issues


def normalize(records: list[RawRecord]) -> tuple[list[RawRecord], int, list[int]]:
    """Sort by event time and resolve duplicates without guessing.

    Identical duplicates collapse to one. Timestamps whose duplicates disagree are
    dropped entirely (returned for reporting): we never pick a value.
    """
    by_time: dict[int, list[RawRecord]] = {}
    for record in records:
        by_time.setdefault(record.event_time_ms, []).append(record)
    kept, identical, conflicting = [], 0, []
    for stamp in sorted(by_time):
        group = by_time[stamp]
        if len({canonical_json(r.values) for r in group}) > 1:
            conflicting.append(stamp)
            continue
        identical += len(group) - 1
        kept.append(group[0])
    return kept, identical, conflicting


def check_series(records: list[RawRecord], *, dataset: str, expected_interval_ms: int, now_ms: int,
                 start_ms: int | None = None, end_ms: int | None = None, ohlc: bool = False,
                 non_negative: tuple[str, ...] = ()) -> QualityReport:
    report = QualityReport(dataset=dataset, rows=len(records))
    for previous, current in zip(records, records[1:]):
        if current.event_time_ms < previous.event_time_ms:
            report.out_of_order += 1
    for record in records:
        if record.validation_error():
            report.invalid_records += 1
        if record.schema_version != SCHEMA_VERSION:
            report.schema_mismatch += 1
        if record.event_time_ms > now_ms or record.available_time_ms > now_ms:
            report.future_records += 1
        report.missing_values += sum(1 for v in record.values.values() if v is None)
        report.negative_values += sum(1 for k in non_negative
                                      if isinstance(record.values.get(k), (int, float)) and record.values[k] < 0)
        if ohlc and _ohlc_inconsistent(record.values):
            report.ohlc_inconsistent += 1

    unique, identical, conflicting = normalize(records)
    report.duplicates_identical, report.duplicates_conflicting = identical, len(conflicting)
    stamps = [r.event_time_ms for r in unique]
    if stamps:
        report.first_event_ms, report.last_event_ms = stamps[0], stamps[-1]
    for a, b in zip(stamps, stamps[1:]):
        if b - a > expected_interval_ms:
            report.gaps += 1
            report.largest_gap_ms = max(report.largest_gap_ms, b - a)
    if start_ms is not None and end_ms is not None and end_ms > start_ms:
        expected = (end_ms - start_ms) // expected_interval_ms
        inside = sum(1 for s in stamps if start_ms <= s < end_ms)
        report.coverage_pct = round(100 * inside / expected, 3) if expected else None

    for name in ("duplicates_identical", "duplicates_conflicting", "out_of_order", "future_records", "gaps",
                 "missing_values", "negative_values", "ohlc_inconsistent", "schema_mismatch", "invalid_records"):
        if getattr(report, name):
            report.issues.append(f"{name}={getattr(report, name)}")
    if report.coverage_pct is not None and report.coverage_pct < 99.0:
        report.issues.append(f"coverage_pct={report.coverage_pct}")
    return report


def _ohlc_inconsistent(values: dict) -> bool:
    try:
        o, h, l, c = (float(values[k]) for k in ("open", "high", "low", "close"))
    except (KeyError, TypeError, ValueError):
        return True
    return l > min(o, c) or h < max(o, c) or l > h
