"""Event study: what happens after an event, before any trading rule exists.

Entry reference is the next bar's open (never the signal close). Returns are
signed in the event's direction. The primary horizon for the gate is fixed in
advance (PRIMARY_HORIZON); other horizons are descriptive only. Bootstrap
resamples UTC days (clusters), because events on the same day across pairs
are not independent.
"""
from __future__ import annotations

import random
from datetime import datetime, timezone
from statistics import mean, median

HORIZONS = {"15m": 1, "30m": 2, "1h": 4, "2h": 8, "4h": 16, "8h": 32, "24h": 96}
PRIMARY_HORIZON = "4h"
MFE_BARS = 96


def outcome(frame, i: int, direction: str) -> dict | None:
    """Forward behaviour after the event at bar i; None without a full 24h of future bars."""
    if i + MFE_BARS >= len(frame):
        return None
    sign = 1 if direction == "LONG" else -1
    entry = frame.open[i + 1]
    row = {f"ret_{name}": sign * (frame.close[i + bars] / entry - 1) for name, bars in HORIZONS.items()}
    highs, lows = frame.high[i + 1:i + 1 + MFE_BARS], frame.low[i + 1:i + 1 + MFE_BARS]
    up = [h / entry - 1 for h in highs]
    down = [l / entry - 1 for l in lows]
    favorable, adverse = (up, down) if sign > 0 else ([-d for d in down], [-u for u in up])
    # MFE >= 0 and MAE <= 0 by definition: a trade that never went against you has MAE 0.
    best, worst = max(favorable), min(adverse)
    row["mfe"], row["mae"] = max(best, 0.0), min(worst, 0.0)
    row["bars_to_mfe"] = favorable.index(best) + 1 if best > 0 else None
    row["bars_to_mae"] = adverse.index(worst) + 1 if worst < 0 else None
    return row


def _day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).date().isoformat()


def cluster_bootstrap(rows: list[dict], key: str, runs: int = 2000, seed: int = 11) -> dict:
    """P(median > 0) and a 90% interval for the median, resampling UTC days."""
    by_day: dict[str, list[float]] = {}
    for row in rows:
        by_day.setdefault(_day(row["decision_time_ms"]), []).append(row[key])
    days = list(by_day)
    if len(days) < 5:
        return {"prob_positive": None, "median_p5": None, "median_p95": None}
    rng, medians = random.Random(seed), []
    for _ in range(runs):
        sample = [v for _ in days for v in by_day[rng.choice(days)]]
        medians.append(median(sample))
    medians.sort()
    return {"prob_positive": round(sum(m > 0 for m in medians) / runs, 4),
            "median_p5": medians[int(0.05 * (runs - 1))], "median_p95": medians[int(0.95 * (runs - 1))]}


def summarize(rows: list[dict], gate: dict, round_trip_cost: float) -> dict:
    key = f"ret_{PRIMARY_HORIZON}"
    out = {"events": len(rows), "pairs": sorted({r["pair"] for r in rows})}
    if not rows:
        out["gate"] = {"verdict": "INSUFFICIENT_EVIDENCE", "reasons": ["no events"]}
        return out
    out["horizons"] = {name: {"median": median(r[f"ret_{name}"] for r in rows),
                              "mean": mean(r[f"ret_{name}"] for r in rows),
                              "win_share": sum(r[f"ret_{name}"] > 0 for r in rows) / len(rows)} for name in HORIZONS}
    out["mfe_median"], out["mae_median"] = median(r["mfe"] for r in rows), median(r["mae"] for r in rows)
    out["bootstrap"] = cluster_bootstrap(rows, key)
    by_pair, by_year = {}, {}
    for r in rows:
        by_pair.setdefault(r["pair"], []).append(r[key])
        by_year.setdefault(_day(r["decision_time_ms"])[:4], []).append(r[key])
    out["by_pair"] = {p: {"events": len(v), "median": median(v)} for p, v in sorted(by_pair.items())}
    out["by_year"] = {y: {"events": len(v), "median": median(v)} for y, v in sorted(by_year.items())}
    out["leave_one_pair_out"] = {p: median([r[key] for r in rows if r["pair"] != p])
                                 for p in by_pair if any(r["pair"] != p for r in rows)}

    if len(rows) < gate["min_discovery_events"]:
        out["gate"] = {"verdict": "INSUFFICIENT_EVIDENCE",
                       "reasons": [f"{len(rows)} events < {gate['min_discovery_events']}"]}
        return out
    reasons = []
    primary = out["horizons"][PRIMARY_HORIZON]["median"]
    if primary <= 0:
        reasons.append(f"median {PRIMARY_HORIZON} return {primary:.5f} is not in the expected direction")
    if primary <= round_trip_cost:
        reasons.append(f"median {PRIMARY_HORIZON} return {primary:.5f} <= round-trip cost {round_trip_cost:.5f}")
    prob = out["bootstrap"]["prob_positive"]
    if prob is None or prob < gate["min_bootstrap_positive_probability"]:
        reasons.append(f"bootstrap P(median>0)={prob} < {gate['min_bootstrap_positive_probability']}")
    supportive = [p for p, s in out["by_pair"].items() if s["events"] >= 5 and s["median"] > 0]
    if len(supportive) < gate["min_pairs"]:
        reasons.append(f"only {len(supportive)} pairs with >=5 events show the effect (< {gate['min_pairs']})")
    if gate.get("leave_one_pair_out_must_hold") and any(v <= 0 for v in out["leave_one_pair_out"].values()):
        reasons.append("effect disappears when a single pair is removed")
    out["gate"] = {"verdict": "PASS" if not reasons else "REJECTED", "reasons": reasons}
    return out
