import logging
import re
import time

import requests

logger = logging.getLogger(__name__)

BINGX_BASE_URL = "https://open-api-vst.bingx.com"
TIMEFRAMES = {"1d": "1d", "4h": "4h", "1h": "1h", "15min": "15m", "5min": "5m"}
BARS_PER_TIMEFRAME = 50

# BingX's public kline endpoint is rate-limited to 5 requests per 15 minutes
# (discovered in production — not documented up front). A 4-timeframe x
# multi-pair fetch every cycle blows through that instantly. Since a candle
# can't change more often than its own period anyway, cache each (symbol,
# interval) response for roughly its bar length — this cuts real request
# volume by an order of magnitude while never serving genuinely stale data.
# 1d gets a 24h TTL, so it adds ~1 extra call/pair/day — negligible.
_CACHE_TTL_SECONDS = {"1d": 24 * 3600, "4h": 4 * 3600, "1h": 3600, "15min": 15 * 60, "5min": 5 * 60}
_cache: dict[tuple, tuple[float, list]] = {}


_RETRY_AFTER_RE = re.compile(r"retry after time:\s*(\d+)")


def _get_klines_with_retry(symbol: str, interval: str, outputsize: int, end_time_ms: int | None = None) -> dict:
    params = {"symbol": symbol, "interval": interval, "limit": outputsize}
    if end_time_ms is not None:
        params["endTime"] = end_time_ms
    for attempt in range(2):
        try:
            response = requests.get(
                f"{BINGX_BASE_URL}/openApi/swap/v2/quote/klines",
                params=params,
                timeout=20,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            if attempt == 0:
                logger.warning("Network error fetching %s %s, retrying once: %s", symbol, interval, exc)
                time.sleep(3)
                continue
            raise
        data = response.json()
        msg = str(data.get("msg", ""))
        if data.get("code") == 109400 and "within" in msg and attempt == 0:
            wait_seconds = 20.0
            match = _RETRY_AFTER_RE.search(msg)
            if match:
                retry_at_epoch_s = int(match.group(1)) / 1000
                wait_seconds = max(retry_at_epoch_s - time.time(), 1) + 2  # +2s safety margin
            logger.warning("BingX kline rate limit hit for %s %s, waiting %.1fs and retrying once",
                            symbol, interval, wait_seconds)
            time.sleep(wait_seconds)
            continue
        if data.get("code") != 0:
            raise RuntimeError(f"BingX kline error for {symbol} {interval}: {data.get('msg')}")
        return data
    raise RuntimeError(f"BingX kline error for {symbol} {interval}: rate limited after retry")


def fetch_bars(symbol: str, interval: str, outputsize: int = BARS_PER_TIMEFRAME,
                end_time_ms: int | None = None) -> list[dict]:
    """symbol like 'BTC-USDT'. Returns bars most-recent-first (BingX returns oldest-first).
    end_time_ms is for historical/backtest pagination — those calls bypass the cache
    (they're one-off point-in-time queries, not the "latest N bars" pattern the
    cache assumes) but still go through the same rate-limit retry logic."""
    if end_time_ms is not None:
        data = _get_klines_with_retry(symbol, interval, outputsize, end_time_ms)
        return _normalize(data)

    label = next((k for k, v in TIMEFRAMES.items() if v == interval), interval)
    ttl = _CACHE_TTL_SECONDS.get(label, 60)
    cache_key = (symbol, interval, outputsize)

    cached = _cache.get(cache_key)
    if cached and (time.monotonic() - cached[0]) < ttl:
        return cached[1]

    data = _get_klines_with_retry(symbol, interval, outputsize)
    normalized = _normalize(data)
    _cache[cache_key] = (time.monotonic(), normalized)
    return normalized


def _normalize(data: dict) -> list[dict]:
    """Normalize field names to what the rest of the app expects (Twelve Data-style
    keys), and reverse BingX's oldest-first order to most-recent-first."""
    bars = data.get("data", [])
    return [
        {
            "datetime": bar["time"],  # epoch ms, kept as-is; consumers that need it parse it
            "open": bar["open"],
            "high": bar["high"],
            "low": bar["low"],
            "close": bar["close"],
            "volume": bar["volume"],
        }
        for bar in reversed(bars)
    ]


def fetch_multi_timeframe(symbol: str) -> dict[str, list[dict]]:
    bars = {}
    for label, interval in TIMEFRAMES.items():
        try:
            bars[label] = fetch_bars(symbol, interval)
        except (requests.RequestException, RuntimeError) as exc:
            logger.error("Failed to fetch %s %s bars: %s", symbol, label, exc)
            bars[label] = []
    return bars
