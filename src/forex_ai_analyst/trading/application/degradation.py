"""Live-vs-backtest degradation alarm for the Donchian 4h strategy.

Each closed trade is measured in R (realized P&L / risk taken). The limits come
from the lab backtest of the live configuration (entry 100, exit 20, 3 ATR,
long, the 20 live markets, 2021-2026 with taker costs and funding; 1147 trades):

    win rate 31%, mean +0.51R, median -0.8R, worst losing run 26,
    worst cumulative-R drawdown -64R (trades taken in chronological order).

Limits are anchored to that real history, not to a bootstrap of independent
trades: breakouts on correlated coins happen together, so losses cluster and a
bootstrap understates how deep a normal drawdown gets. WARNING = half the
historical worst; CRITICAL = worse than anything in the backtest.

A trend follower loses most of the time and earns from rare large winners, so
short losing streaks are normal. The alarm fires only when live results leave
the range the backtest itself produced. It never stops trading on its own: it
tells the operator, who decides whether to send STOP.
"""
from __future__ import annotations

import logging

from forex_ai_analyst.operations import incidents as execution_alerts
from forex_ai_analyst.trading.application import trend_engine
from forex_ai_analyst.trading.infrastructure import signal_repository as scalping_storage

logger = logging.getLogger(__name__)

WARN_LOSS_RUN, CRITICAL_LOSS_RUN = 16, 27
WARN_DRAWDOWN_R, CRITICAL_DRAWDOWN_R = -32.0, -65.0
INCIDENT_KEY = f"degradation:{trend_engine.STRATEGY}"


def trade_r(row: dict) -> float | None:
    """R multiple of one closed trade; broker-reconciled net P&L wins over the journal estimate."""
    try:
        risk = float(row.get("risk_usdt") or 0)
    except (TypeError, ValueError):
        return None
    if risk <= 0:
        return None
    pnl = row.get("actual_net_pnl_usdt")
    if pnl is None:
        pnl = row.get("realized_pnl_usdt")
    return None if pnl is None else float(pnl) / risk


def assess(r_values: list[float]) -> tuple[str | None, dict]:
    """(None | "WARNING" | "CRITICAL", metrics) for R values in chronological order."""
    run = worst_run = 0
    cum = peak = worst_drawdown = 0.0
    for r in r_values:
        run = run + 1 if r <= 0 else 0
        worst_run = max(worst_run, run)
        cum += r
        peak = max(peak, cum)
        worst_drawdown = min(worst_drawdown, cum - peak)
    drawdown = cum - peak
    metrics = {"trades": len(r_values), "current_loss_run": run, "worst_loss_run": worst_run,
               "cumulative_r": round(cum, 2), "drawdown_r": round(drawdown, 2),
               "worst_drawdown_r": round(worst_drawdown, 2)}
    # Judge the current state, not history: a streak that has ended no longer signals decay.
    if run >= CRITICAL_LOSS_RUN or drawdown <= CRITICAL_DRAWDOWN_R:
        return "CRITICAL", metrics
    if run >= WARN_LOSS_RUN or drawdown <= WARN_DRAWDOWN_R:
        return "WARNING", metrics
    return None, metrics


def check() -> None:
    """Scheduler job: alert when live Donchian results leave the backtested range."""
    rows = scalping_storage.closed_strategy_trades(trend_engine.STRATEGY, 500)
    r_values = [r for r in (trade_r(row) for row in rows) if r is not None]
    level, metrics = assess(r_values)
    if level is None:
        execution_alerts.resolve(INCIDENT_KEY, note="live results back inside the backtested range")
        return
    advice = ("Backtestdagi eng yomon holatdan ham yomon: STOP yuborib, strategiyani qayta tekshirish tavsiya etiladi."
              if level == "CRITICAL" else "Backtestdagi eng yomon davrning yarmiga yetdi: normal bo‘lishi mumkin, kuzatib boring.")
    message = (f"Donchian 4h degradatsiya ({level}): ketma-ket {metrics['current_loss_run']} zarar, "
               f"cho‘qqidan {metrics['drawdown_r']}R pastda ({metrics['trades']} yopiq trade). {advice}")
    logger.warning(message)
    execution_alerts.report(INCIDENT_KEY, message, severity=level, details=metrics, remind_after_minutes=24 * 60)
