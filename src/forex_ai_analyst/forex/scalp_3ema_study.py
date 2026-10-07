"""ForexFactory "3 EMA scalping" on Dukascopy minute bid/ask (docs/SCALP_3EMA_STUDY.md).

    python3 -m forex_ai_analyst.forex.scalp_3ema_study            development period, base rule
    python3 -m forex_ai_analyst.forex.scalp_3ema_study --final V  holdout, one named version, once
"""
from __future__ import annotations

import json
import random
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from forex_ai_analyst.forex import dukascopy

PAIRS = ("EURUSD", "GBPUSD", "USDCHF")
PIP, MARKUP = 0.0001, 0.00003
DEV, HOLDOUT = (date(2024, 10, 1), date(2025, 9, 30)), (date(2025, 10, 1), date(2026, 9, 30))
M1, M5, H1 = 60_000, 300_000, 3_600_000


@dataclass(frozen=True)
class Params:
    emas: tuple = (8, 13, 21)
    lookback: int = 5
    offset_pips: float = 3.0
    rr: float = 1.0
    expiry_minutes: int = 60
    m5_aligned: bool = False                 # also require the M5 EMAs stacked the same way
    hours_utc: tuple | None = None           # allowed decision hours (UTC); None = all
    min_range_pips: float = 0.0              # five-candle range must be at least this
    max_spread_share: float = 1.0            # skip when spread > this share of the stop distance
    notes: tuple = field(default=())


VERSIONS = {"base": Params()}


def minutes(pair: str, first: date, last: date) -> list[dict]:
    out, d = [], first
    while d <= last:
        if d.weekday() < 5:
            out += dukascopy.minutes(pair, d)
        d += timedelta(days=1)
    # minutes without ticks are flat fillers at the last price: they would fake zero volatility (holidays)
    return [b for b in out if b["bid_high"] >= b["bid_low"] and b.get("bid_volume", 1) > 0]


def ema(values: list[float], n: int) -> list[float]:
    k, out, v = 2 / (n + 1), [], values[0]
    for x in values:
        v = x * k + v * (1 - k)
        out.append(v)
    return out


def stacked(fast: float, mid: float, slow: float) -> int:
    return 1 if fast > mid > slow else -1 if fast < mid < slow else 0


def aggregate(m1: list[dict], width: int) -> list[dict]:
    groups: dict[int, list[dict]] = {}
    for b in m1:
        groups.setdefault(b["datetime"] // width * width, []).append(b)
    out = []
    for start in sorted(groups):
        g = groups[start]
        out.append({"start": start, "end": start + width, "open": (g[0]["bid_open"] + g[0]["ask_open"]) / 2,
                    "high": max((b["bid_high"] + b["ask_high"]) / 2 for b in g),
                    "low": min((b["bid_low"] + b["ask_low"]) / 2 for b in g),
                    "close": (g[-1]["bid_close"] + g[-1]["ask_close"]) / 2})
    return out


def trend_by_time(bars: list[dict], emas: tuple) -> tuple[list[int], list[int]]:
    closes = [b["close"] for b in bars]
    e = [ema(closes, n) for n in emas]
    return [b["end"] for b in bars], [stacked(e[0][i], e[1][i], e[2][i]) for i in range(len(bars))]


def simulate(pair: str, m1: list[dict], p: Params) -> list[dict]:
    from bisect import bisect_right
    m5 = aggregate(m1, M5)
    h1_end, h1_trend = trend_by_time(aggregate(m1, H1), p.emas)
    m5_end, m5_trend = trend_by_time(m5, p.emas)
    times = [b["datetime"] for b in m1]
    trades, k, busy_until = [], p.lookback, 0
    while k < len(m5):
        bar = m5[k]
        if bar["end"] <= busy_until or bar["end"] - m5[k - p.lookback + 1]["start"] != p.lookback * M5:
            k += 1
            continue
        j = bisect_right(h1_end, bar["end"]) - 1
        side = h1_trend[j] if j >= 0 else 0
        hour = datetime.fromtimestamp(bar["end"] / 1000, timezone.utc).hour
        if side == 0 or (p.m5_aligned and m5_trend[k] != side) or (p.hours_utc and hour not in p.hours_utc):
            k += 1
            continue
        window = m5[k - p.lookback + 1:k + 1]
        hi, lo = max(b["high"] for b in window), min(b["low"] for b in window)
        if (hi - lo) / PIP < p.min_range_pips:
            k += 1
            continue
        off = p.offset_pips * PIP
        trigger, stop = (hi + off, lo - off) if side > 0 else (lo - off, hi + off)
        target = trigger + side * p.rr * abs(trigger - stop)
        i = bisect_right(times, bar["end"] - 1)            # first minute after the decision
        if i >= len(m1):
            break
        spread = m1[i]["ask_open"] - m1[i]["bid_open"]
        if spread > p.max_spread_share * abs(trigger - stop):
            k += 1
            continue
        expiry, entry, fill_i = bar["end"] + p.expiry_minutes * M1, None, None
        while i < len(m1) and times[i] < expiry:
            b = m1[i]
            if side > 0 and b["ask_high"] >= trigger:
                entry, fill_i = max(trigger, b["ask_open"]), i
                break
            if side < 0 and b["bid_low"] <= trigger:
                entry, fill_i = min(trigger, b["bid_open"]), i
                break
            i += 1
        if entry is None:
            busy_until = expiry
            k += 1
            continue
        exit_ = None
        for x in range(fill_i, len(m1)):
            b = m1[x]
            if side > 0:
                if b["bid_low"] <= stop:
                    exit_ = min(stop, b["bid_open"]) if x > fill_i else stop
                elif b["bid_high"] >= target:
                    exit_ = target
            else:
                if b["ask_high"] >= stop:
                    exit_ = max(stop, b["ask_open"]) if x > fill_i else stop
                elif b["ask_low"] <= target:
                    exit_ = target
            if exit_ is not None:
                break
        if exit_ is None:
            break
        net = side * (exit_ / entry - 1) - MARKUP
        t = datetime.fromtimestamp(m1[fill_i]["datetime"] / 1000, timezone.utc)
        trades.append({"pair": pair, "day": t.date().isoformat(), "hour": t.hour, "side": side, "net": net,
                       "r": side * (exit_ - entry) / abs(entry - stop), "risk_pips": abs(entry - stop) / PIP,
                       "spread_pips": spread / PIP, "minutes": (m1[x]["datetime"] - m1[fill_i]["datetime"]) // M1,
                       "entry_ms": m1[fill_i]["datetime"]})
        busy_until = m1[x]["datetime"]
        k += 1
    return trades


def evaluate(trades: list[dict]) -> dict:
    if not trades:
        return {"trades": 0}
    net = [t["net"] for t in trades]
    gains, losses = sum(x for x in net if x > 0), -sum(x for x in net if x < 0)
    by_day: dict[str, float] = {}
    per: dict[str, float] = {}
    for t in trades:
        by_day[t["day"]] = by_day.get(t["day"], 0.0) + t["net"]
        per[t["pair"]] = per.get(t["pair"], 0.0) + t["net"]
    vals, rng = list(by_day.values()), random.Random(11)
    p = sum(sum(rng.choice(vals) for _ in vals) > 0 for _ in range(2000)) / 2000
    return {"trades": len(net), "hit_rate": round(sum(x > 0 for x in net) / len(net), 3),
            "mean_r": round(sum(t["r"] for t in trades) / len(net), 3),
            "mean_net_bp": round(sum(net) / len(net) * 1e4, 2), "profit_factor": round(gains / losses, 3) if losses else None,
            "p_mean_positive": p, "avg_risk_pips": round(sum(t["risk_pips"] for t in trades) / len(net), 1),
            "avg_spread_pips": round(sum(t["spread_pips"] for t in trades) / len(net), 2),
            "pairs_positive": sum(v > 0 for v in per.values()),
            "per_pair_total_pct": {k: round(v * 100, 2) for k, v in per.items()}}


def run(version: str, period: tuple[date, date]) -> tuple[dict, list[dict]]:
    p = VERSIONS[version]
    trades = [t for pair in PAIRS for t in simulate(pair, minutes(pair, *period), p)]
    return evaluate(trades), trades


def main() -> None:
    if "--final" in sys.argv:
        version = sys.argv[sys.argv.index("--final") + 1]
        report, _ = run(version, HOLDOUT)
        gate = (report["trades"] >= 300 and report["p_mean_positive"] >= 0.95
                and (report["profit_factor"] or 0) >= 1.2 and report["pairs_positive"] >= 2)
        out = {"version": version, "params": asdict(VERSIONS[version]), "holdout": report, "passes": gate,
               "versions_tried_on_development": list(VERSIONS)}
        Path("research_output/scalp_3ema_final.json").write_text(json.dumps(out, indent=2) + "\n")
        print(json.dumps(out))
        return
    out = {}
    for version in VERSIONS:
        out[version], _ = run(version, DEV)
        print(version, json.dumps(out[version]))
    Path("research_output").mkdir(exist_ok=True)
    Path("research_output/scalp_3ema_development.json").write_text(json.dumps(out, indent=2) + "\n")


if __name__ == "__main__":
    main()
