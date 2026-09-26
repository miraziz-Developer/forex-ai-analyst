"""Data sources, feasibility probing, and point-in-time loaders (Binance public archives).

Loaders download each archive once into a local cache (sha256 recorded),
convert rows into provenance-carrying RawRecords, and refuse to read the
holdout period until the registry says it was opened.
"""
from __future__ import annotations

import csv
import hashlib
import io
import os
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

from forex_ai_analyst.research.edge_lab.models import Availability, RawRecord

BASE = "https://data.binance.vision/data"
FIVE_MIN, FIFTEEN_MIN, HOUR = 300_000, 900_000, 3_600_000


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
    DataSource("perp_klines_15m", BASE + "/futures/um/monthly/klines/{symbol}/15m/{symbol}-15m-{period}.zip",
               "monthly", FIFTEEN_MIN, Availability.HISTORICAL_AND_LIVE, "open_time", "open_time + interval"),
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


# ---------- loaders (work package 2) ----------

KLINE_URLS = {
    "perp_klines_15m": BASE + "/futures/um/monthly/klines/{symbol}/15m/{symbol}-15m-{period}.zip",
    "spot_klines_15m": BASE + "/spot/monthly/klines/{symbol}/15m/{symbol}-15m-{period}.zip",
    "premium_index_15m": BASE + "/futures/um/monthly/premiumIndexKlines/{symbol}/15m/{symbol}-15m-{period}.zip",
}
FUNDING_URL = BASE + "/futures/um/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{period}.zip"
OI_URL = BASE + "/futures/um/daily/metrics/{symbol}/{symbol}-metrics-{period}.zip"


def cache_dir() -> Path:
    return Path(os.environ.get("EDGE_LAB_CACHE", ".edge_lab_cache"))


class HoldoutSealedError(RuntimeError):
    pass


def fetch(url: str, name: str, session=requests, retries: int = 3) -> Path | None:
    """Download once into the cache; None when the archive does not exist (404)."""
    path = cache_dir() / name
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(retries):
        try:
            response = session.get(url, timeout=120)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                if archive.testzip() is not None:
                    raise zipfile.BadZipFile("corrupt member")
            tmp = path.with_suffix(".part")
            tmp.write_bytes(response.content)
            tmp.replace(path)
            return path
        except (requests.RequestException, zipfile.BadZipFile):
            if attempt == retries - 1:
                raise
            time.sleep(2 * (attempt + 1))
    return None


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def csv_rows(path: Path):
    with zipfile.ZipFile(path) as archive:
        name = next(n for n in archive.namelist() if n.endswith(".csv"))
        for row in csv.reader(io.TextIOWrapper(archive.open(name), encoding="utf-8")):
            if row and row[0][:1].isdigit():   # skip header rows
                yield row


def _ms(stamp: int) -> int:
    # Binance spot archives switched to microsecond timestamps in 2025.
    return stamp // 1000 if stamp > 10**14 else stamp


def month_periods(start: str, end: str) -> list[str]:
    """Calendar months touched by [start, end)."""
    out, y, m = [], int(start[:4]), int(start[5:7])
    ey, em = int(end[:4]), int(end[5:7])
    last = (f"{ey - 1:04d}-12" if em == 1 else f"{ey:04d}-{em - 1:02d}") if end[8:10] == "01" else end[:7]
    while f"{y:04d}-{m:02d}" <= last:
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def day_periods(start: str, end: str) -> list[str]:
    day, stop = date.fromisoformat(start), date.fromisoformat(end)
    out = []
    while day < stop:
        out.append(day.isoformat())
        day += timedelta(days=1)
    return out


def _to_ms(day: str) -> int:
    return int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp() * 1000)


def data_end(manifest: dict, holdout_opened: bool) -> str:
    """Last usable date (exclusive): the holdout stays unreadable until opened."""
    return manifest["holdout_period"][1] if holdout_opened else manifest["holdout_period"][0]


def load_klines(source: str, pair: str, start: str, end: str, received_ms: int, session=requests) -> tuple[list[RawRecord], list[dict]]:
    symbol, market = symbol_for(pair), ("spot" if source.startswith("spot") else "perpetual")
    lo, hi, records, files = _to_ms(start), _to_ms(end), [], []
    for period in month_periods(start, end):
        path = fetch(KLINE_URLS[source].format(symbol=symbol, period=period), f"{source}/{symbol}/{period}.zip", session)
        if path is None:
            files.append({"period": period, "missing": True})
            continue
        files.append({"period": period, "sha256": file_sha256(path)})
        for row in csv_rows(path):
            open_ms = _ms(int(row[0]))
            if not lo <= open_ms < hi:
                continue
            if source == "premium_index_15m":
                values = {"premium": float(row[4])}
            else:
                values = {"open": float(row[1]), "high": float(row[2]), "low": float(row[3]), "close": float(row[4]),
                          "volume": float(row[5]), "quote_volume": float(row[7]), "taker_buy_quote": float(row[10])}
            records.append(RawRecord("binance", market, pair, open_ms, open_ms + FIFTEEN_MIN, received_ms, source, values))
    return records, files


def load_funding(pair: str, start: str, end: str, received_ms: int, session=requests) -> tuple[list[RawRecord], list[dict]]:
    symbol, lo, hi, records, files = symbol_for(pair), _to_ms(start), _to_ms(end), [], []
    for period in month_periods(start, end):
        path = fetch(FUNDING_URL.format(symbol=symbol, period=period), f"funding/{symbol}/{period}.zip", session)
        if path is None:
            files.append({"period": period, "missing": True})
            continue
        files.append({"period": period, "sha256": file_sha256(path)})
        for row in csv_rows(path):
            stamp = _ms(int(row[0]))
            if lo <= stamp < hi:
                records.append(RawRecord("binance", "perpetual", pair, stamp, stamp, received_ms, "funding",
                                         {"rate": float(row[-1])}))
    return records, files


def load_open_interest(pair: str, start: str, end: str, received_ms: int, session=requests,
                       workers: int = 16) -> tuple[list[RawRecord], list[dict]]:
    """5-minute OI metrics; each value becomes available one interval after create_time."""
    symbol, lo, hi = symbol_for(pair), _to_ms(start), _to_ms(end)
    days = day_periods(start, end)
    with ThreadPoolExecutor(workers) as pool:
        paths = list(pool.map(lambda d: fetch(OI_URL.format(symbol=symbol, period=d), f"oi/{symbol}/{d}.zip", session), days))
    records, files = [], []
    for day, path in zip(days, paths):
        if path is None:
            files.append({"period": day, "missing": True})
            continue
        files.append({"period": day, "sha256": file_sha256(path)})
        for row in csv_rows(path):
            stamp = int(datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp() * 1000)
            if lo <= stamp < hi and row[2] != "":
                records.append(RawRecord("binance", "perpetual", pair, stamp, stamp + FIVE_MIN, received_ms,
                                         "open_interest_5m", {"oi": float(row[2])}))
    return records, files
