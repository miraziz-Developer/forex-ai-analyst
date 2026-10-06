"""Turn-of-the-month in stock indices (docs/TOM_STUDY.md).

    python3 -m forex_ai_analyst.forex.tom_study
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from pathlib import Path

from forex_ai_analyst.forex.data import Market, load_yahoo
from forex_ai_analyst.forex.index_study import COST, INDICES, REPLICATION, SPLIT, SWAP_PER_YEAR, day, days_between

GATE = {"min_trades": 250, "min_p": 0.95, "min_pf": 1.3, "min_share_positive": 2 / 3, "min_drift_multiple": 2.0}


def tom_trades(bars: list[dict], market: str) -> list[dict]:
    """Enter at the close of the second-to-last trading day of a month, exit at the close of day 3 of the next."""
    days = [day(b) for b in bars]
    out = []
    for i in range(1, len(bars) - 3):
        last_of_month = days[i][:7] != days[i + 1][:7]
        if not last_of_month:
            continue
        entry, exit_ = i - 1, i + 3                       # day -2 close -> close of the 3rd day of the new month
        if exit_ >= len(bars) or days[exit_][:7] != days[i + 1][:7]:
            continue
        held = max(days_between(days[entry], days[exit_]), 1)
        net = bars[exit_]["close"] / bars[entry]["close"] - 1 - COST - SWAP_PER_YEAR * held / 365
        out.append({"market": market, "entry_day": days[entry], "month": days[i + 1][:7], "net": net, "bars_held": 4})
    return out


def drift_per_bar(bars: list[dict]) -> float:
    rets = [b["close"] / a["close"] - 1 for a, b in zip(bars, bars[1:])]
    return sum(rets) / len(rets)


def bootstrap_p(trades: list[dict], runs: int = 4000, seed: int = 11) -> float:
    by_month: dict[str, list[float]] = {}
    for t in trades:
        by_month.setdefault(t["month"], []).append(t["net"])
    months = list(by_month)
    rng = random.Random(seed)
    return sum(sum(x for _ in months for x in by_month[rng.choice(months)]) > 0 for _ in range(runs)) / runs


def evaluate(data: dict[str, list[dict]]) -> dict:
    trades = [t for name, b in data.items() for t in tom_trades(b, name)]
    net = [t["net"] for t in trades]
    gains, losses = sum(x for x in net if x > 0), -sum(x for x in net if x < 0)
    halves = [[t["net"] for t in trades if t["entry_day"] < SPLIT], [t["net"] for t in trades if t["entry_day"] >= SPLIT]]
    per: dict[str, float] = {}
    for t in trades:
        per[t["market"]] = per.get(t["market"], 0.0) + t["net"]
    drift = sum(drift_per_bar(b) for b in data.values()) / len(data)
    per_bar = sum(net) / len(net) / 4
    s = {"trades": len(net), "hit_rate": round(sum(x > 0 for x in net) / len(net), 3),
         "mean_net_pct": round(sum(net) / len(net) * 100, 4), "profit_factor": round(gains / losses, 3),
         "p_mean_positive": bootstrap_p(trades),
         "halves_mean_pct": [round(sum(h) / len(h) * 100, 4) for h in halves],
         "net_per_day_pct": round(per_bar * 100, 4), "drift_per_day_pct": round(drift * 100, 4),
         "markets_positive": f"{sum(v > 0 for v in per.values())}/{len(per)}",
         "per_market_total_pct": {k: round(v * 100, 1) for k, v in per.items()}}
    reasons = []
    if s["trades"] < GATE["min_trades"]:
        reasons.append("too few trades")
    if s["p_mean_positive"] < GATE["min_p"]:
        reasons.append(f"P {s['p_mean_positive']:.3f} < {GATE['min_p']}")
    if s["profit_factor"] < GATE["min_pf"]:
        reasons.append(f"PF {s['profit_factor']} < {GATE['min_pf']}")
    if min(sum(h) for h in halves) <= 0:
        reasons.append("not positive in both periods")
    if sum(v > 0 for v in per.values()) / len(per) < GATE["min_share_positive"]:
        reasons.append("too few indices positive")
    if per_bar < GATE["min_drift_multiple"] * drift:
        reasons.append("does not beat 2x the drift per day")
    return {**s, "passes": not reasons, "reasons": reasons}


def main() -> None:
    load = lambda pairs: {n: load_yahoo(Market(n, s, COST, pct=True), "1d") for n, s in pairs}   # noqa: E731
    report = {"main": evaluate(load(INDICES)), "replication": evaluate(load(REPLICATION))}
    report["adopted"] = report["main"]["passes"] and report["replication"]["passes"]
    for k in ("main", "replication"):
        print(f"{k}: {json.dumps(report[k])}")
    print("adopted:", report["adopted"])
    Path("research_output").mkdir(exist_ok=True)
    Path("research_output/tom_study.json").write_text(json.dumps(
        {"generated_at": datetime.now(timezone.utc).isoformat(), "gate": GATE, "report": report}, indent=2) + "\n")


if __name__ == "__main__":
    main()
