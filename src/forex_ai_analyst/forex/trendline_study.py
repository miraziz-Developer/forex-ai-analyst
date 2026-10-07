"""Automated trendline breakout on Dukascopy H1 bid/ask (docs/TRENDLINE_STUDY.md).

    python3 -m forex_ai_analyst.forex.trendline_study
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from pathlib import Path

from forex_ai_analyst.forex import h1_ml_study as h1

K, HOLD, MARKUP, SPLIT = 5, 120, 0.00003, "2018-01-01"


def pivots(values: list[float], high: bool) -> list[int]:
    """Indices of swing highs (or lows); pivot j becomes known at bar j + K."""
    out = []
    for j in range(K, len(values) - K):
        left, right = values[j - K:j], values[j + 1:j + K + 1]
        if (high and values[j] > max(left) and values[j] >= max(right)) or \
                (not high and values[j] < min(left) and values[j] <= min(right)):
            out.append(j)
    return out


def trades_for(name: str, bars: list[dict]) -> list[dict]:
    mid = {k: [(b[f"bid_{k}"] + b[f"ask_{k}"]) / 2 for b in bars] for k in ("open", "high", "low", "close")}
    highs, lows = pivots(mid["high"], True), pivots(mid["low"], False)
    out, used, i, hi_ptr, lo_ptr = [], set(), 2 * K + 2, 0, 0
    known_h, known_l = [], []
    n = len(bars)
    while i < n - 2:
        while hi_ptr < len(highs) and highs[hi_ptr] + K <= i:
            known_h.append(highs[hi_ptr])
            hi_ptr += 1
        while lo_ptr < len(lows) and lows[lo_ptr] + K <= i:
            known_l.append(lows[lo_ptr])
            lo_ptr += 1
        signal = None
        for side, known, key in ((1, known_h, "high"), (-1, known_l, "low")):
            if len(known) < 2:
                continue
            j1, j2 = known[-2], known[-1]
            p1, p2 = mid[key][j1], mid[key][j2]
            if (side > 0 and p2 >= p1) or (side < 0 and p2 <= p1) or (side, j1, j2) in used:
                continue
            slope = (p2 - p1) / (j2 - j1)
            line_now, line_prev = p2 + slope * (i - j2), p2 + slope * (i - 1 - j2)
            c, cp = mid["close"][i], mid["close"][i - 1]
            if (side > 0 and c > line_now and cp <= line_prev) or (side < 0 and c < line_now and cp >= line_prev):
                stop = min(mid["low"][j2:i + 1]) if side > 0 else max(mid["high"][j2:i + 1])
                signal = (side, stop, p1, (side, j1, j2))
                break
        if signal is None:
            i += 1
            continue
        side, stop, target, key = signal
        used.add(key)
        e = i + 1
        entry = bars[e]["ask_open"] if side > 0 else bars[e]["bid_open"]
        if side * (entry - stop) <= 0 or side * (target - entry) <= 0:
            i += 1
            continue
        exit_, x = None, e
        for x in range(e, min(e + HOLD, n)):
            b = bars[x]
            if side > 0:
                if b["bid_low"] <= stop:
                    exit_ = stop
                    break
                if b["bid_high"] >= target:
                    exit_ = target
                    break
            else:
                if b["ask_high"] >= stop:
                    exit_ = stop
                    break
                if b["ask_low"] <= target:
                    exit_ = target
                    break
        if exit_ is None:
            x = min(e + HOLD, n - 1)
            exit_ = bars[x]["bid_open"] if side > 0 else bars[x]["ask_open"]
        net = side * (exit_ / entry - 1) - MARKUP
        day = datetime.fromtimestamp(bars[e]["datetime"] / 1000, timezone.utc).date().isoformat()
        out.append({"market": name, "day": day, "side": side, "net": net,
                    "r": side * (exit_ - entry) / abs(entry - stop)})
        i = x + 1
    return out


def evaluate(trades: list[dict]) -> dict:
    net = [t["net"] for t in trades]
    gains, losses = sum(x for x in net if x > 0), -sum(x for x in net if x < 0)
    halves = [[t["net"] for t in trades if t["day"] < SPLIT], [t["net"] for t in trades if t["day"] >= SPLIT]]
    per: dict[str, float] = {}
    by_day: dict[str, float] = {}
    for t in trades:
        per[t["market"]] = per.get(t["market"], 0.0) + t["net"]
        by_day[t["day"]] = by_day.get(t["day"], 0.0) + t["net"]
    vals, rng = list(by_day.values()), random.Random(11)
    p = sum(sum(rng.choice(vals) for _ in vals) > 0 for _ in range(2000)) / 2000
    s = {"trades": len(net), "hit_rate": round(sum(x > 0 for x in net) / len(net), 3),
         "mean_net_bp": round(sum(net) / len(net) * 1e4, 2), "mean_r": round(sum(t["r"] for t in trades) / len(net), 3),
         "profit_factor": round(gains / losses, 3), "p_mean_positive": p,
         "halves_mean_bp": [round(sum(h) / len(h) * 1e4, 2) for h in halves],
         "pairs_positive": sum(v > 0 for v in per.values()),
         "per_pair_total_pct": {k: round(v * 100, 1) for k, v in per.items()}}
    reasons = []
    if s["trades"] < 500:
        reasons.append("too few trades")
    if p < 0.95:
        reasons.append(f"P {p:.3f} < 0.95")
    if s["profit_factor"] < 1.2:
        reasons.append(f"PF {s['profit_factor']} < 1.2")
    if min(sum(h) for h in halves) <= 0:
        reasons.append("not positive in both halves")
    if s["pairs_positive"] < 6:
        reasons.append(f"only {s['pairs_positive']}/10 pairs positive")
    return {**s, "passes": not reasons, "reasons": reasons}


def main() -> None:
    trades = [t for m in h1.MARKETS for t in trades_for(m, h1.load(m))]
    report = evaluate(trades)
    print(json.dumps(report))
    Path("research_output").mkdir(exist_ok=True)
    Path("research_output/trendline_study.json").write_text(json.dumps(
        {"generated_at": datetime.now(timezone.utc).isoformat(), "report": report}, indent=2) + "\n")


if __name__ == "__main__":
    main()
