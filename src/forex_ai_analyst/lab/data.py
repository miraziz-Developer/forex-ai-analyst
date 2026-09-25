"""Public historical data for research: Binance USD-M futures archives (no API key).

Live execution is on BingX, whose perpetual prices track Binance closely; the
archive is used because it offers complete multi-year 1h candles and the
actual funding-rate history needed for realistic cost accounting.
"""
from __future__ import annotations

import csv
import io
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import requests

KLINES_URL = ("https://data.binance.vision/data/futures/um/monthly/klines/"
              "{symbol}/{interval}/{symbol}-{interval}-{month}.zip")
FUNDING_URL = ("https://data.binance.vision/data/futures/um/monthly/fundingRate/"
               "{symbol}/{symbol}-fundingRate-{month}.zip")
CACHE_DIR = Path(".lab_cache")
HOUR_MS = 3_600_000


def symbol_for(pair: str) -> str:
    return pair.upper().replace("-", "")


def months(start: datetime, end: datetime) -> list[str]:
    """Calendar months touched by [start, end)."""
    result, year, month = [], start.year, start.month
    while (year, month) < (end.year, end.month) or ((year, month) == (end.year, end.month) and end.day > 1):
        result.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return result


def _cached(url: str, name: str) -> Path | None:
    CACHE_DIR.mkdir(exist_ok=True)
    path = CACHE_DIR / name
    if not path.exists():
        response = requests.get(url, timeout=120)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        path.write_bytes(response.content)
    return path


def _rows(path: Path):
    with zipfile.ZipFile(path) as archive:
        name = next(item for item in archive.namelist() if item.endswith(".csv"))
        for row in csv.reader(io.TextIOWrapper(archive.open(name), encoding="utf-8")):
            if row and row[0].isdigit():  # newer files carry a header row
                yield row


def load_klines(pair: str, start: datetime, end: datetime, interval: str = "1h") -> list[dict]:
    """Chronological closed candles in [start, end) with a continuity check."""
    symbol, start_ms, end_ms, bars = symbol_for(pair), _ms(start), _ms(end), {}
    for month in months(start, end):
        path = _cached(KLINES_URL.format(symbol=symbol, interval=interval, month=month),
                       f"{symbol}-{interval}-{month}.zip")
        if path is None:
            raise RuntimeError(f"archive missing: {symbol} {interval} {month}")
        for row in _rows(path):
            stamp = int(row[0])
            if start_ms <= stamp < end_ms:
                bars[stamp] = {"datetime": stamp, "open": float(row[1]), "high": float(row[2]),
                               "low": float(row[3]), "close": float(row[4]), "volume": float(row[5])}
    result = [bars[key] for key in sorted(bars)]
    if interval == "1h":
        gaps = sum(1 for a, b in zip(result, result[1:]) if b["datetime"] - a["datetime"] != HOUR_MS)
        if gaps > 5:
            raise RuntimeError(f"{symbol} {interval} has {gaps} gaps; refusing to backtest on it")
    return result


def load_funding(pair: str, start: datetime, end: datetime) -> list[tuple[int, float]]:
    """(funding_time_ms, rate) events; a positive rate means longs pay shorts."""
    symbol, start_ms, end_ms, events = symbol_for(pair), _ms(start), _ms(end), {}
    for month in months(start, end):
        path = _cached(FUNDING_URL.format(symbol=symbol, month=month), f"{symbol}-funding-{month}.zip")
        if path is None:
            continue
        for row in _rows(path):
            stamp = int(row[0])
            if start_ms <= stamp < end_ms:
                events[stamp] = float(row[-1])
    return sorted(events.items())


def resample(bars: list[dict], hours: int) -> list[dict]:
    """Aggregate 1h candles into complete `hours` candles aligned to UTC."""
    width, groups = hours * HOUR_MS, {}
    for bar in bars:
        groups.setdefault(bar["datetime"] // width * width, []).append(bar)
    result = []
    for stamp in sorted(groups):
        group = groups[stamp]
        if len(group) != hours:
            continue
        result.append({"datetime": stamp, "open": group[0]["open"], "high": max(b["high"] for b in group),
                       "low": min(b["low"] for b in group), "close": group[-1]["close"],
                       "volume": sum(b["volume"] for b in group)})
    return result


def _ms(moment: datetime) -> int:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.timestamp() * 1000)
