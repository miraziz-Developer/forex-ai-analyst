"""Provider abstraction that returns validated, de-duplicated closed candles."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import os

import requests

_SECONDS = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
_BINANCE_FUTURES_KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"


def binance_futures_symbol(pair: str) -> str:
    """Convert the project's BTC-USDT pair convention to Binance's BTCUSDT."""
    symbol = pair.upper().replace("-", "")
    if not symbol.endswith("USDT") or not symbol[:-4].isalnum():
        raise ValueError(f"unsupported Binance Futures pair: {pair}")
    return symbol


def fetch_binance_futures_bars(pair: str, timeframe: str, outputsize: int = 300) -> list[dict]:
    """Fetch public Binance USD-M Futures klines; no account or API key is used."""
    if timeframe not in _SECONDS:
        raise ValueError(f"unsupported Binance Futures timeframe: {timeframe}")
    response = requests.get(_BINANCE_FUTURES_KLINES_URL, params={
        "symbol": binance_futures_symbol(pair), "interval": timeframe,
        "limit": max(1, min(int(outputsize), 1500)),
    }, timeout=15)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise ValueError("unexpected Binance Futures kline response")
    bars = []
    for row in payload:
        if not isinstance(row, list) or len(row) < 6:
            continue
        bars.append({"datetime": row[0], "open": row[1], "high": row[2], "low": row[3],
                     "close": row[4], "volume": row[5]})
    return bars


def closed_bars(bars: list[dict], timeframe: str, now: datetime | None = None) -> list[dict]:
    if timeframe not in _SECONDS:
        raise ValueError(f"unsupported timeframe: {timeframe}")
    now_ms = int((now or datetime.now(timezone.utc)).timestamp() * 1000)
    interval_ms = _SECONDS[timeframe] * 1000
    seen, result = set(), []
    for bar in sorted(bars, key=lambda item: int(item["datetime"])):
        try:
            stamp = int(bar["datetime"])
            normalized = {key: (stamp if key == "datetime" else float(bar[key]))
                          for key in ("datetime", "open", "high", "low", "close", "volume")}
        except (KeyError, TypeError, ValueError):
            continue
        if stamp in seen or stamp + interval_ms > now_ms or normalized["volume"] < 0:
            continue
        if normalized["low"] > min(normalized["open"], normalized["close"]) or \
                normalized["high"] < max(normalized["open"], normalized["close"]):
            continue
        seen.add(stamp)
        result.append(normalized)
    return result


@dataclass
class MarketDataProvider:
    fetcher: callable
    cache: dict[tuple[str, str], list[dict]] = field(default_factory=dict)

    def fetch_closed_bars(self, pair: str, timeframe: str, limit: int, now: datetime | None = None) -> list[dict]:
        raw = self.fetcher(pair, timeframe, outputsize=limit + 1)
        bars = closed_bars(raw, timeframe, now)
        self.cache[(pair, timeframe)] = bars[-limit:]
        return self.cache[(pair, timeframe)]


def provider_from_environment() -> MarketDataProvider:
    """Choose an explicit provider so the multi-strategy service never silently uses BingX data."""
    provider = os.environ.get("MULTI_STRATEGY_PROVIDER", "").strip().lower()
    if provider == "binance_futures":
        return MarketDataProvider(fetcher=fetch_binance_futures_bars)
    raise ValueError("MULTI_STRATEGY_PROVIDER must be binance_futures")