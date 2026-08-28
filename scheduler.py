import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler

import broker
import storage
from market_data import fetch_bars
from notifier import send_telegram_message

logger = logging.getLogger(__name__)

WATCH_PAIRS = [p.strip() for p in os.environ.get("WATCH_PAIRS", "BTC-USDT,ETH-USDT,SOL-USDT,XRP-USDT,BNB-USDT").split(",") if p.strip()]
POLL_INTERVAL_MINUTES = int(os.environ.get("POLL_INTERVAL_MINUTES", "20"))
RESOLVER_INTERVAL_MINUTES = int(os.environ.get("RESOLVER_INTERVAL_MINUTES", "15"))
SIGNAL_EXPIRY_HOURS = int(os.environ.get("SIGNAL_EXPIRY_HOURS", "24"))

_last_digest_date: date | None = None

# Watchdog: tracks the last analysis_job tick where at least one pair was
# checked without raising. If every pair keeps erroring (bad API key, BingX/
# Azure outage, etc.) the process itself stays alive and /health stays "ok" —
# this is the only signal that catches that specific silent-failure case.
_last_successful_check: datetime | None = None
_last_watchdog_alert: datetime | None = None
_WATCHDOG_ALERT_COOLDOWN_MINUTES = 60


def start_scheduler(on_check: Callable[[str], dict],
                     telegram_bot_token: str, telegram_chat_id: str) -> BackgroundScheduler:
    """Runs a full AI read on every watched pair on a timer (no indicator gating —
    the AI decides each tick, see extract_recommendation in app.py), plus a second,
    separate timer that resolves open signals against real price history (and, if a
    real broker position exists, force-closes it on expiry) and sends a daily digest."""
    scheduler = BackgroundScheduler(timezone="UTC")

    def analysis_job():
        global _last_successful_check
        for pair in WATCH_PAIRS:
            try:
                on_check(pair)
                _last_successful_check = datetime.now(timezone.utc)
            except Exception:
                logger.exception("Scheduler: check failed for %s", pair)

    def resolver_job():
        try:
            _resolve_open_signals()
        except Exception:
            logger.exception("Scheduler: resolving open signals failed")
        try:
            _maybe_send_daily_digest(telegram_bot_token, telegram_chat_id)
        except Exception:
            logger.exception("Scheduler: daily digest failed")
        try:
            _check_watchdog(telegram_bot_token, telegram_chat_id)
        except Exception:
            logger.exception("Scheduler: watchdog check failed")

    scheduler.add_job(analysis_job, "interval", minutes=POLL_INTERVAL_MINUTES,
                       next_run_time=datetime.now(timezone.utc))
    scheduler.add_job(resolver_job, "interval", minutes=RESOLVER_INTERVAL_MINUTES,
                       next_run_time=datetime.now(timezone.utc))
    scheduler.start()
    logger.info(
        "Scheduler started: AI check on %s every %s min, signal resolver every %s min",
        WATCH_PAIRS, POLL_INTERVAL_MINUTES, RESOLVER_INTERVAL_MINUTES,
    )
    return scheduler


def _resolve_open_signals() -> None:
    for signal in storage.get_open_signals():
        _resolve_one(signal)


def _resolve_one(signal: dict) -> None:
    symbol = signal["pair"]
    try:
        bars = fetch_bars(symbol, "1h", outputsize=100)
    except Exception as exc:
        logger.error("Resolver: failed to fetch bars for %s: %s", symbol, exc)
        return
    if not bars:
        return

    signal_time = signal["signal_time"]
    if signal_time.tzinfo is None:
        signal_time = signal_time.replace(tzinfo=timezone.utc)
    direction = signal["direction"]
    target = float(signal["target_price"])
    stop = float(signal["stop_price"])

    chrono = sorted(
        (b for b in bars if _bar_time(b) >= signal_time),
        key=_bar_time,
    )

    for bar in chrono:
        high, low = float(bar["high"]), float(bar["low"])
        if direction == "BUY":
            hit_target, hit_stop = high >= target, low <= stop
        else:
            hit_target, hit_stop = low <= target, high >= stop

        if hit_stop:  # if both hit in the same bar, treat conservatively as the loss
            storage.resolve_signal(signal["id"], "LOSS", stop)
            logger.info("Signal %s (%s) resolved: LOSS at %s", signal["id"], symbol, stop)
            return
        if hit_target:
            storage.resolve_signal(signal["id"], "WIN", target)
            logger.info("Signal %s (%s) resolved: WIN at %s", signal["id"], symbol, target)
            return

    age_hours = (datetime.now(timezone.utc) - signal_time).total_seconds() / 3600
    if age_hours >= SIGNAL_EXPIRY_HOURS:
        latest_price = float(bars[0]["close"])
        broker_qty = signal.get("broker_qty")
        if broker_qty:
            try:
                broker.close_position(symbol, direction, broker_qty)
                logger.info("Force-closed expired broker position for signal %s (%s)", signal["id"], symbol)
            except Exception:
                logger.exception("Resolver: failed to force-close expired position for signal %s (%s)",
                                  signal["id"], symbol)
        storage.resolve_signal(signal["id"], "EXPIRED", latest_price)
        logger.info("Signal %s (%s) resolved: EXPIRED at %s", signal["id"], symbol, latest_price)


def _check_watchdog(telegram_bot_token: str, telegram_chat_id: str) -> None:
    """Alerts if analysis_job hasn't completed a single successful pair check in
    a while — catches the case where the process is alive (/health still says
    "ok") but every check has been silently erroring (expired API key, BingX/
    Azure outage, etc.), which nothing else here would ever surface."""
    global _last_watchdog_alert
    now = datetime.now(timezone.utc)

    if _last_successful_check is None:
        return  # still starting up — first analysis_job tick hasn't landed yet

    stale_for = now - _last_successful_check
    threshold = timedelta(minutes=POLL_INTERVAL_MINUTES * 3)
    if stale_for < threshold:
        return

    if _last_watchdog_alert and (now - _last_watchdog_alert) < timedelta(minutes=_WATCHDOG_ALERT_COOLDOWN_MINUTES):
        return  # already alerted recently, don't spam every 15 min while it stays down

    _last_watchdog_alert = now
    hours = stale_for.total_seconds() / 3600
    send_telegram_message(
        f"⚠️ Ogohlantirish: {hours:.1f} soatdan beri birorta ham juftlik muvaffaqiyatli "
        f"tekshirilmadi (barcha urinishlar xatolik bilan tugadi). Render loglarini tekshiring.",
        telegram_bot_token, telegram_chat_id,
    )
    logger.warning("Watchdog: no successful analysis check in %.1f hours — alert sent", hours)


def _bar_time(bar: dict) -> datetime:
    return datetime.fromtimestamp(bar["datetime"] / 1000, tz=timezone.utc)


def _maybe_send_daily_digest(telegram_bot_token: str, telegram_chat_id: str) -> None:
    global _last_digest_date
    today = datetime.now(timezone.utc).date()
    if _last_digest_date == today:
        return
    _last_digest_date = today

    stats = storage.get_stats()
    all_time, last_30 = stats["all_time"], stats["last_30_days"]
    lines = [
        "Kunlik hisobot",
        f"Ochiq signallar: {stats['open_signals']}",
        f"Barcha vaqt — G'alaba: {all_time.get('WIN', 0)}, Zarar: {all_time.get('LOSS', 0)}, "
        f"Muddati o'tgan: {all_time.get('EXPIRED', 0)}, Foiz: {all_time['win_rate_pct']}%, "
        f"Real P&L: {all_time['realized_pnl_usdt']:+.2f} USDT",
        f"So'nggi 30 kun — G'alaba: {last_30.get('WIN', 0)}, Zarar: {last_30.get('LOSS', 0)}, "
        f"Foiz: {last_30['win_rate_pct']}%, Real P&L: {last_30['realized_pnl_usdt']:+.2f} USDT",
    ]
    send_telegram_message("\n".join(lines), telegram_bot_token, telegram_chat_id)
