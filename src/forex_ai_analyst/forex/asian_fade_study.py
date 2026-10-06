"""Asian-session fade on Dukascopy hourly bid/ask (docs/ASIAN_FADE_STUDY.md).

    python3 -m forex_ai_analyst.forex.asian_fade_study
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from pathlib import Path
from statistics import pstdev

from forex_ai_analyst.forex import h1_ml_study as h1

ENTRY_CLOSE_HOURS = {0, 1, 2, 3, 4}        # UTC hour at which the signal bar closes
LAST_EXIT_HOUR = 6
FIRST_TEST = "2014-01-01"
SPLIT = "2020-01-01"
GATE = {"min_trades": 500, "min_p": 0.95, "min_pf": 1.2, "min_positive": 6}


def utc(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, timezone.utc)


def trades_for(name: str, bars: list[dict], k: float) -> list[dict]:
    mid = [(b["bid_close"] + b["ask_close"]) / 2 for b in bars]
    out, i = [], 20
    while i < len(bars) - 2:
        window = mid[i - 19:i + 1]
        if bars[i]["datetime"] - bars[i - 19]["datetime"] != 19 * h1.HOUR:
            i += 1
            continue
        mean, sd = sum(window) / 20, pstdev(window)
        close_at = utc(bars[i]["datetime"] + h1.HOUR)
        if sd <= 0 or close_at.hour not in ENTRY_CLOSE_HOURS or close_at.weekday() >= 5 \
                or utc(bars[i]["datetime"]).isoformat() < FIRST_TEST:
            i += 1
            continue
        side = -1 if mid[i] > mean + k * sd else 1 if mid[i] < mean - k * sd else 0
        if side == 0:
            i += 1
            continue
        e = i + 1
        if bars[e]["datetime"] != bars[i]["datetime"] + h1.HOUR:
            i += 1
            continue
        entry = bars[e]["ask_open"] if side > 0 else bars[e]["bid_open"]
        stop_dist = 2 * k * sd
        j = e
        while True:
            nxt = j + 1
            if nxt >= len(bars) or bars[nxt]["datetime"] != bars[j]["datetime"] + h1.HOUR:
                break                                       # data gap: leave at this bar's close below
            back = side * (mid[j] - mean) >= 0
            stopped = side * (entry - mid[j]) >= stop_dist
            late = utc(bars[nxt]["datetime"]).hour >= LAST_EXIT_HOUR and utc(bars[nxt]["datetime"]).hour < 12
            if back or stopped or late:
                break
            j = nxt
        x = j + 1 if j + 1 < len(bars) and bars[j + 1]["datetime"] == bars[j]["datetime"] + h1.HOUR else j
        exit_ = (bars[x]["bid_open"] if side > 0 else bars[x]["ask_open"]) if x != j else \
            (bars[j]["bid_close"] if side > 0 else bars[j]["ask_close"])
        net = side * (exit_ / entry - 1) - h1.MARKUP
        out.append({"market": name, "day": utc(bars[e]["datetime"]).date().isoformat(), "side": side, "net": net,
                    "hours": x - e})
        i = x
    return out


def bootstrap_p(trades: list[dict], runs: int = 2000, seed: int = 11) -> float:
    by_day: dict[str, float] = {}
    for t in trades:
        by_day[t["day"]] = by_day.get(t["day"], 0.0) + t["net"]
    values = list(by_day.values())
    rng = random.Random(seed)
    return sum(sum(rng.choice(values) for _ in values) > 0 for _ in range(runs)) / runs


def evaluate(trades: list[dict]) -> dict:
    if not trades:
        return {"trades": 0, "passes": False, "reasons": ["no trades"]}
    net = [t["net"] for t in trades]
    gains, losses = sum(x for x in net if x > 0), -sum(x for x in net if x < 0)
    halves = [[t["net"] for t in trades if t["day"] < SPLIT], [t["net"] for t in trades if t["day"] >= SPLIT]]
    per: dict[str, float] = {}
    for t in trades:
        per[t["market"]] = per.get(t["market"], 0.0) + t["net"]
    s = {"trades": len(net), "hit_rate": round(sum(x > 0 for x in net) / len(net), 3),
         "mean_net_bp": round(sum(net) / len(net) * 1e4, 3), "profit_factor": round(gains / losses, 3) if losses else None,
         "p_mean_positive": bootstrap_p(trades), "avg_hours": round(sum(t["hours"] for t in trades) / len(net), 2),
         "halves_mean_bp": [round(sum(h) / len(h) * 1e4, 3) if h else None for h in halves],
         "markets_positive": sum(v > 0 for v in per.values()),
         "per_market_total_pct": {k: round(v * 100, 1) for k, v in per.items()}}
    reasons = []
    if s["trades"] < GATE["min_trades"]:
        reasons.append("too few trades")
    if s["p_mean_positive"] < GATE["min_p"]:
        reasons.append(f"P {s['p_mean_positive']:.3f} < {GATE['min_p']}")
    if (s["profit_factor"] or 0) < GATE["min_pf"]:
        reasons.append(f"PF {s['profit_factor']} < {GATE['min_pf']}")
    if not all(halves) or min(sum(h) for h in halves) <= 0:
        reasons.append("not positive in both halves")
    if s["markets_positive"] < GATE["min_positive"]:
        reasons.append(f"only {s['markets_positive']}/10 markets positive")
    return {**s, "passes": not reasons, "reasons": reasons}


def main() -> None:
    h1.prefetch()
    data = {m: h1.load(m) for m in h1.MARKETS}
    report = {}
    for name, k in (("AF1", 2.0), ("AF2", 2.5)):
        report[name] = evaluate([t for m in h1.MARKETS for t in trades_for(m, data[m], k)])
        print(f"{name}: {json.dumps(report[name])}")
    Path("research_output").mkdir(exist_ok=True)
    Path("research_output/asian_fade_study.json").write_text(json.dumps(
        {"generated_at": datetime.now(timezone.utc).isoformat(), "gate": GATE, "report": report}, indent=2) + "\n")


if __name__ == "__main__":
    main()
