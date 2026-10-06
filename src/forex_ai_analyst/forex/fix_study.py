"""Pre-registered month-end 4pm London fix reversal study (docs/FIX_STUDY.md).

    python3 -m forex_ai_analyst.forex.fix_study           (downloads Dukascopy minutes on first run)
"""
from __future__ import annotations

import calendar
import json
import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from forex_ai_analyst.forex import dukascopy

PAIRS = ("EURUSD", "GBPUSD", "USDJPY", "AUDUSD")
LONDON = ZoneInfo("Europe/London")
FIRST, LAST = (2016, 1), (2026, 9)
MARKUP = 0.00005
THRESHOLD = 0.0010
SPLIT = "2021-01-01"
GATE = {"min_trades": 150, "min_p": 0.95, "min_pf": 1.2, "min_positive": 3}


def month_ends() -> list[date]:
    out, (y, m) = [], FIRST
    while (y, m) <= LAST:
        d = date(y, m, calendar.monthrange(y, m)[1])
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        out.append(d)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def next_weekday(d: date) -> date:
    d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def at(candles: dict[int, dict], day: date, hh: int, mm: int) -> dict | None:
    """The minute candle that opens at hh:mm London time on `day`."""
    local = datetime(day.year, day.month, day.day, hh, mm, tzinfo=LONDON)
    return candles.get(int(local.astimezone(timezone.utc).timestamp() * 1000))


def mid(c: dict) -> float:
    return (c["bid_open"] + c["ask_open"]) / 2


def trade(pair: str, day: date, exit_day: date, load=dukascopy.minutes) -> dict | None:
    today = {c["datetime"]: c for c in load(pair, day)}
    nxt = {c["datetime"]: c for c in load(pair, exit_day)}
    a, b, entry_c, exit_c = at(today, day, 15, 0), at(today, day, 15, 55), at(today, day, 16, 3), at(nxt, exit_day, 12, 0)
    if None in (a, b, entry_c, exit_c):
        return None
    move = mid(b) / mid(a) - 1
    if move == 0:
        return None
    side = -1 if move > 0 else 1
    entry = entry_c["ask_open"] if side > 0 else entry_c["bid_open"]
    exit_ = exit_c["bid_open"] if side > 0 else exit_c["ask_open"]
    net = side * (exit_ / entry - 1) - MARKUP
    return {"pair": pair, "day": day.isoformat(), "month": day.isoformat()[:7], "move": move, "side": side,
            "net": net}


def bootstrap_p(trades: list[dict], runs: int = 4000, seed: int = 11) -> float:
    by_month: dict[str, list[float]] = {}
    for t in trades:
        by_month.setdefault(t["month"], []).append(t["net"])
    months = list(by_month)
    rng = random.Random(seed)
    return sum(sum(x for _ in months for x in by_month[rng.choice(months)]) > 0 for _ in range(runs)) / runs


def evaluate(trades: list[dict]) -> dict:
    if not trades:
        return {"trades": 0, "passes": False, "reasons": ["no trades"]}
    net = [t["net"] for t in trades]
    gains, losses = sum(x for x in net if x > 0), -sum(x for x in net if x < 0)
    halves = [[t["net"] for t in trades if t["day"] < SPLIT], [t["net"] for t in trades if t["day"] >= SPLIT]]
    per_pair: dict[str, float] = {}
    for t in trades:
        per_pair[t["pair"]] = per_pair.get(t["pair"], 0.0) + t["net"]
    s = {"trades": len(net), "hit_rate": round(sum(x > 0 for x in net) / len(net), 3),
         "mean_net_bp": round(sum(net) / len(net) * 1e4, 2),
         "profit_factor": round(gains / losses, 3) if losses else None, "p_mean_positive": bootstrap_p(trades),
         "halves_mean_bp": [round(sum(h) / len(h) * 1e4, 2) if h else None for h in halves],
         "pairs_positive": sum(v > 0 for v in per_pair.values()),
         "per_pair_total_bp": {k: round(v * 1e4, 1) for k, v in per_pair.items()}}
    reasons = []
    if s["trades"] < GATE["min_trades"]:
        reasons.append("too few trades")
    if s["p_mean_positive"] < GATE["min_p"]:
        reasons.append(f"P {s['p_mean_positive']:.3f} < {GATE['min_p']}")
    if (s["profit_factor"] or 0) < GATE["min_pf"]:
        reasons.append(f"PF {s['profit_factor']} < {GATE['min_pf']}")
    if not all(halves) or min(sum(h) for h in halves) <= 0:
        reasons.append("not positive in both halves")
    if s["pairs_positive"] < GATE["min_positive"]:
        reasons.append(f"only {s['pairs_positive']}/4 pairs positive")
    return {**s, "passes": not reasons, "reasons": reasons}


def main() -> None:
    trades = []
    for d in month_ends():
        for pair in PAIRS:
            t = trade(pair, d, next_weekday(d))
            if t:
                trades.append(t)
    report = {"FIX1": evaluate(trades), "FIX2": evaluate([t for t in trades if abs(t["move"]) >= THRESHOLD])}
    for name, r in report.items():
        print(f"{name}: {json.dumps(r)}")
    Path("research_output").mkdir(exist_ok=True)
    Path("research_output/fix_study.json").write_text(json.dumps(
        {"generated_at": datetime.now(timezone.utc).isoformat(), "gate": GATE, "report": report}, indent=2) + "\n")


if __name__ == "__main__":
    main()
