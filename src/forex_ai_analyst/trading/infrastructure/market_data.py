"""Provider abstraction that returns validated, de-duplicated closed candles."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import os

import requests

_SECONDS = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
_BINANCE_FUTURES_KLINES_URLS = (
    "https://fapi.binance.com/fapi/v1/klines",
    "https://fapi1.binance.com/fapi/v1/klines",
    "https://fapi2.binance.com/fapi/v1/klines",
    "https://fapi3.binance.com/fapi/v1/klines",
)
_BINGX_SWAP_KLINES_URL = "https://open-api.bingx.com/openApi/swap/v2/quote/klines"


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
    params = {"symbol": binance_futures_symbol(pair), "interval": timeframe,
              "limit": max(1, min(int(outputsize), 1500))}
    errors = []
    for url in _BINANCE_FUTURES_KLINES_URLS:
        try:
            response = requests.get(url, params=params, timeout=15)
            response.raise_for_status()
            break
        except requests.RequestException as exc:
            errors.append(f"{url}: {exc}")
    else:
        raise RuntimeError("Binance Futures OHLCV unavailable from this Render region; all endpoints failed: " +
                           "; ".join(errors))
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


def bingx_swap_symbol(pair: str) -> str:
    """Validate the project's perpetual-pair convention accepted by BingX."""
    symbol = pair.upper()
    if not symbol.endswith("-USDT") or not symbol[:-5].isalnum():
        raise ValueError(f"unsupported BingX swap pair: {pair}")
    return symbol


def fetch_bingx_swap_bars(pair: str, timeframe: str, outputsize: int = 300) -> list[dict]:
    """Fetch public BingX perpetual-swap klines; no account or API key is used.

    This intentionally uses BingX's production public quote endpoint. It does
    not submit orders; trade execution remains restricted to the VST endpoint
    in broker.py.
    """
    if timeframe not in _SECONDS:
        raise ValueError(f"unsupported BingX swap timeframe: {timeframe}")
    response = requests.get(
        _BINGX_SWAP_KLINES_URL,
        params={"symbol": bingx_swap_symbol(pair), "interval": timeframe,
                "limit": max(1, min(int(outputsize), 1440))},
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != 0 or not isinstance(payload.get("data"), list):
        raise ValueError(f"unexpected BingX swap kline response: {payload.get('msg', '')}")
    return [
        {"datetime": row["time"], "open": row["open"], "high": row["high"],
         "low": row["low"], "close": row["close"], "volume": row["volume"]}
        for row in payload["data"]
        if isinstance(row, dict)
    ]


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
    # key -> (current closed-candle boundary in ms, validated chronological bars)
    cache: dict[tuple[str, str], tuple[int, list[dict]]] = field(default_factory=dict)

    def fetch_closed_bars(self, pair: str, timeframe: str, limit: int, now: datetime | None = None) -> list[dict]:
        if timeframe not in _SECONDS:
            raise ValueError(f"unsupported timeframe: {timeframe}")
        instant = now or datetime.now(timezone.utc)
        now_ms, interval_ms = int(instant.timestamp() * 1000), _SECONDS[timeframe] * 1000
        closed_boundary = now_ms // interval_ms * interval_ms
        key = (pair.upper(), timeframe)
        cached = self.cache.get(key)
        if cached and cached[0] == closed_boundary and len(cached[1]) >= limit:
            return cached[1][-limit:]
        raw = self.fetcher(pair, timeframe, outputsize=limit + 1)
        bars = closed_bars(raw, timeframe, instant)[-limit:]
        self.cache[key] = (closed_boundary, bars)
        return bars


def provider_from_environment() -> MarketDataProvider:
    """Use BingX public perpetual OHLCV by default; reject unsupported providers."""
    provider = os.environ.get("MULTI_STRATEGY_PROVIDER", "").strip().lower() or "bingx"
    if provider == "binance_futures":
        return MarketDataProvider(fetcher=fetch_binance_futures_bars)
    if provider == "bingx":
        return MarketDataProvider(fetcher=fetch_bingx_swap_bars)
    raise ValueError("MULTI_STRATEGY_PROVIDER must be bingx or binance_futures")