"""Dukascopy Bank's free historical datafeed: one-minute bid and ask candles since 2003.

Files: https://datafeed.dukascopy.com/datafeed/{PAIR}/{YYYY}/{MM-1:02}/{DD:02}/{BID|ASK}_candles_min_1.bi5,
LZMA-compressed records of 24 bytes (seconds from midnight UTC, open, close, low, high as integers
in points, volume as float). Bid and ask give the real spread minute by minute. Raw files are cached
forever (history does not change) under .forex_cache/dukascopy.
"""
from __future__ import annotations

import lzma
import struct
import time
from datetime import date, datetime, timezone

import requests

from forex_ai_analyst.forex.data import CACHE_DIR

URL = "https://datafeed.dukascopy.com/datafeed/{pair}/{y}/{m:02d}/{d:02d}/{side}_candles_min_1.bi5"
POINT = {"USDJPY": 1e-3, "EURJPY": 1e-3, "GBPJPY": 1e-3, "XAUUSD": 1e-3}
RECORD = struct.Struct(">5If")
_SESSION = requests.Session()          # keep-alive: a new TLS connection costs ~20 s, a reused one ~0.3 s
_SESSION.headers["User-Agent"] = "Mozilla/5.0"


def _raw(pair: str, day: date, side: str) -> bytes:
    path = CACHE_DIR / "dukascopy" / pair / f"{day.isoformat()}-{side}.bi5"
    if path.exists():
        return path.read_bytes()
    url = URL.format(pair=pair, y=day.year, m=day.month - 1, d=day.day, side=side)
    for attempt in range(6):
        response = _SESSION.get(url, timeout=60)
        if response.status_code == 200:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(response.content)
            return response.content
        if response.status_code == 404:
            return b""
        time.sleep(2 * (attempt + 1))
    response.raise_for_status()
    raise RuntimeError(f"Dukascopy unavailable for {pair} {day}")


def decode(blob: bytes, day: date, point: float) -> list[tuple]:
    """[(ms, open, high, low, close)] for one side of one day."""
    if not blob:
        return []
    raw = lzma.decompress(blob)
    midnight = int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() * 1000)
    out = []
    for k in range(len(raw) // RECORD.size):
        sec, o, c, lo, hi, _vol = RECORD.unpack_from(raw, k * RECORD.size)
        out.append((midnight + sec * 1000, o * point, hi * point, lo * point, c * point))
    return out


def minutes(pair: str, day: date) -> list[dict]:
    """One-minute candles with bid and ask, UTC. Empty on days without trading."""
    point = POINT.get(pair, 1e-5)
    bid = decode(_raw(pair, day, "BID"), day, point)
    ask = {r[0]: r for r in decode(_raw(pair, day, "ASK"), day, point)}
    return [{"datetime": b[0], "bid_open": b[1], "bid_high": b[2], "bid_low": b[3], "bid_close": b[4],
             "ask_open": ask[b[0]][1], "ask_close": ask[b[0]][4]} for b in bid if b[0] in ask]


def cache_size() -> int:
    folder = CACHE_DIR / "dukascopy"
    return sum(p.stat().st_size for p in folder.rglob("*.bi5")) if folder.exists() else 0

