"""How the MT5 engines combine: monthly R per engine, correlations, and portfolio results for risk allocations.

    python3 -m forex_ai_analyst.forex.portfolio_study

Analysis only (no new rule): every engine's backtest trades as already published in its own study.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

from forex_ai_analyst.forex import commodity_trend_study as ct
from forex_ai_analyst.forex import crypto_cfd_study as cc
from forex_ai_analyst.forex import fix_study as fx
from forex_ai_analyst.forex import gold_h4_study as gh
from forex_ai_analyst.forex import index_study as ix
from forex_ai_analyst.forex.data import Market, load_yahoo
from forex_ai_analyst.lab.strategies import atr

START, END = "2021-01", "2026-08"          # months every engine has data for
ALLOCATIONS = {
    "equal 0.5%": {"crypto": 0.5, "gold_h4": 0.5, "index": 0.5, "commodity": 0.5, "fix": 0.5},
    "by evidence": {"crypto": 0.5, "gold_h4": 0.5, "index": 0.4, "commodity": 0.25, "fix": 0.15},
}


def month(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m")


def engine_trades() -> dict[str, list[tuple[str, float]]]:
    """(exit month, R) per trade for each engine."""
    out = {}
    raw = [t for c in cc.COINS for t in cc.with_swap(cc.trades_for(c), 0.20)]
    out["crypto"] = [(month(t["exit_time"]), t["r"]) for t in raw]
    out["gold_h4"] = [(month(t["exit_time"]), t["r"]) for t in gh.trades_for("XAUUSD")]
    p = {"entry_n": 55, "exit_n": 20, "stop_atr": 2.0, "sides": "long"}
    basket = [m for m in ct.MARKETS if m.name in ("XAUUSD", "XAGUSD", "WTI", "BRENT")]
    out["commodity"] = [(month(t["exit_time"]), t["r"]) for m in basket for t in ct.run_market(m, p)]
    idx = []
    for name, symbol in ix.INDICES:
        bars = load_yahoo(Market(name, symbol, ix.COST, pct=True), "1d")
        a, pos = atr(bars, 14), {ix.day(b): k for k, b in enumerate(bars)}
        for t in ix.backtest(bars, "IDX1", name):
            k = pos[t["entry_day"]]
            stop_frac = ix.STOP_ATR * a[k - 1] / bars[k]["open"]
            idx.append((t["exit_day"][:7], t["net"] / stop_frac))
    out["index"] = idx
    fix = []
    for d in fx.month_ends():
        for pair in fx.PAIRS:
            t = fx.trade(pair, d, fx.next_weekday(d))
            if t:
                fix.append((t["month"], t["net"] / 0.01))
    out["fix"] = fix
    return out


def months_between(a: str, b: str) -> list[str]:
    y, m = map(int, a.split("-"))
    out = []
    while f"{y:04d}-{m:02d}" <= b:
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def corr(a: list[float], b: list[float]) -> float:
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va, vb = math.sqrt(sum((x - ma) ** 2 for x in a)), math.sqrt(sum((y - mb) ** 2 for y in b))
    return cov / (va * vb) if va and vb else 0.0


def stats(rets: list[float]) -> dict:
    eq = peak = 1.0
    dd = 0.0
    for r in rets:
        eq *= 1 + r
        peak, dd = max(peak, eq), min(dd, eq / peak - 1)
    mean = sum(rets) / len(rets)
    sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / len(rets))
    return {"cagr": round(eq ** (12 / len(rets)) - 1, 4), "max_drawdown": round(dd, 4),
            "sharpe": round(mean / sd * math.sqrt(12), 2) if sd else 0.0, "worst_month": round(min(rets), 4),
            "losing_months": f"{sum(r < 0 for r in rets)}/{len(rets)}"}


def main() -> None:
    trades = engine_trades()
    months = months_between(START, END)
    monthly = {e: [sum(r for m, r in ts if m == mo) for mo in months] for e, ts in trades.items()}
    names = list(monthly)
    report = {"months": len(months),
              "per_engine_R_per_year": {e: round(sum(v) / len(v) * 12, 1) for e, v in monthly.items()},
              "correlation": {a: {b: round(corr(monthly[a], monthly[b]), 2) for b in names} for a in names},
              "single_engine_at_0.5pct": {e: stats([0.005 * r for r in monthly[e]]) for e in names},
              "portfolios": {}}
    for label, alloc in ALLOCATIONS.items():
        rets = [sum(alloc[e] / 100 * monthly[e][i] for e in names) for i in range(len(months))]
        report["portfolios"][label] = stats(rets)
    print(json.dumps(report, indent=1))
    Path("research_output").mkdir(exist_ok=True)
    Path("research_output/portfolio_study.json").write_text(json.dumps(
        {"generated_at": datetime.now(timezone.utc).isoformat(), "report": report}, indent=2) + "\n")


if __name__ == "__main__":
    main()
