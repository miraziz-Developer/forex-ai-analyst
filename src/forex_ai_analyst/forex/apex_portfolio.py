"""Apex Portfolio: the proven MT5 engines under one risk layer (docs/APEX_PORTFOLIO.md).

    python3 -m forex_ai_analyst.forex.apex_portfolio

A  current allocation: fixed risk per trade per engine (the bot's defaults).
B  inverse volatility: each month every engine's risk is proportional to 1 / its trailing 12-month volatility
   of monthly R, with the same total risk budget as A.
C  B plus portfolio volatility targeting (Moreira and Muir 2017): the whole book is scaled each month to aim at
   12% annual volatility from its own trailing 12 months, between 0.5x and 2x.
Weights use only months before the one they are applied to.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from forex_ai_analyst.forex import portfolio_study as ps

BASE_RISK = {"crypto": 0.3, "gold_h4": 0.75, "commodity": 0.5, "index": 1.0, "fix": 0.25, "rebound": 0.3, "fomc": 0.5}
LOOKBACK, TARGET_VOL, SCALE_RANGE = 12, 0.12, (0.5, 2.0)
DEV_END = "2023-12"


def engine_months() -> tuple[list[str], dict[str, list[float]]]:
    """Monthly R per engine, 2021-01..2026-08, from each engine's published backtest."""
    from forex_ai_analyst.forex import ideas_study as ideas
    from forex_ai_analyst.forex.crypto_cfd_study import COINS, with_swap
    from forex_ai_analyst.forex.rebound_cfd_study import trades_for
    trades = ps.engine_trades()
    trades["rebound"] = [(ps.month(t["exit_time"]), t["r"]) for c in COINS for t in with_swap(trades_for(c), 0.20)]
    bars = ideas.hourly("USA500IDXUSD")
    fomc = []
    for day in ideas.fomc_days():
        r = ideas.window_return(bars, ideas.ny(ideas.prev_weekday(day), 14), ideas.ny(day, 14))
        if r is not None:
            fomc.append((day.isoformat()[:7], r / 0.02))           # R against the live 2-daily-ATR stop (~2%)
    trades["fomc"] = fomc
    months = ps.months_between(ps.START, ps.END)
    return months, {e: [sum(r for m, r in ts if m == mo) for mo in months] for e, ts in trades.items()}


def vol(x: list[float]) -> float:
    m = sum(x) / len(x)
    return math.sqrt(sum((v - m) ** 2 for v in x) / len(x))


def allocate(monthly: dict[str, list[float]], method: str) -> list[float]:
    """Monthly portfolio return (fraction of equity) for allocation A, B or C."""
    names, n = list(monthly), len(next(iter(monthly.values())))
    budget = sum(BASE_RISK[e] for e in names)
    out = []
    for i in range(n):
        risk = dict(BASE_RISK)
        if method in ("B", "C") and i >= LOOKBACK:
            inv = {e: (1 / v if (v := vol(monthly[e][i - LOOKBACK:i])) > 0 else 0.0) for e in names}
            total = sum(inv.values())
            if total:
                risk = {e: budget * inv[e] / total for e in names}
        out.append(sum(risk[e] / 100 * monthly[e][i] for e in names))
    if method != "C":
        return out
    scaled = []
    for i, r in enumerate(out):
        k = 1.0
        if i >= LOOKBACK and (v := vol(out[i - LOOKBACK:i]) * math.sqrt(12)) > 0:
            k = min(max(TARGET_VOL / v, SCALE_RANGE[0]), SCALE_RANGE[1])
        scaled.append(k * r)
    return scaled


def current_risk(monthly: dict[str, list[float]]) -> dict[str, float]:
    """Risk % per trade for each engine for the coming month under C (the bot's FX_*_RISK_PCT values)."""
    names = list(monthly)
    budget = sum(BASE_RISK[e] for e in names)
    inv = {e: (1 / v if (v := vol(monthly[e][-LOOKBACK:])) > 0 else 0.0) for e in names}
    b = allocate(monthly, "B")
    v = vol(b[-LOOKBACK:]) * math.sqrt(12)
    k = min(max(TARGET_VOL / v, SCALE_RANGE[0]), SCALE_RANGE[1]) if v > 0 else 1.0
    return {e: round(k * budget * inv[e] / sum(inv.values()), 2) for e in names}


def main() -> None:
    months, monthly = engine_months()
    cut = months.index(DEV_END) + 1
    report = {"engines": list(monthly), "dev": {}, "holdout": {}, "full": {}}
    for method in ("A", "B", "C"):
        rets = allocate(monthly, method)
        report["dev"][method] = ps.stats(rets[LOOKBACK:cut])                 # after the warm-up year
        report["holdout"][method] = ps.stats(rets[cut:])
        report["full"][method] = ps.stats(rets[LOOKBACK:])
    report["risk_pct_now"] = current_risk(monthly)
    a = report["holdout"]["A"]
    report["adopted"] = next((m for m in ("C", "B") if report["holdout"][m]["sharpe"] > a["sharpe"]
                              and report["holdout"][m]["max_drawdown"] >= a["max_drawdown"] - 0.02), "A")
    Path("research_output").mkdir(exist_ok=True)
    Path("research_output/apex_portfolio.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
