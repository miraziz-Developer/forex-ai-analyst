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
HOUR_URL = "https://datafeed.dukascopy.com/datafeed/{pair}/{y}/{m:02d}/{side}_candles_hour_1.bi5"
DAY_URL = "https://datafeed.dukascopy.com/datafeed/{pair}/{y}/{side}_candles_day_1.bi5"
POINT = {"USDJPY": 1e-3, "EURJPY": 1e-3, "GBPJPY": 1e-3, "XAUUSD": 1e-3}
RECORD = struct.Struct(">5If")
PAUSE = 0.5                             # seconds between requests: bursts get the IP blocked
_SESSION = requests.Session()          # keep-alive: a new TLS connection costs ~20 s, a reused one ~0.3 s
_SESSION.headers["User-Agent"] = "Mozilla/5.0"


def _name(day: date, side: str, kind) -> str:
    if kind == "day":
        return f"{day.year}-{side}-D1.bi5"
    return f"{day.isoformat()[:7]}-{side}-H1.bi5" if kind else f"{day.isoformat()}-{side}.bi5"


def _raw(pair: str, day: date, side: str, hourly=False) -> bytes:
    """Minute file of `day`; hourly=True: the hour file of day's month; hourly="day": the day file of day's year."""
    name = _name(day, side, hourly)
    path = CACHE_DIR / "dukascopy" / pair / name
    if path.exists():
        return path.read_bytes()
    if path.with_suffix(".missing").exists():               # prefetch gave up on it: treat as no data
        return b""
    url = (DAY_URL.format(pair=pair, y=day.year, side=side) if hourly == "day"
           else HOUR_URL.format(pair=pair, y=day.year, m=day.month - 1, side=side) if hourly
           else URL.format(pair=pair, y=day.year, m=day.month - 1, d=day.day, side=side))
    for attempt in range(6):
        time.sleep(PAUSE)
        try:
            response = _SESSION.get(url, timeout=60)
        except requests.RequestException:
            time.sleep(30 * (attempt + 1))         # the feed blocks bursts for a while; back off
            continue
        if response.status_code == 200:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(response.content)
            return response.content
        if response.status_code == 404:
            return b""
        time.sleep(10 * (attempt + 1))
    raise RuntimeError(f"Dukascopy unavailable for {pair} {day} {side}")


def prefetch(jobs: list[tuple[str, date]], log=print, hourly: bool = False) -> int:
    """Download every (pair, day) bid and ask file not cached yet. A failing file goes to the back of the
    queue (others continue) and is skipped after three failures; several failures in a row mean the feed is
    blocking us, so wait five minutes. With hourly=True each job's day stands for its month's hour file."""
    from collections import deque

    def cached(p, d, s):
        return (CACHE_DIR / "dukascopy" / p / _name(d, s, hourly)).exists()
    queue = deque((p, d, s) for p, d in jobs for s in ("BID", "ASK") if not cached(p, d, s))
    done, fails, in_a_row = 0, {}, 0
    while queue:
        item = queue.popleft()
        try:
            _raw(item[0], item[1], item[2], hourly)
        except RuntimeError:
            fails[item] = fails.get(item, 0) + 1
            in_a_row += 1
            if fails[item] < 3:
                queue.append(item)
            else:
                log(f"skipping {item[0]} {item[1]} {item[2]}: not served after 3 tries")
                marker = (CACHE_DIR / "dukascopy" / item[0] / _name(item[1], item[2], hourly)).with_suffix(".missing")
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.touch()
            if in_a_row >= 3:
                log(f"feed is blocking; waiting 5 minutes ({len(queue)} files left)")
                time.sleep(300)
                in_a_row = 0
            continue
        in_a_row = 0
        done += 1
        if done % 100 == 0:
            log(f"{done} downloaded, {len(queue)} left")
    return done


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


def hours(pair: str, year: int, month: int) -> list[dict]:
    """Hourly candles of one month with bid and ask OHLC, UTC; empty if the month was never served."""
    point, first = POINT.get(pair, 1e-5), date(year, month, 1)
    try:
        bid = decode(_raw(pair, first, "BID", hourly=True), first, point)
        ask = {r[0]: r for r in decode(_raw(pair, first, "ASK", hourly=True), first, point)}
    except RuntimeError:
        return []
    return [{"datetime": b[0], "bid_open": b[1], "bid_high": b[2], "bid_low": b[3], "bid_close": b[4],
             "ask_open": ask[b[0]][1], "ask_high": ask[b[0]][2], "ask_low": ask[b[0]][3], "ask_close": ask[b[0]][4]}
            for b in bid if b[0] in ask]


def days(pair: str, first_year: int, last_year: int) -> list[dict]:
    """Daily bars (UTC days, Dukascopy's own day candles) with mid OHLC, the format of data.load_yahoo.
    Weekend placeholder candles (no range) are dropped."""
    point, out = POINT.get(pair, 1e-5), []
    for year in range(first_year, last_year + 1):
        first = date(year, 1, 1)
        bid = decode(_raw(pair, first, "BID", hourly="day"), first, point)
        ask = {r[0]: r for r in decode(_raw(pair, first, "ASK", hourly="day"), first, point)}
        for b in bid:
            a = ask.get(b[0])
            if a is None or b[2] <= b[3]:
                continue
            out.append({"datetime": b[0], "open": (b[1] + a[1]) / 2, "high": (b[2] + a[2]) / 2,
                        "low": (b[3] + a[3]) / 2, "close": (b[4] + a[4]) / 2, "volume": 0,
                        "spread": (a[4] - b[4])})
    return out


def minutes(pair: str, day: date) -> list[dict]:
    """One-minute candles with bid and ask, UTC. Empty on days without trading."""
    point = POINT.get(pair, 1e-5)
    bid = decode(_raw(pair, day, "BID"), day, point)
    ask = {r[0]: r for r in decode(_raw(pair, day, "ASK"), day, point)}
    return [{"datetime": b[0], "bid_open": b[1], "bid_high": b[2], "bid_low": b[3], "bid_close": b[4],
             "ask_open": ask[b[0]][1], "ask_high": ask[b[0]][2], "ask_low": ask[b[0]][3], "ask_close": ask[b[0]][4]}
            for b in bid if b[0] in ask]


def cache_size() -> int:
    folder = CACHE_DIR / "dukascopy"
    return sum(p.stat().st_size for p in folder.rglob("*.bi5")) if folder.exists() else 0

