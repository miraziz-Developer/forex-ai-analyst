"""Single-process scheduler for public Binance data and paper signal resolution."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler

from forex_ai_analyst.trading.infrastructure import bingx_broker as broker
from forex_ai_analyst.trading.infrastructure import signal_repository as scalping_storage
from forex_ai_analyst.operations import incidents as execution_alerts
from forex_ai_analyst.trading.domain.models import CandidateStatus
from forex_ai_analyst.trading.infrastructure.market_data import MarketDataProvider

logger = logging.getLogger(__name__)


def _broker_error_details(exc: Exception) -> dict:
    """Expose only the broker client's pre-sanitized operational diagnostic."""
    diagnostic = getattr(exc, "diagnostic", None)
    if not isinstance(diagnostic, dict):
        return {"error": type(exc).__name__}
    return {
        "error": type(exc).__name__,
        **{key: diagnostic.get(key) for key in ("endpoint", "http_status", "bingx_code", "bingx_msg", "category")},
    }


def _broker_error_summary(exc: Exception) -> str:
    """Render the allowlisted broker diagnostic for an operator-facing alert."""
    details = _broker_error_details(exc)
    if "category" not in details:
        return details["error"]
    parts = [f"category={details['category']}"]
    if details.get("endpoint"):
        parts.append(f"endpoint={details['endpoint']}")
    if details.get("http_status") is not None:
        parts.append(f"http_status={details['http_status']}")
    if details.get("bingx_code") is not None:
        parts.append(f"bingx_code={details['bingx_code']}")
    if details.get("bingx_msg"):
        parts.append(f"bingx_msg={details['bingx_msg']}")
    return "; ".join(parts)


def _auto_exit_from_history(row: dict, orders: list[dict]) -> dict | None:
    """Return one unambiguous filled broker-side close for an open journal row."""
    entry_side = str(row["direction"]).upper()
    close_side = "SELL" if entry_side == "BUY" else "BUY"
    position_side = "LONG" if entry_side == "BUY" else "SHORT"
    expected_quantity = abs(float(row.get("broker_quantity") or 0))
    opened_at_ms = int(datetime.fromisoformat(row["created_at"]).timestamp() * 1000)
    matches = []
    for order in orders:
        quantity, fill = order.get("filled_quantity"), order.get("fill_price")
        if (order.get("order_id") == str(row["broker_order_id"]) or order.get("symbol") != row["pair"].upper() or
                order.get("status") not in {"FILLED", "CLOSED"} or order.get("side") != close_side or
                order.get("position_side") != position_side or fill is None or float(fill) <= 0 or
                order.get("created_at_ms") is None or float(order["created_at_ms"]) < opened_at_ms):
            continue
        if expected_quantity and (quantity is None or abs(float(quantity) - expected_quantity) > max(1e-8, expected_quantity * .01)):
            continue
        matches.append(order)
    return matches[0] if len(matches) == 1 else None


def _auto_exit_status(row: dict, order: dict) -> CandidateStatus:
    """Classify from actual broker fills; trigger type is stored only as reason."""
    entry = row.get("broker_fill_price") or row.get("entry_price")
    if entry is None:
        return CandidateStatus.WIN
    multiplier = 1 if row["direction"] == "BUY" else -1
    pnl = (float(order["fill_price"]) - float(entry)) * multiplier
    if pnl > 0:
        return CandidateStatus.WIN
    if pnl < 0:
        return CandidateStatus.LOSS
    return CandidateStatus.TIME_EXIT


def recover_open_vst_orders() -> None:
    """Compare durable open VST journal rows to active broker positions.

    Recovery closes a journal only after a single immutable, fully matched broker
    close fill is present in history; absence or ambiguity remains fail-closed.
    """
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
                start_time_ms = int(datetime.fromisoformat(row["created_at"]).timestamp() * 1000)
                close_order = _auto_exit_from_history(row, broker.order_history(row["pair"], start_time_ms=start_time_ms))
                if close_order:
                    status = _auto_exit_status(row, close_order)
                    reason = str(close_order.get("type") or "BROKER_CLOSE")
                    scalping_storage.resolve_paper_signal(row["fingerprint"], status, float(close_order["fill_price"]),
                                                          close_order, close_reason=reason)
                    execution_alerts.resolve(key, note="broker-side close fill matched immutable order history", notify=True)
                    execution_alerts.resolve(f"missing-position:{row['fingerprint']}", note="broker-side close fill matched")
                    continue
                age_limited = start_time_ms < int((now - timedelta(days=7)).timestamp() * 1000)
                message = f"{row['pair']} #{row['fingerprint'][:10]} broker pozitsiyasi topilmadi va close fill tarixi aniq mos kelmadi; jurnal ochiq qoldi."
                if age_limited:
                    message += " Jurnal 7 kundan eski: BingX history oynasi exit fillni qamramasligi mumkin; manual audit yoki saqlangan fill talab qilinadi."
                execution_alerts.report(key, message, severity="CRITICAL", details={
                    "fingerprint": row["fingerprint"], "pair": row["pair"],
                    "history_window_limited": age_limited,
                })
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
            details = {"fingerprint": row["fingerprint"], **_broker_error_details(exc)}
            execution_alerts.report(f"recovery-failure:{row['fingerprint']}",
                                    f"{row['pair']} #{row['fingerprint'][:10]} startup recovery qayta urinadi "
                                    f"({_broker_error_summary(exc)}).",
                                    details=details)


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
            details = {"fingerprint": row["fingerprint"], **_broker_error_details(exc)}
            execution_alerts.report(f"reconciliation-failure:{row['fingerprint']}",
                                    f"{row['pair']} #{row['fingerprint'][:10]} reconciliation qayta urinadi "
                                    f"({_broker_error_summary(exc)}).",
                                    details=details)


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