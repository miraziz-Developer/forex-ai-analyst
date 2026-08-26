import logging

import requests

logger = logging.getLogger(__name__)

TWELVEDATA_BASE_URL = "https://api.twelvedata.com/time_series"
TIMEFRAMES = {"4h": "4h", "1h": "1h", "15min": "15min", "5min": "5min"}
BARS_PER_TIMEFRAME = 50


def normalize_symbol(raw_ticker: str) -> str:
    """Convert a TradingView-style ticker (EURUSD, FX:EURUSD) into Twelve Data's EUR/USD form."""
    ticker = raw_ticker.split(":")[-1].upper().replace("/", "")
    if len(ticker) != 6:
        raise ValueError(f"Unrecognized forex ticker: {raw_ticker!r}")
    return f"{ticker[:3]}/{ticker[3:]}"


def fetch_bars(symbol: str, interval: str, api_key: str, outputsize: int = BARS_PER_TIMEFRAME) -> list[dict]:
    response = requests.get(
        TWELVEDATA_BASE_URL,
        params={
            "symbol": symbol,
            "interval": interval,
            "outputsize": outputsize,
            "apikey": api_key,
        },
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("status") == "error":
        raise RuntimeError(f"Twelve Data error for {symbol} {interval}: {data.get('message')}")
    return data.get("values", [])


def fetch_multi_timeframe(raw_ticker: str, api_key: str) -> dict[str, list[dict]]:
    symbol = normalize_symbol(raw_ticker)
    bars = {}
    for label, interval in TIMEFRAMES.items():
        try:
            bars[label] = fetch_bars(symbol, interval, api_key)
        except (requests.RequestException, RuntimeError) as exc:
            logger.error("Failed to fetch %s %s bars: %s", symbol, label, exc)
            bars[label] = []
    return bars
