"""Pre-registered test of the regime-switching system (docs/REGIME_SYSTEM_STUDY.md).

    python3 -m forex_ai_analyst.forex.regime_system_study
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

from forex_ai_analyst.forex.data import MARKETS, cost_fraction
from forex_ai_analyst.forex.regime_system import module_signals
from forex_ai_analyst.forex.study import bars_for, bootstrap_p_mean_positive, summarize
from forex_ai_analyst.lab import engine

GATE = {"min_trades": 100, "min_bootstrap_p": 0.90, "min_profit_factor": 1.2, "min_market_share_positive": 0.6}
FX_ONLY = [m for m in MARKETS.values() if m.name not in ("BTCUSD",)]   # currencies + gold


def run(timeframe: str) -> dict:
    by_module: dict[str, list[dict]] = {"trend": [], "range": [], "breakout": []}
    for market in FX_ONLY:
        bars = bars_for(market, timeframe)
        if len(bars) < 400:
            continue
        side = cost_fraction(market, median(b["close"] for b in bars)) / 2 * 100
        costs = engine.Costs(fee_pct_per_side=side, slippage_pct_per_side=0.0, charge_funding=False)
        for name, signals in module_signals(bars).items():
            by_module[name] += [{**t, "market": market.name}
                                for t in engine.run(bars, signals, costs=costs).trades]
    by_module["combined"] = [t for name in ("trend", "range", "breakout") for t in by_module[name]]
    report = {}
    for name, trades in by_module.items():
        trades.sort(key=lambda t: t["exit_time"])
        if not trades:
            report[name] = {"pooled": {"trades": 0}, "passes": False, "reasons": ["no trades"]}
            continue
        mid = trades[len(trades) // 2]["exit_time"]
        halves = [summarize([t for t in trades if t["exit_time"] < mid]),
                  summarize([t for t in trades if t["exit_time"] >= mid])]
        per_market: dict[str, list] = {}
        for t in trades:
            per_market.setdefault(t["market"], []).append(t["r"])
        positive = sum(1 for rs in per_market.values() if sum(rs) > 0)
        pooled, p = summarize(trades), bootstrap_p_mean_positive(trades)
        reasons = []
        if pooled["trades"] < GATE["min_trades"]:
            reasons.append(f"{pooled['trades']} trades < {GATE['min_trades']}")
        if p < GATE["min_bootstrap_p"]:
            reasons.append(f"P(mean R > 0) {p:.2f} < {GATE['min_bootstrap_p']}")
        if (pooled.get("profit_factor") or 0) < GATE["min_profit_factor"]:
            reasons.append(f"profit factor {pooled.get('profit_factor')} < {GATE['min_profit_factor']}")
        if any(h.get("mean_r", 0) <= 0 for h in halves):
            reasons.append("not positive in both halves")
        if positive / len(per_market) < GATE["min_market_share_positive"]:
            reasons.append(f"only {positive}/{len(per_market)} markets positive")
        years = (trades[-1]["exit_time"] - trades[0]["entry_time"]) / 86_400_000 / 365
        report[name] = {"pooled": pooled, "p_mean_positive": round(p, 3), "trades_per_month": round(len(trades) / years / 12, 1),
                        "halves": [h.get("mean_r") for h in halves], "markets_positive": f"{positive}/{len(per_market)}",
                        "per_market_total_r": {m: round(sum(rs), 1) for m, rs in per_market.items()},
                        "passes": not reasons, "reasons": reasons}
    return report


def main() -> None:
    results = {"D1": run("D1"), "H4": run("H4")}
    out = Path("research_output")
    out.mkdir(exist_ok=True)
    (out / "regime_system_study.json").write_text(json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(),
                                                              "gate": GATE, "results": results}, indent=2) + "\n")
    for tf, report in results.items():
        for name, r in report.items():
            s = r["pooled"]
            print(f"{tf} {name:9s} trades {s.get('trades')} ({r.get('trades_per_month')}/mo) win {s.get('win_rate')} "
                  f"meanR {s.get('mean_r')} PF {s.get('profit_factor')} P {r.get('p_mean_positive')} halves {r.get('halves')} "
                  f"markets+ {r.get('markets_positive')} -> {'PASS' if r['passes'] else 'FAIL: ' + '; '.join(r['reasons'])}")


if __name__ == "__main__":
    main()
