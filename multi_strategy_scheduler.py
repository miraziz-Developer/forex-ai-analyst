"""Single-process scheduler for public Binance data and paper signal resolution."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler

import scalping_storage
import execution_alerts
from scalping_core import CandidateStatus
from scalping_data import MarketDataProvider

logger = logging.getLogger(__name__)


def recover_open_vst_orders() -> None:
    """Compare durable open VST journal rows to active broker positions.

    Recovery only observes and alerts: it cannot manufacture a close fill, P&L,
    or outcome for a position that BingX no longer reports.
    """
    import broker
    now = datetime.now(timezone.utc)
    try:
        sla_minutes = max(1, int(os.environ.get("VST_OPEN_SLA_MINUTES", "120")))
    except ValueError:
        sla_minutes = 120
    for row in scalping_storage.open_vst_orders():
        key = f"open-position:{row['fingerprint']}"
        try:
            side = "LONG" if row["direction"] == "BUY" else "SHORT"
            position = broker.get_position(row["pair"], side)
            if not position:
                execution_alerts.report(key, f"{row['pair']} #{row['fingerprint'][:10]} broker pozitsiyasi topilmadi; jurnal ochiq qoldi.",
                                        severity="CRITICAL", details={"fingerprint": row["fingerprint"], "pair": row["pair"]})
                continue
            expected = abs(float(row.get("broker_quantity") or 0))
            actual = abs(float(position.get("positionAmt", 0)))
            if expected and actual + 1e-12 < expected:
                execution_alerts.report(key, f"{row['pair']} #{row['fingerprint'][:10]} broker miqdori jurnal miqdoridan kichik.",
                                        severity="CRITICAL", details={"fingerprint": row["fingerprint"], "expected_quantity": expected, "broker_quantity": actual})
            else:
                execution_alerts.resolve(key, note="broker position matches open journal")
            opened_at = datetime.fromisoformat(row["created_at"])
            if now - opened_at > timedelta(minutes=sla_minutes):
                execution_alerts.report(f"open-sla:{row['fingerprint']}",
                                        f"{row['pair']} #{row['fingerprint'][:10]} {sla_minutes} daqiqalik open-position SLA dan oshdi.",
                                        severity="WARNING", details={"fingerprint": row["fingerprint"], "opened_at": row["created_at"], "sla_minutes": sla_minutes})
            else:
                execution_alerts.resolve(f"open-sla:{row['fingerprint']}", note="position remains within open SLA")
        except Exception as exc:
            execution_alerts.report(f"recovery-failure:{row['fingerprint']}",
                                    f"{row['pair']} #{row['fingerprint'][:10]} startup recovery qayta urinadi ({type(exc).__name__}).",
                                    details={"fingerprint": row["fingerprint"], "error": type(exc).__name__})


def resolve_open_paper_signals(provider: MarketDataProvider) -> None:
    """Resolve positions on closed bars; stop wins if a bar hits both levels.

    A position whose planned holding window ends is closed as TIME_EXIT, never
    discarded as an expired signal. VST rows are journaled only after BingX
    confirms the market-close fill; a failed API call leaves the row open for a
    safe retry on the next scheduler run.
    """
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
                        if not broker.get_position(signal["pair"], position_side):
                            logger.warning("VST position absent for expired window %s; leaving journal open "
                                           "because a TP/SL or manual-close fill cannot be safely inferred",
                                           signal["fingerprint"])
                            execution_alerts.report(f"missing-position:{signal['fingerprint']}",
                                                    f"{signal['pair']} #{signal['fingerprint'][:10]} broker pozitsiyasi topilmadi; jurnal yopilmadi.",
                                                    details={"fingerprint": signal["fingerprint"], "pair": signal["pair"]})
                            continue
                        execution_alerts.resolve(f"missing-position:{signal['fingerprint']}", note="broker position is present for time exit")
                        close_order = broker.close_position(signal["pair"], signal["direction"],
                                                            float(signal["broker_quantity"]))
                        exit_price = float(close_order["fill_price"])
                        scalping_storage.resolve_paper_signal(signal["fingerprint"], CandidateStatus.TIME_EXIT,
                                                              exit_price, close_order)
                    else:
                        scalping_storage.resolve_paper_signal(signal["fingerprint"], CandidateStatus.TIME_EXIT, exit_price)
        except Exception as exc:
            logger.warning("paper resolver will retry %s: %s", signal["fingerprint"], exc)
            execution_alerts.report(f"resolver-failure:{signal['fingerprint']}",
                                    f"{signal['pair']} #{signal['fingerprint'][:10]} time-exit close qayta urinadi ({type(exc).__name__}).",
                                    details={"fingerprint": signal["fingerprint"], "error": type(exc).__name__})


def reconcile_closed_vst_orders() -> None:
    """Verify strategy orders with immutable order IDs, never account income.

    P&L, fee and funding are stored only when the broker order response itself
    explicitly provides every value. VST income remains account-scoped and is
    intentionally not assigned to an AI signal.
    """
    import broker
    for row in scalping_storage.closed_vst_orders_pending_reconciliation():
        try:
            entry = broker.get_order(row["pair"], row["broker_order_id"])
            close = broker.get_order(row["pair"], row["broker_close_order_id"]) if row.get("broker_close_order_id") else None
            if entry["status"] not in {"FILLED", "CLOSED"} or (close and close["status"] not in {"FILLED", "CLOSED"}):
                scalping_storage.mark_reconciliation(row["fingerprint"], "PENDING", "broker order hali final FILLED emas")
                continue
            values = [entry.get("realized_pnl_usdt"), entry.get("commission_usdt")]
            if close:
                values.extend([close.get("realized_pnl_usdt"), close.get("commission_usdt")])
            if any(value is None for value in values):
                scalping_storage.mark_reconciliation(row["fingerprint"], "UNAVAILABLE",
                                                     "order response order-level P&L/fee bermadi; income taqsimlanmadi")
                execution_alerts.report(f"unreconciled-order:{row['fingerprint']}",
                                        f"{row['pair']} #{row['fingerprint'][:10]} uchun order-level fee/P&L mavjud emas.",
                                        details={"fingerprint": row["fingerprint"]})
                continue
            gross = float(entry["realized_pnl_usdt"]) + (float(close["realized_pnl_usdt"]) if close else 0.0)
            fees = abs(float(entry["commission_usdt"])) + (abs(float(close["commission_usdt"])) if close else 0.0)
            scalping_storage.mark_reconciliation(row["fingerprint"], "VERIFIED", "exchange order-ID bilan tasdiqlandi",
                                                 gross_pnl=gross, fees=fees, funding=0.0)
            execution_alerts.resolve(f"unreconciled-order:{row['fingerprint']}", note="order-level values verified")
            execution_alerts.resolve(f"reconciliation-failure:{row['fingerprint']}", note="reconciliation succeeded")
        except Exception as exc:
            logger.warning("VST reconciliation will retry %s: %s", row["fingerprint"], exc)
            execution_alerts.report(f"reconciliation-failure:{row['fingerprint']}",
                                    f"{row['pair']} #{row['fingerprint'][:10]} reconciliation qayta urinadi ({type(exc).__name__}).",
                                    details={"fingerprint": row["fingerprint"], "error": type(exc).__name__})


def start_scheduler(*, scan: Callable[[MarketDataProvider], None], provider: MarketDataProvider,
                    interval_seconds: int) -> BackgroundScheduler:
    if interval_seconds < 300:
        raise ValueError("MULTI_STRATEGY_SCAN_INTERVAL_SECONDS must be at least 300")
    scheduler = BackgroundScheduler(timezone="UTC")
    recover_open_vst_orders()
    scheduler.add_job(scan, "interval", seconds=interval_seconds, args=[provider], id="multi-strategy-scan",
                      max_instances=1, coalesce=True, next_run_time=datetime.now(timezone.utc))
    scheduler.add_job(resolve_open_paper_signals, "interval", seconds=interval_seconds, args=[provider],
                      id="multi-strategy-resolve", max_instances=1, coalesce=True,
                      next_run_time=datetime.now(timezone.utc))
    scheduler.add_job(reconcile_closed_vst_orders, "interval", seconds=interval_seconds,
                      id="bingx-vst-reconcile", max_instances=1, coalesce=True,
                      next_run_time=datetime.now(timezone.utc))
    scheduler.add_job(recover_open_vst_orders, "interval", seconds=interval_seconds,
                      id="bingx-vst-recovery", max_instances=1, coalesce=True,
                      next_run_time=datetime.now(timezone.utc))
    scheduler.start()
    logger.info("Multi-strategy scheduler started: scan and resolver every %s seconds", interval_seconds)
    return scheduler