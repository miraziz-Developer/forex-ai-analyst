"""Single-process scheduler for public Binance data and paper signal resolution."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler

import scalping_storage
from scalping_core import CandidateStatus
from scalping_data import MarketDataProvider

logger = logging.getLogger(__name__)


def resolve_open_paper_signals(provider: MarketDataProvider) -> None:
    """Resolve closed-bar paper positions conservatively: stop wins if one bar hits both levels."""
    now = datetime.now(timezone.utc)
    for signal in scalping_storage.open_paper_signals():
        try:
            bars = provider.fetch_closed_bars(signal["pair"], "5m", 300, now)
            if not bars:
                continue
            direction, stop, target = signal["direction"], float(signal["stop_price"]), float(signal["target_price"])
            for bar in bars:
                if int(bar["datetime"]) <= int(signal["candle_time"]):
                    continue
                if direction == "BUY":
                    hit_stop, hit_target = bar["low"] <= stop, bar["high"] >= target
                else:
                    hit_stop, hit_target = bar["high"] >= stop, bar["low"] <= target
                if hit_stop:
                    scalping_storage.resolve_paper_signal(signal["fingerprint"], CandidateStatus.LOSS, stop)
                    break
                if hit_target:
                    scalping_storage.resolve_paper_signal(signal["fingerprint"], CandidateStatus.WIN, target)
                    break
            else:
                if datetime.fromisoformat(signal["expiry_time"]) <= now:
                    exit_price = float(bars[-1]["close"])
                    if signal.get("broker_quantity"):
                        import broker
                        position_side = "LONG" if signal["direction"] == "BUY" else "SHORT"
                        if broker.get_position(signal["pair"], position_side):
                            exit_price = float(broker.close_position(signal["pair"], signal["direction"],
                                                                     float(signal["broker_quantity"]))["fill_price"])
                    scalping_storage.resolve_paper_signal(signal["fingerprint"], CandidateStatus.EXPIRED, exit_price)
        except Exception:
            logger.exception("paper resolver failed for %s", signal["fingerprint"])


def start_scheduler(*, scan: Callable[[MarketDataProvider], None], provider: MarketDataProvider,
                    interval_seconds: int) -> BackgroundScheduler:
    if interval_seconds < 300:
        raise ValueError("MULTI_STRATEGY_SCAN_INTERVAL_SECONDS must be at least 300")
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(scan, "interval", seconds=interval_seconds, args=[provider], id="multi-strategy-scan",
                      max_instances=1, coalesce=True, next_run_time=datetime.now(timezone.utc))
    scheduler.add_job(resolve_open_paper_signals, "interval", seconds=interval_seconds, args=[provider],
                      id="multi-strategy-resolve", max_instances=1, coalesce=True,
                      next_run_time=datetime.now(timezone.utc))
    scheduler.start()
    logger.info("Multi-strategy scheduler started: scan and resolver every %s seconds", interval_seconds)
    return scheduler