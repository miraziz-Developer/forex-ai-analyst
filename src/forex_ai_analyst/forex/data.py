"""Historical bars for forex research: Yahoo Finance (development), MetaTrader 5 or CSV (on the VM).

Bars are dicts {"datetime": ms, "open", "high", "low", "close", "volume"} in UTC,
oldest first, matching `forex_ai_analyst.lab.engine`.
"""
from __future__ import annotations

import csv
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests

CACHE_DIR = Path(".forex_cache")
_YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"


@dataclass(frozen=True)
class Market:
    name: str            # MT5-style name
    yahoo: str           # Yahoo Finance symbol used for research data
    round_trip: float    # spread + commission for one round trip, in price units (or fraction if pct=True)
    pct: bool = False    # round_trip given as a fraction of price (crypto)
    point: float = 0.00001  # MT5 point for the "+N points" offsets in the original bots


# Conservative retail ECN costs (spread + commission, round trip). Swaps are not modelled.
MARKETS = {m.name: m for m in (
    Market("EURUSD", "EURUSD=X", 0.00012),
    Market("GBPUSD", "GBPUSD=X", 0.00016),
    Market("AUDUSD", "AUDUSD=X", 0.00014),
    Market("NZDUSD", "NZDUSD=X", 0.00020),
    Market("USDCAD", "USDCAD=X", 0.00018),
    Market("USDCHF", "USDCHF=X", 0.00018),
    Market("USDJPY", "USDJPY=X", 0.014, point=0.001),
    Market("EURJPY", "EURJPY=X", 0.022, point=0.001),
    Market("GBPJPY", "GBPJPY=X", 0.030, point=0.001),
    Market("XAUUSD", "GC=F", 0.40, point=0.01),          # gold futures as a proxy for spot gold
    Market("BTCUSD", "BTC-USD", 0.0010, pct=True, point=0.01),
)}


def cost_fraction(market: Market, price: float) -> float:
    """Round-trip cost as a fraction of price."""
    return market.round_trip if market.pct else market.round_trip / price


def _get(url: str, params: dict) -> dict:
    for attempt in range(4):
        try:
            response = requests.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
            if response.status_code == 200:
                return response.json()
        except requests.RequestException:
            pass
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Yahoo request failed for {url}")


def load_yahoo(market: Market, interval: str) -> list[dict]:
    """interval '1h' (about 730 days) or '1d' (2004 onwards). Cached per day."""
    CACHE_DIR.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).date().isoformat()
    path = CACHE_DIR / f"{market.yahoo.replace('=', '_')}-{interval}-v2-{stamp}.json"
    if path.exists():
        return json.loads(path.read_text())
    params = {"interval": interval}
    if interval == "1d":
        params.update(period1=1072915200, period2=int(time.time()))   # 2004-01-01 .. now
    else:
        params["range"] = "730d"
    payload = _get(_YAHOO_URL.format(symbol=market.yahoo), params)
    result = payload["chart"]["result"][0]
    quote = result["indicators"]["quote"][0]
    bars = []
    for i, ts in enumerate(result.get("timestamp") or []):
        o, h, l, c = (quote[k][i] for k in ("open", "high", "low", "close"))
        if None in (o, h, l, c) or not (l <= min(o, c) <= max(o, c) <= h):
            continue
        bars.append({"datetime": ts * 1000, "open": o, "high": h, "low": l, "close": c,
                     "volume": quote.get("volume", [0] * len(result["timestamp"]))[i] or 0})
    if interval == "1d":
        # Yahoo stamps a daily FX bar at London midnight, i.e. 23:00 UTC of the previous day in summer.
        # Shift by 3 hours before taking the UTC date so every bar carries its own trading day
        # (index/metal/crypto stamps at 00:00-13:30 UTC are unaffected).
        for bar in bars:
            bar["datetime"] = (bar["datetime"] + 3 * 3_600_000) // 86_400_000 * 86_400_000
    bars.sort(key=lambda b: b["datetime"])
    path.write_text(json.dumps(bars))
    return bars


def load_mt5(symbol: str, timeframe: str, start: datetime, end: datetime) -> list[dict]:
    """Bars from a running MetaTrader 5 terminal (Windows VM). timeframe: 'M15', 'H1', 'H4', 'D1'."""
    import MetaTrader5 as mt5   # only available on Windows with MT5 installed

    if not mt5.initialize():
        raise RuntimeError(f"MetaTrader 5 initialize failed: {mt5.last_error()}")
    rates = mt5.copy_rates_range(symbol, getattr(mt5, f"TIMEFRAME_{timeframe}"), start, end)
    if rates is None:
        raise RuntimeError(f"no MT5 rates for {symbol} {timeframe}: {mt5.last_error()}")
    return [{"datetime": int(r["time"]) * 1000, "open": float(r["open"]), "high": float(r["high"]),
             "low": float(r["low"]), "close": float(r["close"]), "volume": float(r["tick_volume"])} for r in rates]


def load_csv(path: str | Path) -> list[dict]:
    """CSV with columns datetime (ISO or epoch seconds), open, high, low, close[, volume]."""
    bars = []
    with open(path, newline="") as handle:
        for row in csv.DictReader(handle):
            raw = row["datetime"].strip()
            moment = (datetime.fromtimestamp(float(raw), timezone.utc) if raw.replace(".", "").isdigit()
                      else datetime.fromisoformat(raw).replace(tzinfo=timezone.utc))
            bars.append({"datetime": int(moment.timestamp() * 1000), "open": float(row["open"]),
                         "high": float(row["high"]), "low": float(row["low"]), "close": float(row["close"]),
                         "volume": float(row.get("volume") or 0)})
    return sorted(bars, key=lambda b: b["datetime"])
