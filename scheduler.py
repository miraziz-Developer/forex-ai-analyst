import logging
import os
from datetime import datetime, timezone
from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)

WATCH_PAIRS = [p.strip() for p in os.environ.get("WATCH_PAIRS", "EURUSD,GBPUSD,USDJPY").split(",") if p.strip()]
POLL_INTERVAL_MINUTES = int(os.environ.get("POLL_INTERVAL_MINUTES", "20"))


def start_scheduler(on_check: Callable[[str], dict]) -> BackgroundScheduler:
    """Runs a full AI read on every watched pair on a timer. There's no indicator
    gating this — the AI itself decides each tick whether anything is worth a signal
    (see extract_recommendation in app.py)."""
    scheduler = BackgroundScheduler(timezone="UTC")

    def job():
        for pair in WATCH_PAIRS:
            try:
                on_check(pair)
            except Exception:
                logger.exception("Scheduler: check failed for %s", pair)

    scheduler.add_job(job, "interval", minutes=POLL_INTERVAL_MINUTES, next_run_time=datetime.now(timezone.utc))
    scheduler.start()
    logger.info(
        "Scheduler started: full AI check on %s every %s min",
        WATCH_PAIRS, POLL_INTERVAL_MINUTES,
    )
    return scheduler
