"""Gold (and silver) on H4 with the crypto Donchian rule (docs/GOLD_H4_STUDY.md).

    python3 -m forex_ai_analyst.forex.gold_h4_study
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from forex_ai_analyst.forex import dukascopy
from forex_ai_analyst.forex import h1_ml_study as h1
from forex_ai_analyst.forex.study import bootstrap_p_mean_positive, summarize
from forex_ai_analyst.lab import engine
from forex_ai_analyst.lab.data import resample
from forex_ai_analyst.lab.strategies import donchian

dukascopy.POINT.update({"XAGUSD": 1e-3})
PARAMS = {"entry_n": 100, "exit_n": 20, "stop_atr": 3.0, "sides": "long"}
COST = {"XAUUSD": 0.0005, "XAGUSD": 0.0010}
SWAP_PER_YEAR = 0.05


def h4(name: str) -> list[dict]:
    hourly = [{"datetime": b["datetime"], "open": (b["bid_open"] + b["ask_open"]) / 2,
               "high": (b["bid_high"] + b["ask_high"]) / 2, "low": (b["bid_low"] + b["ask_low"]) / 2,
               "close": (b["bid_close"] + b["ask_close"]) / 2, "volume": 0} for b in h1.load(name)]
    return resample(hourly, 4)


def trades_for(name: str) -> list[dict]:
    bars = h4(name)
    costs = engine.Costs(fee_pct_per_side=COST[name] / 2 * 100, slippage_pct_per_side=0.0, charge_funding=False)
    out = []
    for t in engine.run(bars, donchian(bars, **PARAMS), costs=costs).trades:
        days = (t["exit_time"] - t["entry_time"]) / 86_400_000
        swap = t["qty"] * t["entry"] * SWAP_PER_YEAR * days / 365
        out.append({**t, "market": name, "r": (t["net"] - swap) / t["risk"] if t["risk"] else 0.0})
    return out


def main() -> None:
    gold, silver = trades_for("XAUUSD"), trades_for("XAGUSD")
    gold.sort(key=lambda t: t["exit_time"])
    mid = gold[len(gold) // 2]["exit_time"]
    halves = [summarize([t for t in gold if t["exit_time"] < mid]), summarize([t for t in gold if t["exit_time"] >= mid])]
    s, p, sv = summarize(gold), bootstrap_p_mean_positive(gold), summarize(silver)
    reasons = []
    if s["trades"] < 100:
        reasons.append("too few trades")
    if (s.get("profit_factor") or 0) < 1.3:
        reasons.append(f"PF {s.get('profit_factor')} < 1.3")
    if p < 0.95:
        reasons.append(f"P {p:.3f} < 0.95")
    if any(h.get("mean_r", 0) <= 0 for h in halves):
        reasons.append("not positive in both halves")
    if sv.get("mean_r", 0) <= 0:
        reasons.append("silver replication not positive")
    report = {"gold": {**s, "p_mean_positive": round(p, 3), "halves": [h.get("mean_r") for h in halves]},
              "silver": sv, "passes": not reasons, "reasons": reasons}
    print(json.dumps(report))
    Path("research_output").mkdir(exist_ok=True)
    Path("research_output/gold_h4_study.json").write_text(json.dumps(
        {"generated_at": datetime.now(timezone.utc).isoformat(), "report": report}, indent=2) + "\n")


if __name__ == "__main__":
    main()
