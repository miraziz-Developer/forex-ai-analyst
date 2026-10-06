"""Pre-registered FX session-flow study (docs/SESSION_FLOW_STUDY.md).

    python3 -m forex_ai_analyst.forex.session_study
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from pathlib import Path

from forex_ai_analyst.forex.data import MARKETS, cost_fraction, load_yahoo

PAIRS = ("EURUSD", "GBPUSD", "AUDUSD", "NZDUSD", "USDCAD", "USDCHF", "USDJPY")
US_OPEN, US_CLOSE = 12, 20
SPLIT = "2025-05-01"
GATE = {"min_trades": 1000, "min_p": 0.95, "min_pf": 1.2, "min_positive": 5}


def usd_sign(pair: str) -> int:
    """+1 if buying the pair is long USD."""
    return 1 if pair.startswith("USD") else -1


def session_trades(bars: list[dict], pair: str, cost: float, both: bool) -> list[dict]:
    opens = {}
    for b in bars:
        t = datetime.fromtimestamp(b["datetime"] / 1000, timezone.utc)
        if t.minute == 0:
            opens[(t.date().isoformat(), t.hour)] = (t.weekday(), b["open"])
    days = sorted({d for d, _ in opens})
    trades, sign = [], usd_sign(pair)
    for k, d in enumerate(days):
        a, b = opens.get((d, US_OPEN)), opens.get((d, US_CLOSE))
        if a and b and a[0] < 5:
            gross = -sign * (b[1] / a[1] - 1)                                     # short USD in US hours
            trades.append({"pair": pair, "day": d, "leg": "us", "gross": gross, "net": gross - cost})
        if both and b and b[0] < 4 and k + 1 < len(days):                         # Mon-Thu evenings only
            nxt = opens.get((days[k + 1], US_OPEN))
            if nxt and nxt[0] == b[0] + 1:
                gross = sign * (nxt[1] / b[1] - 1)                                # long USD outside US hours
                trades.append({"pair": pair, "day": d, "leg": "rest", "gross": gross, "net": gross - 1.5 * cost})
    return trades


def bootstrap_p(trades: list[dict], runs: int = 4000, seed: int = 11) -> float:
    by_day: dict[str, list[float]] = {}
    for t in trades:
        by_day.setdefault(t["day"], []).append(t["net"])
    days = list(by_day)
    rng = random.Random(seed)
    return sum(sum(x for _ in days for x in by_day[rng.choice(days)]) > 0 for _ in range(runs)) / runs


def evaluate(trades: list[dict]) -> dict:
    net = [t["net"] for t in trades]
    gains, losses = sum(x for x in net if x > 0), -sum(x for x in net if x < 0)
    halves = [[t["net"] for t in trades if t["day"] < SPLIT], [t["net"] for t in trades if t["day"] >= SPLIT]]
    per_pair: dict[str, float] = {}
    for t in trades:
        per_pair[t["pair"]] = per_pair.get(t["pair"], 0.0) + t["net"]
    s = {"trades": len(net), "hit_rate": round(sum(x > 0 for x in net) / len(net), 3),
         "mean_gross_bp": round(sum(t["gross"] for t in trades) / len(net) * 1e4, 3),
         "mean_net_bp": round(sum(net) / len(net) * 1e4, 3), "profit_factor": round(gains / losses, 3),
         "p_mean_positive": bootstrap_p(trades),
         "halves_mean_bp": [round(sum(h) / len(h) * 1e4, 3) if h else None for h in halves],
         "pairs_positive": sum(v > 0 for v in per_pair.values()),
         "per_pair_total_bp": {k: round(v * 1e4, 1) for k, v in per_pair.items()}}
    reasons = []
    if s["trades"] < GATE["min_trades"]:
        reasons.append("too few trades")
    if s["p_mean_positive"] < GATE["min_p"]:
        reasons.append(f"P {s['p_mean_positive']:.3f} < {GATE['min_p']}")
    if s["profit_factor"] < GATE["min_pf"]:
        reasons.append(f"PF {s['profit_factor']} < {GATE['min_pf']}")
    if not all(halves) or min(sum(h) for h in halves) <= 0:
        reasons.append("not positive in both halves")
    if s["pairs_positive"] < GATE["min_positive"]:
        reasons.append(f"only {s['pairs_positive']}/7 pairs positive")
    return {**s, "passes": not reasons, "reasons": reasons}


def main() -> None:
    data = {p: load_yahoo(MARKETS[p], "1h") for p in PAIRS}
    report = {}
    for name, both in (("SF1", False), ("SF2", True)):
        trades = []
        for p, bars in data.items():
            cost = cost_fraction(MARKETS[p], sorted(b["close"] for b in bars)[len(bars) // 2])
            trades += session_trades(bars, p, cost, both)
        report[name] = evaluate(trades)
        print(f"{name}: {json.dumps(report[name])}")
    Path("research_output").mkdir(exist_ok=True)
    Path("research_output/session_flow_study.json").write_text(json.dumps(
        {"generated_at": datetime.now(timezone.utc).isoformat(), "gate": GATE, "report": report}, indent=2) + "\n")


if __name__ == "__main__":
    main()
