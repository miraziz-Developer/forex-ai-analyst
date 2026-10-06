"""Exit variants for the v2c walk-forward trades (docs/ML_EXITS_STUDY.md).

    python3 -m forex_ai_analyst.forex.ml_exits      (needs: pip install scikit-learn numpy)
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from pathlib import Path

from forex_ai_analyst.forex.ml_features import load_context
from forex_ai_analyst.forex.ml_study import HORIZON, build_dataset, evaluate, models, walk_forward
from forex_ai_analyst.forex.regime_system_study import FX_ONLY

STOP = 3.0
VARIANTS = {"S": {}, "T15": {"tp": 1.5}, "T3": {"tp": 3.0}, "TR": {"trail": 1.5, "arm": 1.0}}


def replay(bars: list[dict], i: int, side: int, atr: float, tp: float | None = None,
           trail: float | None = None, arm: float | None = None) -> float:
    """Gross return of one trade decided at bar i, entered at bar i+1's open, held to bar i+HORIZON's close."""
    entry = bars[i + 1]["open"]
    stop = entry - side * STOP * atr
    target = entry + side * tp * atr if tp else None
    best = entry
    for b in bars[i + 1:i + HORIZON + 1]:
        o, hi, lo = b["open"], b["high"], b["low"]
        if side * (o - stop) <= 0:                       # gap through the stop
            return side * (o / entry - 1)
        if target and side * (o - target) >= 0:          # gap through the target
            return side * (o / entry - 1)
        if (lo if side > 0 else hi) * side <= stop * side:
            return side * (stop / entry - 1)             # stop first when both are touched
        if target and (hi if side > 0 else lo) * side >= target * side:
            return side * (target / entry - 1)
        if trail:
            best = max(best, b["close"]) if side > 0 else min(best, b["close"])
            if side * (best - entry) >= arm * atr:
                stop = max(stop, entry, best - trail * atr) if side > 0 else min(stop, entry, best + trail * atr)
    return side * (bars[i + HORIZON]["close"] / entry - 1)


def paired_p(base: list[dict], other: list[dict], runs=4000, seed=11) -> float:
    by_day: dict[str, float] = {}
    for a, b in zip(base, other):
        by_day[a["day"]] = by_day.get(a["day"], 0.0) + b["net"] - a["net"]
    days = list(by_day)
    rng = random.Random(seed)
    return sum(sum(by_day[rng.choice(days)] for _ in days) > 0 for _ in range(runs)) / runs


def main() -> None:
    X, y, meta, _ = build_dataset(with_cot=True)
    trades = walk_forward(X, y, meta, models(c=0.1)["logistic"])
    ctx = load_context(FX_ONLY)
    index = {name: {d: k for k, d in enumerate(c.days)} for name, c in ctx.caches.items()}
    report, results = {}, {}
    for name, params in VARIANTS.items():
        out = []
        for t in trades:
            c = ctx.caches[t["market"]]
            i = index[t["market"]][t["day"]]
            gross = replay(c.bars, i, t["side"], c.f["atr"][i], **params)
            out.append({**t, "net": gross - t["cost"], "hit": gross > 0})
        results[name] = out
        report[name] = {k: v for k, v in evaluate(out).items() if k not in ("per_market_total_pct", "passes", "reasons")}
    report["original_no_stop"] = {k: v for k, v in evaluate(trades).items()
                                  if k not in ("per_market_total_pct", "passes", "reasons")}
    for name in ("T15", "T3", "TR"):
        report[name]["p_better_than_S"] = paired_p(results["S"], results[name])
    for name, r in report.items():
        print(f"{name}: {json.dumps(r)}")
    out_dir = Path("research_output")
    out_dir.mkdir(exist_ok=True)
    (out_dir / "ml_exits_study.json").write_text(json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(),
                                                            "report": report}, indent=2) + "\n")


if __name__ == "__main__":
    main()
