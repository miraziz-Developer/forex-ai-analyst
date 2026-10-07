"""The crypto capitulation rebound carried to FX and metals on H1 bid/ask (docs/FX_REBOUND_STUDY.md).

    python3 -m forex_ai_analyst.forex.fx_rebound_study            development 2010-2018: every variant
    python3 -m forex_ai_analyst.forex.fx_rebound_study --holdout  the chosen variant, once, on 2019-2026
"""
from __future__ import annotations

import json
import random
import sys
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from forex_ai_analyst.forex import dukascopy
from forex_ai_analyst.forex import h1_ml_study as h1
from forex_ai_analyst.forex.classic_study import Book, mid
from forex_ai_analyst.forex.three_ma_study import MARKETS
from forex_ai_analyst.lab.strategies import atr

dukascopy.POINT.update({"XAGUSD": 1e-3})
HOLDOUT_START = int(datetime(2019, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
HOLDOUT_SPLIT = int(datetime(2023, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
WINDOW, VOL_HOURS, HOLD = 24, 720, 24
OUT = Path("research_output/fx_rebound.json")


@dataclass(frozen=True)
class Variant:
    name: str
    k: float = 3.0                    # the 24 h move must reach k standard deviations of 24 h returns
    sides: str = "both"               # "both" or "long" (buy crashes only)
    skip_rollover: bool = False       # R: no signal from bars opening 20:00-23:00 UTC (rollover spreads)
    target_fraction: float = 0.5
    stop_atr: float = 0.5


SINGLES = (Variant("base"), Variant("K2.5", k=2.5), Variant("K3.5", k=3.5), Variant("LONG", sides="long"),
           Variant("R", skip_rollover=True), Variant("T38", target_fraction=0.382))


def trades_for(market: str, bars: list[dict], v: Variant) -> list[dict]:
    """At an hour's close: a fall over the last 24 hours of at least k sigma and a green hour buys the next
    open (a rise and a red hour sells, unless long only). Stop 0.5 ATR(14) beyond the 24 h extreme; target a
    fraction of the move back; out at the open 24 hours after entry at the latest. One position per market."""
    o = [mid(b, "open") for b in bars]
    c = [mid(b, "close") for b in bars]
    hi = [mid(b, "high") for b in bars]
    lo = [mid(b, "low") for b in bars]
    a = atr([{"high": h, "low": low, "close": x} for h, low, x in zip(hi, lo, c)], 14)
    sq = [0.0, 0.0]                                    # running sum of squared hourly returns
    for i in range(1, len(c)):
        sq.append(sq[-1] + (c[i] / c[i - 1] - 1) ** 2)
    book, opened_at = Book(market, v.name), None
    for i in range(VOL_HOURS + WINDOW, len(bars) - 1):
        b = bars[i]
        if book.pos is not None:
            book.step(b)
            if book.pos is not None and i - opened_at + 1 >= HOLD:
                nxt = bars[i + 1]
                book.close(nxt["bid_open"] if book.pos["side"] > 0 else nxt["ask_open"], nxt["datetime"], "time")
            if book.pos is not None:
                continue
        if a[i] is None:
            continue
        if v.skip_rollover and datetime.fromtimestamp(b["datetime"] / 1000, timezone.utc).hour in (20, 21, 22, 23):
            continue
        # volatility of 24 h moves from the 720 hours before the current 24 h window
        sigma = ((sq[i - WINDOW + 1] - sq[i - WINDOW - VOL_HOURS + 1]) / VOL_HOURS) ** 0.5 * WINDOW ** 0.5
        if not sigma:
            continue
        move = c[i] / c[i - WINDOW] - 1
        side = 1 if move <= -v.k * sigma and c[i] > o[i] else \
            -1 if v.sides == "both" and move >= v.k * sigma and c[i] < o[i] else 0
        if not side:
            continue
        extreme = min(lo[i - WINDOW + 1:i + 1]) if side > 0 else max(hi[i - WINDOW + 1:i + 1])
        nxt = bars[i + 1]
        price = nxt["ask_open"] if side > 0 else nxt["bid_open"]
        book.open(side, price, extreme - side * v.stop_atr * a[i],
                  price + side * v.target_fraction * abs(c[i - WINDOW] - c[i]), nxt["datetime"])
        if book.pos is not None:
            opened_at = i + 1
    return book.trades


def stats(rows: list[dict]) -> dict:
    if not rows:
        return {"trades": 0, "t_stat": None}
    r = [t["r"] for t in rows]
    mean = sum(r) / len(r)
    sd = (sum((x - mean) ** 2 for x in r) / max(1, len(r) - 1)) ** 0.5
    gains, losses = sum(x for x in r if x > 0), -sum(x for x in r if x < 0)
    by_month: dict[str, float] = {}
    per: dict[str, float] = {}
    for t in rows:
        key = datetime.fromtimestamp(t["entry_ms"] / 1000, timezone.utc).strftime("%Y-%m")
        by_month[key] = by_month.get(key, 0.0) + t["r"]
        per[t["market"]] = per.get(t["market"], 0.0) + t["r"]
    vals, rng = list(by_month.values()), random.Random(7)
    p = sum(sum(rng.choice(vals) for _ in vals) > 0 for _ in range(2000)) / 2000
    longs, shorts = [t["r"] for t in rows if t["side"] > 0], [t["r"] for t in rows if t["side"] < 0]
    return {"trades": len(r), "win_rate": round(sum(x > 0 for x in r) / len(r), 3), "mean_r": round(mean, 4),
            "profit_factor": round(gains / losses, 3) if losses else None, "p_mean_positive": p,
            "t_stat": round(mean / sd * len(r) ** 0.5, 2) if sd else None,
            "long": [len(longs), round(sum(longs) / len(longs), 4) if longs else None],
            "short": [len(shorts), round(sum(shorts) / len(shorts), 4) if shorts else None],
            "markets_positive": sum(x > 0 for x in per.values()),
            "per_market_total_r": {k: round(x, 1) for k, x in per.items()}}


def run(v: Variant, loaded: dict[str, list[dict]], holdout: bool) -> list[dict]:
    rows = [t for m, bars in loaded.items() for t in trades_for(m, bars, v)]
    return [t for t in rows if (t["entry_ms"] >= HOLDOUT_START) == holdout]


def combine(better: list[Variant]) -> Variant:
    combo = Variant("+".join(v.name for v in better))
    for v in better:
        combo = replace(combo, **{k: val for k, val in asdict(v).items()
                                  if k != "name" and val != getattr(SINGLES[0], k)})
    return combo


def main() -> None:
    loaded = {m: h1.load(m) for m in MARKETS}
    Path("research_output").mkdir(exist_ok=True)
    if "--holdout" in sys.argv:
        saved = json.loads(OUT.read_text())
        if not saved.get("holdout_allowed"):
            raise SystemExit("development did not qualify: the holdout is not spent")
        v = Variant(**saved["chosen"])
        rows = run(v, loaded, True)
        report = stats(rows)
        first = stats([t for t in rows if t["entry_ms"] < HOLDOUT_SPLIT])
        second = stats([t for t in rows if t["entry_ms"] >= HOLDOUT_SPLIT])
        report["halves_mean_r"] = [first.get("mean_r"), second.get("mean_r")]
        report["passes"] = (report["trades"] >= 300 and report["mean_r"] > 0 and report["p_mean_positive"] >= 0.95
                            and (report["profit_factor"] or 0) >= 1.15 and report["markets_positive"] >= 7
                            and all((x or 0) > 0 for x in report["halves_mean_r"]))
        saved["holdout"] = report
        OUT.write_text(json.dumps(saved, indent=2) + "\n")
        print(json.dumps({"chosen": saved["chosen"], "holdout": report}, indent=1))
        return
    dev = {}
    for v in SINGLES:
        dev[v.name] = stats(run(v, loaded, False))
        print(v.name, json.dumps({k: x for k, x in dev[v.name].items() if k != "per_market_total_r"}))
    base_t = dev["base"]["t_stat"] or 0
    better = [v for v in SINGLES[1:] if (dev[v.name]["t_stat"] or -1e9) > base_t]
    pool = list(SINGLES)
    if len(better) > 1:
        combo = combine(better)
        dev[combo.name] = stats(run(combo, loaded, False))
        print(combo.name, json.dumps({k: x for k, x in dev[combo.name].items() if k != "per_market_total_r"}))
        pool.append(combo)
    chosen = max(pool, key=lambda v: dev[v.name]["t_stat"] if dev[v.name]["t_stat"] is not None else -1e9)
    allowed = (dev[chosen.name]["t_stat"] or 0) >= 2.0 and dev[chosen.name]["trades"] >= 300
    OUT.write_text(json.dumps({"development": dev, "chosen": asdict(chosen), "holdout_allowed": allowed},
                              indent=2) + "\n")
    print("chosen", asdict(chosen), "holdout allowed", allowed)


if __name__ == "__main__":
    main()
