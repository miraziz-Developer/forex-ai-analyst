"""Quiet-market range rule for the MT5 indices and metals, H1 bid/ask (docs/QUIET_RANGE_STUDY.md).

    python3 -m forex_ai_analyst.forex.quiet_range_study            development 2013-2019: both variants
    python3 -m forex_ai_analyst.forex.quiet_range_study --holdout  the chosen variant, once, on 2020-2026
"""
from __future__ import annotations

import json
import random
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

from forex_ai_analyst.forex import dukascopy
from forex_ai_analyst.forex.index_study import sma
from forex_ai_analyst.lab.strategies import atr

dukascopy.POINT.update({"XAGUSD": 1e-3})
# MT5 name -> Dukascopy instrument
MARKETS = {"US500": "USA500IDXUSD", "NAS100": "USATECHIDXUSD", "US30": "USA30IDXUSD", "GER40": "DEUIDXEUR",
           "UK100": "GBRIDXGBP", "JP225": "JPNIDXJPY", "XAUUSD": "XAUUSD", "XAGUSD": "XAGUSD"}
EXTRA = {"XAGUSD": 0.0005}                  # on top of the real spread, round trip; indices and gold 0.02%
FIRST, LAST = (2013, 1), (2026, 9)
HOLDOUT_START = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
HOLDOUT_SPLIT = int(datetime(2023, 4, 1, tzinfo=timezone.utc).timestamp() * 1000)
BAND_N, BAND_K, HOLD, STOP_ATR = 20, 2.5, 24, 3.0
OUT = Path("research_output/quiet_range.json")
VARIANTS = {"QR1": True, "QR2": False}        # name -> quiet-regime filter on


def load(instrument: str) -> list[dict]:
    out, (y, m) = [], FIRST
    while (y, m) <= LAST:
        out += [h for h in dukascopy.hours(instrument, y, m) if h["bid_high"] > h["bid_low"]]
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def daily_context(bars: list[dict]) -> dict[str, tuple[bool, bool]]:
    """For each UTC day: (previous day's close above its 200-day average, previous day's 20-day realized
    volatility at or below the median of the 250 values before it). Both use completed days only."""
    days: dict[str, float] = {}
    for b in bars:
        days[datetime.fromtimestamp(b["datetime"] / 1000, timezone.utc).date().isoformat()] = \
            (b["bid_close"] + b["ask_close"]) / 2
    keys = sorted(days)
    closes = [days[k] for k in keys]
    avg = sma(closes, 200)
    rets = [0.0] + [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
    vol = [statistics.pstdev(rets[i - 19:i + 1]) if i >= 20 else None for i in range(len(rets))]
    out = {}
    for i in range(1, len(keys)):
        j = i - 1                                                  # the last completed day
        trend = avg[j] is not None and closes[j] > avg[j]
        past = [v for v in vol[max(0, j - 250):j] if v is not None]
        quiet = vol[j] is not None and len(past) >= 200 and vol[j] <= statistics.median(past)
        out[keys[i]] = (trend, quiet)
    return out


def trades_for(market: str, bars: list[dict], quiet_filter: bool) -> list[dict]:
    """In a daily uptrend (and, for QR1, a quiet regime): an hourly close below the 20-hour, 2.5-SD Bollinger
    band buys at the next open (ask); out at the next open after a close back at the middle band, or after
    24 hours; 3 ATR(14) stop on the bid, checked first. Long only, one position."""
    mid = [(b["bid_close"] + b["ask_close"]) / 2 for b in bars]
    hi = [(b["bid_high"] + b["ask_high"]) / 2 for b in bars]
    lo = [(b["bid_low"] + b["ask_low"]) / 2 for b in bars]
    a = atr([{"high": h, "low": low, "close": c} for h, low, c in zip(hi, lo, mid)], 14)
    middle = sma(mid, BAND_N)
    ctx = daily_context(bars)
    extra = EXTRA.get(market, 0.0002)
    out, pos = [], None

    def close(price: float, i: int, how: str) -> None:
        nonlocal pos
        entry, stop = pos["entry"], pos["stop"]
        gross = price - entry
        out.append({"market": market, "entry_ms": pos["ms"], "exit_ms": bars[i]["datetime"], "how": how,
                    "r": (gross - extra * entry) / (entry - stop), "r_gross": gross / (entry - stop)})
        pos = None

    for i in range(BAND_N, len(bars) - 1):
        b = bars[i]
        if pos is not None:
            if b["bid_low"] <= pos["stop"]:
                close(min(pos["stop"], b["bid_open"]), i, "stop")
            elif mid[i] >= middle[i] or i - pos["k"] + 1 >= HOLD:
                close(bars[i + 1]["bid_open"], i + 1, "middle" if mid[i] >= middle[i] else "time")
            if pos is not None:
                continue
        if a[i] is None or middle[i] is None:
            continue
        window = mid[i - BAND_N + 1:i + 1]
        lower = middle[i] - BAND_K * statistics.pstdev(window)
        day = datetime.fromtimestamp(b["datetime"] / 1000, timezone.utc).date().isoformat()
        trend, quiet = ctx.get(day, (False, False))
        if mid[i] < lower and trend and (quiet or not quiet_filter):
            nxt = bars[i + 1]
            pos = {"entry": nxt["ask_open"], "stop": nxt["ask_open"] - STOP_ATR * a[i], "ms": nxt["datetime"],
                   "k": i + 1}
    return out


def stats(rows: list[dict]) -> dict:
    if not rows:
        return {"trades": 0, "t_stat": None}
    r = [t["r"] for t in rows]
    mean = sum(r) / len(r)
    sd = statistics.stdev(r) if len(r) > 1 else 0.0
    gains, losses = sum(x for x in r if x > 0), -sum(x for x in r if x < 0)
    by_month: dict[str, float] = {}
    per: dict[str, float] = {}
    for t in rows:
        key = datetime.fromtimestamp(t["entry_ms"] / 1000, timezone.utc).strftime("%Y-%m")
        by_month[key] = by_month.get(key, 0.0) + t["r"]
        per[t["market"]] = per.get(t["market"], 0.0) + t["r"]
    vals, rng = list(by_month.values()), random.Random(7)
    p = sum(sum(rng.choice(vals) for _ in vals) > 0 for _ in range(2000)) / 2000
    return {"trades": len(r), "win_rate": round(sum(x > 0 for x in r) / len(r), 3), "mean_r": round(mean, 4),
            "mean_r_gross": round(sum(t["r_gross"] for t in rows) / len(r), 4),
            "profit_factor": round(gains / losses, 3) if losses else None, "p_mean_positive": p,
            "t_stat": round(mean / sd * len(r) ** 0.5, 2) if sd else None,
            "per_month": round(len(r) / max(1, len(by_month)), 1),
            "markets_positive": sum(x > 0 for x in per.values()),
            "per_market_total_r": {k: round(x, 1) for k, x in per.items()}}


def run(loaded: dict[str, list[dict]], quiet_filter: bool, holdout: bool) -> list[dict]:
    rows = [t for m, bars in loaded.items() for t in trades_for(m, bars, quiet_filter)]
    return [t for t in rows if (t["entry_ms"] >= HOLDOUT_START) == holdout]


def main() -> None:
    loaded = {m: load(instrument) for m, instrument in MARKETS.items()}
    Path("research_output").mkdir(exist_ok=True)
    if "--holdout" in sys.argv:
        saved = json.loads(OUT.read_text())
        if not saved.get("holdout_allowed"):
            raise SystemExit("development did not qualify: the holdout is not spent")
        rows = run(loaded, VARIANTS[saved["chosen"]], True)
        report = stats(rows)
        report["halves_mean_r"] = [stats([t for t in rows if t["entry_ms"] < HOLDOUT_SPLIT]).get("mean_r"),
                                   stats([t for t in rows if t["entry_ms"] >= HOLDOUT_SPLIT]).get("mean_r")]
        report["passes"] = (report["trades"] >= 300 and report["mean_r"] > 0 and report["p_mean_positive"] >= 0.95
                            and (report["profit_factor"] or 0) >= 1.2 and report["markets_positive"] >= 5
                            and all((x or 0) > 0 for x in report["halves_mean_r"]))
        saved["holdout"] = report
        OUT.write_text(json.dumps(saved, indent=2) + "\n")
        print(json.dumps(report, indent=1))
        return
    dev = {name: stats(run(loaded, flag, False)) for name, flag in VARIANTS.items()}
    for name, row in dev.items():
        print(name, json.dumps(row))
    chosen = max(dev, key=lambda n: dev[n]["t_stat"] if dev[n]["t_stat"] is not None else -1e9)
    allowed = (dev[chosen]["t_stat"] or 0) >= 2.0 and dev[chosen]["trades"] >= 300
    OUT.write_text(json.dumps({"development": dev, "chosen": chosen, "holdout_allowed": allowed}, indent=2) + "\n")
    print("chosen", chosen, "holdout allowed", allowed)


if __name__ == "__main__":
    main()
