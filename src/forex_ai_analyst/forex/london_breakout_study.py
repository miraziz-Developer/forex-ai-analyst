"""London-open breakout of the Asian range (docs/LONDON_BREAKOUT_STUDY.md).

    python3 -m forex_ai_analyst.forex.london_breakout_study
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from pathlib import Path

from forex_ai_analyst.forex import h1_ml_study as h1

MAIN, SPLIT, MARKUP = ("EURUSD", "GBPUSD"), "2018-01-01", 0.00003
PIP = {"USDJPY": 0.01, "EURJPY": 0.01, "GBPJPY": 0.01}


def utc(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, timezone.utc)


def day_trades(name: str, bars: list[dict]) -> list[dict]:
    by_day: dict[str, dict[int, dict]] = {}
    for b in bars:
        t = utc(b["datetime"])
        if t.weekday() < 5:
            by_day.setdefault(t.date().isoformat(), {})[t.hour] = b
    pip, out = PIP.get(name, 0.0001), []
    for day, hours in sorted(by_day.items()):
        asia = [hours.get(h) for h in range(7)]
        if None in asia or 16 not in hours:
            continue
        hi = max((b["bid_high"] + b["ask_high"]) / 2 for b in asia)
        lo = min((b["bid_low"] + b["ask_low"]) / 2 for b in asia)
        width = hi - lo
        if width <= 0:
            continue
        buy_at, sell_at = hi + 0.5 * pip, lo - 0.5 * pip
        side = entry = None
        h = 7
        while h <= 15 and side is None:
            b = hours.get(h)
            if b is not None:
                up, down = b["ask_high"] >= buy_at, b["bid_low"] <= sell_at
                if up and down:
                    break                                     # both sides in one bar: skip the day
                if up:
                    side, entry = 1, buy_at
                elif down:
                    side, entry = -1, sell_at
            h += 1
        if side is None:
            continue
        stop, target = (lo, entry + width) if side > 0 else (hi, entry - width)
        exit_ = None
        for k in range(h - 1, 16):                            # the entry bar itself can also hit stop/target
            b = hours.get(k)
            if b is None:
                continue
            hit_stop = b["bid_low"] <= stop if side > 0 else b["ask_high"] >= stop
            hit_target = b["bid_high"] >= target if side > 0 else b["ask_low"] <= target
            if hit_stop:
                exit_ = stop
                break
            if hit_target:
                exit_ = target
                break
        if exit_ is None:
            last = hours[16]
            exit_ = last["bid_open"] if side > 0 else last["ask_open"]
        net = side * (exit_ / entry - 1) - MARKUP
        out.append({"market": name, "day": day, "net": net, "r": side * (exit_ - entry) / width})
    return out


def evaluate(trades: list[dict], markets: tuple[str, ...]) -> dict:
    net = [t["net"] for t in trades]
    gains, losses = sum(x for x in net if x > 0), -sum(x for x in net if x < 0)
    halves = [[t["net"] for t in trades if t["day"] < SPLIT], [t["net"] for t in trades if t["day"] >= SPLIT]]
    per = {m: sum(t["net"] for t in trades if t["market"] == m) for m in markets}
    by_day: dict[str, float] = {}
    for t in trades:
        by_day[t["day"]] = by_day.get(t["day"], 0.0) + t["net"]
    vals, rng = list(by_day.values()), random.Random(11)
    p = sum(sum(rng.choice(vals) for _ in vals) > 0 for _ in range(2000)) / 2000
    return {"trades": len(net), "hit_rate": round(sum(x > 0 for x in net) / len(net), 3),
            "mean_net_bp": round(sum(net) / len(net) * 1e4, 2), "mean_r": round(sum(t["r"] for t in trades) / len(net), 3),
            "profit_factor": round(gains / losses, 3), "p_mean_positive": p,
            "halves_mean_bp": [round(sum(x) / len(x) * 1e4, 2) for x in halves],
            "positive": sum(v > 0 for v in per.values()), "per_market_total_pct": {k: round(v * 100, 1) for k, v in per.items()}}


def main() -> None:
    data = {m: h1.load(m) for m in h1.MARKETS}
    main_r = evaluate([t for m in MAIN for t in day_trades(m, data[m])], MAIN)
    others = tuple(m for m in h1.MARKETS if m not in MAIN)
    rep = evaluate([t for m in others for t in day_trades(m, data[m])], others)
    reasons = []
    if main_r["trades"] < 500:
        reasons.append("too few trades")
    if main_r["p_mean_positive"] < 0.95:
        reasons.append(f"P {main_r['p_mean_positive']:.3f} < 0.95")
    if main_r["profit_factor"] < 1.2:
        reasons.append(f"PF {main_r['profit_factor']} < 1.2")
    if min(main_r["halves_mean_bp"]) <= 0:
        reasons.append("not positive in both periods")
    if main_r["positive"] < 2:
        reasons.append("not both pairs positive")
    if rep["mean_net_bp"] <= 0 or rep["positive"] < 5:
        reasons.append("replication fails")
    report = {"main": main_r, "replication": rep, "passes": not reasons, "reasons": reasons}
    print(json.dumps(report))
    Path("research_output").mkdir(exist_ok=True)
    Path("research_output/london_breakout_study.json").write_text(json.dumps(
        {"generated_at": datetime.now(timezone.utc).isoformat(), "report": report}, indent=2) + "\n")


if __name__ == "__main__":
    main()
