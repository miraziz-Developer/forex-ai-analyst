import logging
import threading
import time

import requests

logger = logging.getLogger(__name__)

TWELVEDATA_BASE_URL = "https://api.twelvedata.com/time_series"
TIMEFRAMES = {"4h": "4h", "1h": "1h", "15min": "15min", "5min": "5min"}
BARS_PER_TIMEFRAME = 50

# Twelve Data free tier: 8 requests/minute. This gates every call (across
# threads/pairs) so concurrent checks — e.g. the scheduler and a manual
# webhook firing close together — never burst past that and get 429s.
_MAX_CALLS_PER_MINUTE = 8
_rate_lock = threading.Lock()
_call_timestamps: list[float] = []


def _throttle() -> None:
    with _rate_lock:
        while True:
            now = time.monotonic()
            while _call_timestamps and now - _call_timestamps[0] > 60:
                _call_timestamps.pop(0)
            if len(_call_timestamps) < _MAX_CALLS_PER_MINUTE:
                _call_timestamps.append(now)
                return
            time.sleep(60 - (now - _call_timestamps[0]) + 0.5)


def normalize_symbol(raw_ticker: str) -> str:
    """Convert a TradingView-style ticker (EURUSD, FX:EURUSD) into Twelve Data's EUR/USD form."""
    ticker = raw_ticker.split(":")[-1].upper().replace("/", "")
    if len(ticker) != 6:
        raise ValueError(f"Unrecognized forex ticker: {raw_ticker!r}")
    return f"{ticker[:3]}/{ticker[3:]}"


def fetch_bars(symbol: str, interval: str, api_key: str, outputsize: int = BARS_PER_TIMEFRAME) -> list[dict]:
    _throttle()
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
