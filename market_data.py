import logging

import requests

logger = logging.getLogger(__name__)

BINGX_BASE_URL = "https://open-api-vst.bingx.com"
TIMEFRAMES = {"4h": "4h", "1h": "1h", "15min": "15m", "5min": "5m"}
BARS_PER_TIMEFRAME = 50


def fetch_bars(symbol: str, interval: str, outputsize: int = BARS_PER_TIMEFRAME) -> list[dict]:
    """symbol like 'BTC-USDT'. Returns bars most-recent-first (BingX returns oldest-first)."""
    response = requests.get(
        f"{BINGX_BASE_URL}/openApi/swap/v2/quote/klines",
        params={"symbol": symbol, "interval": interval, "limit": outputsize},
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("code") != 0:
        raise RuntimeError(f"BingX kline error for {symbol} {interval}: {data.get('msg')}")

    bars = data.get("data", [])
    # normalize field names to what the rest of the app expects (Twelve Data-style keys),
    # and reverse to most-recent-first
    normalized = [
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
    return normalized


def fetch_multi_timeframe(symbol: str) -> dict[str, list[dict]]:
    bars = {}
    for label, interval in TIMEFRAMES.items():
        try:
            bars[label] = fetch_bars(symbol, interval)
        except (requests.RequestException, RuntimeError) as exc:
            logger.error("Failed to fetch %s %s bars: %s", symbol, label, exc)
            bars[label] = []
    return bars
