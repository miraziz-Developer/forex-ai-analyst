import logging
import os
from datetime import datetime, timezone
from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler

from indicators import detect_crossover
from market_data import fetch_bars, normalize_symbol

logger = logging.getLogger(__name__)

WATCH_PAIRS = [p.strip() for p in os.environ.get("WATCH_PAIRS", "EURUSD,GBPUSD,USDJPY").split(",") if p.strip()]
EMA_LENGTH = int(os.environ.get("EMA_LENGTH", "21"))
TRIGGER_TIMEFRAME = os.environ.get("TRIGGER_TIMEFRAME", "1h")
POLL_INTERVAL_MINUTES = int(os.environ.get("POLL_INTERVAL_MINUTES", "15"))

_last_seen_bar_time: dict[str, str] = {}


def _check_pair(pair: str, twelvedata_api_key: str, on_trigger: Callable[[str], None]) -> None:
    symbol = normalize_symbol(pair)
    try:
        bars = fetch_bars(symbol, TRIGGER_TIMEFRAME, twelvedata_api_key, outputsize=EMA_LENGTH + 5)
    except Exception as exc:
        logger.error("Scheduler: failed to fetch %s %s bars: %s", symbol, TRIGGER_TIMEFRAME, exc)
        return

    if not bars:
        return

    latest_bar_time = bars[0].get("datetime")
    if _last_seen_bar_time.get(pair) == latest_bar_time:
        return
    _last_seen_bar_time[pair] = latest_bar_time

    direction = detect_crossover(bars, EMA_LENGTH)
    if direction:
        logger.info("Scheduler: EMA%s crossover (%s) detected for %s", EMA_LENGTH, direction, symbol)
        on_trigger(pair)


def start_scheduler(twelvedata_api_key: str, on_trigger: Callable[[str], None]) -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="UTC")

    def job():
        for pair in WATCH_PAIRS:
            _check_pair(pair, twelvedata_api_key, on_trigger)

    scheduler.add_job(job, "interval", minutes=POLL_INTERVAL_MINUTES, next_run_time=datetime.now(timezone.utc))
    scheduler.start()
    logger.info(
        "Scheduler started: watching %s every %s min on %s bars (EMA%s)",
        WATCH_PAIRS, POLL_INTERVAL_MINUTES, TRIGGER_TIMEFRAME, EMA_LENGTH,
    )
    return scheduler
