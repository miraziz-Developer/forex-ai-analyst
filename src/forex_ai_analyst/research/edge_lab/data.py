"""Data-source catalog and Phase 0 feasibility probing (Binance public archives).

Loaders and the immutable cache are work package 2; this module only records
which datasets exist, their cadence, and when each record becomes knowable.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta

import requests

from forex_ai_analyst.research.edge_lab.models import Availability

BASE = "https://data.binance.vision/data"
FIVE_MIN, HOUR = 300_000, 3_600_000


@dataclass(frozen=True)
class DataSource:
    name: str
    url: str                 # {symbol} {period}; period is YYYY-MM (monthly) or YYYY-MM-DD (daily)
    cadence: str             # "monthly" | "daily"
    interval_ms: int
    availability: Availability
    event_time: str
    available_time_rule: str


SOURCES = [
    DataSource("perp_klines_5m", BASE + "/futures/um/monthly/klines/{symbol}/5m/{symbol}-5m-{period}.zip",
               "monthly", FIVE_MIN, Availability.HISTORICAL_AND_LIVE, "open_time", "open_time + interval"),
    DataSource("spot_klines_5m", BASE + "/spot/monthly/klines/{symbol}/5m/{symbol}-5m-{period}.zip",
               "monthly", FIVE_MIN, Availability.HISTORICAL_AND_LIVE, "open_time", "open_time + interval"),
    DataSource("premium_index_5m", BASE + "/futures/um/monthly/premiumIndexKlines/{symbol}/5m/{symbol}-5m-{period}.zip",
               "monthly", FIVE_MIN, Availability.HISTORICAL_AND_LIVE, "open_time", "open_time + interval"),
    DataSource("mark_price_1h", BASE + "/futures/um/monthly/markPriceKlines/{symbol}/1h/{symbol}-1h-{period}.zip",
               "monthly", HOUR, Availability.HISTORICAL_AND_LIVE, "open_time", "open_time + interval"),
    DataSource("index_price_1h", BASE + "/futures/um/monthly/indexPriceKlines/{symbol}/1h/{symbol}-1h-{period}.zip",
               "monthly", HOUR, Availability.HISTORICAL_AND_LIVE, "open_time", "open_time + interval"),
    DataSource("funding", BASE + "/futures/um/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{period}.zip",
               "monthly", 8 * HOUR, Availability.HISTORICAL_AND_LIVE, "calc_time", "calc_time"),
    DataSource("open_interest_5m", BASE + "/futures/um/daily/metrics/{symbol}/{symbol}-metrics-{period}.zip",
               "daily", FIVE_MIN, Availability.HISTORICAL_AND_LIVE, "create_time", "create_time + 5m"),
    DataSource("liquidations", BASE + "/futures/um/daily/liquidationSnapshot/{symbol}/{symbol}-liquidationSnapshot-{period}.zip",
               "daily", 0, Availability.UNAVAILABLE, "time", "not collected historically"),
]


def symbol_for(pair: str) -> str:
    return pair.upper().replace("-", "")


def edge_periods(source: DataSource, start: str, end: str) -> tuple[str, str]:
    """First and last archive period inside [start, end) (dates as YYYY-MM-DD)."""
    if source.cadence == "daily":
        return start, (date.fromisoformat(end) - timedelta(days=1)).isoformat()
    if end[8:10] != "01":
        return start[:7], end[:7]
    y, m = int(end[:4]), int(end[5:7])
    return start[:7], (f"{y - 1:04d}-12" if m == 1 else f"{y:04d}-{m - 1:02d}")


def probe(url: str, session=requests) -> int:
    """HTTP status of an archive URL (HEAD; no download). 0 means unreachable."""
    try:
        return session.head(url, timeout=30, allow_redirects=True).status_code
    except requests.RequestException:
        return 0


def feasibility(pairs: list[str], start: str, end: str, session=requests) -> dict:
    """Probe the first and last archive of every source for every pair."""
    results = []
    for source in SOURCES:
        first, last = edge_periods(source, start, end)
        for pair in pairs:
            symbol = symbol_for(pair)
            statuses = {p: probe(source.url.format(symbol=symbol, period=p), session) for p in (first, last)}
            results.append({"source": source.name, "pair": pair, "first_period": first, "last_period": last,
                            "first_status": statuses[first], "last_status": statuses[last],
                            "available": all(s == 200 for s in statuses.values())})
    summary = {}
    for source in SOURCES:
        rows = [r for r in results if r["source"] == source.name]
        summary[source.name] = {"declared": str(source.availability),
                                "pairs_available": sum(r["available"] for r in rows), "pairs_probed": len(rows)}
    return {"period": [start, end],
            "sources": [asdict(s) | {"availability": str(s.availability)} for s in SOURCES],
            "summary": summary, "probes": results}
