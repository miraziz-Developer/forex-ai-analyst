"""Improving the capitulation rebound without fooling ourselves (docs/CRYPTO_ENGINE2_STUDY.md, improvement section).

    python3 -m forex_ai_analyst.lab.rebound_improve           development: every variant on the twenty original markets
    python3 -m forex_ai_analyst.lab.rebound_improve --test    the chosen variant, once, on the ten replication markets
"""
from __future__ import annotations

import json
import sys
from bisect import bisect_right
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from forex_ai_analyst.forex.study import bootstrap_p_mean_positive, summarize
from forex_ai_analyst.lab import combination_study as cs
from forex_ai_analyst.lab import data, engine
from forex_ai_analyst.lab.engine2_study import DROP, HOLD, LIVE_MARKETS, LOOKBACK, NEW_MARKETS
from forex_ai_analyst.lab.rebound_replication import SPLIT
from forex_ai_analyst.lab.strategies import atr

HOUR = 3_600_000
OUT = Path("research_output/rebound_improve.json")
BASE_ON_TEST = {"mean_r": 0.145, "profit_factor": 1.33}     # the unchanged rule on the ten markets (replication)


@dataclass(frozen=True)
class Variant:
    name: str
    funding_le_zero: bool = False     # F: shorts crowded after the crash (funding <= 0) -> squeeze fuel
    market_wide: bool = False         # M: BTC itself down >= 5% in 24 h -> forced, market-wide liquidation
    volume_climax: bool = False       # V: a 24 h hourly volume peak >= 3x the prior week's hourly average
    target_fraction: float = 0.5      # T38: take profit at 38.2% of the drop instead of 50%
    stop_atr: float = 0.5             # S1: stop 1.0 ATR below the 24 h low instead of 0.5


SINGLES = (Variant("base"), Variant("F", funding_le_zero=True), Variant("M", market_wide=True),
           Variant("V", volume_climax=True), Variant("T38", target_fraction=0.382), Variant("S1", stop_atr=1.0))


def signals(bars: list[dict], v: Variant, funding: list[tuple[int, float]], btc: dict[int, float]) -> engine.Signals:
    """The engine2 rule (12% below the close 24 h earlier, green hour, buy the next open, stop under the 24 h
    low, target a fraction of the drop, 24 bars at most) with the variant's filters and exit settings."""
    n = len(bars)
    closes = [b["close"] for b in bars]
    a = atr(bars, 14)
    f_times = [t for t, _ in funding]
    entry, stop, target = [False] * n, [None] * n, [None] * n
    for i in range(24 + 168, n):
        if a[i] is None or closes[i] > closes[i - LOOKBACK] * (1 - DROP) or closes[i] <= bars[i]["open"]:
            continue
        known_at = bars[i]["datetime"] + HOUR                      # the signal hour has closed
        if v.funding_le_zero:
            k = bisect_right(f_times, known_at) - 1
            if k < 0 or funding[k][1] > 0:
                continue
        if v.market_wide:
            now, before = btc.get(bars[i]["datetime"]), btc.get(bars[i]["datetime"] - 24 * HOUR)
            if now is None or before is None or now / before - 1 > -0.05:
                continue
        if v.volume_climax:
            week = [b["volume"] for b in bars[i - 24 - 168:i - 24]]
            if max(b["volume"] for b in bars[i - 23:i + 1]) < 3 * sum(week) / len(week):
                continue
        low = min(b["low"] for b in bars[i - LOOKBACK + 1:i + 1])
        entry[i] = True
        stop[i] = closes[i] - low + v.stop_atr * a[i]
        target[i] = v.target_fraction * (closes[i - LOOKBACK] - closes[i])
    none = [False] * n
    return engine.Signals(entry, none, none, none, stop, target_distance=target, max_bars=HOLD)


def load(markets) -> tuple[dict, dict[int, float]]:
    market_data = {pair: (data.load_klines(pair, cs.START, cs.END, "1h"), data.load_funding(pair, cs.START, cs.END))
                   for pair in markets}
    btc_bars = market_data["BTC-USDT"][0] if "BTC-USDT" in market_data else \
        data.load_klines("BTC-USDT", cs.START, cs.END, "1h")
    return market_data, {b["datetime"]: b["close"] for b in btc_bars}


def run(v: Variant, market_data: dict, btc: dict[int, float]) -> tuple[list[dict], dict[str, float]]:
    trades, per = [], {}
    for pair, (bars, funding) in market_data.items():
        t = [{**x, "market": pair} for x in engine.run(bars, signals(bars, v, funding, btc), funding).trades]
        trades += t
        per[pair] = round(sum(x["r"] for x in t), 1)
    return trades, per


def stats(trades: list[dict], per: dict[str, float]) -> dict:
    s = summarize(trades)
    if not trades:
        return s
    r = [t["r"] for t in trades]
    mean = sum(r) / len(r)
    sd = (sum((x - mean) ** 2 for x in r) / max(1, len(r) - 1)) ** 0.5
    first = summarize([t for t in trades if t["entry_time"] < SPLIT])
    second = summarize([t for t in trades if t["entry_time"] >= SPLIT])
    return {**s, "t_stat": round(mean / sd * len(r) ** 0.5, 2) if sd else None,
            "p_mean_positive": round(bootstrap_p_mean_positive(trades), 3),
            "halves_mean_r": [first.get("mean_r"), second.get("mean_r")],
            "markets_positive": sum(x > 0 for x in per.values()), "markets": len(per)}


def combine(better: list[Variant]) -> Variant:
    """One variant carrying every change of the given singles."""
    combo = Variant("+".join(v.name for v in better))
    for v in better:
        combo = replace(combo, **{k: val for k, val in asdict(v).items()
                                  if k != "name" and val != getattr(SINGLES[0], k)})
    return combo


def choose(dev: dict, market_data: dict, btc: dict[int, float]) -> Variant:
    """Every single change that raised the development t-stat over the base rule is also tried together; the
    variant with the highest development t-stat wins (edge and frequency together: a filter can raise mean R
    just by trading less)."""
    base_t = dev["base"]["t_stat"]
    better = [v for v in SINGLES[1:] if (dev[v.name].get("t_stat") or 0) > base_t]
    pool = list(SINGLES)
    if len(better) > 1:
        combo = combine(better)
        dev[combo.name] = stats(*run(combo, market_data, btc))
        print(combo.name, json.dumps(dev[combo.name]))
        pool.append(combo)
    return max(pool, key=lambda v: dev[v.name].get("t_stat") or -1e9)


def main() -> None:
    Path("research_output").mkdir(exist_ok=True)
    if "--test" in sys.argv:
        saved = json.loads(OUT.read_text())
        v = Variant(**saved["chosen"])
        market_data, btc = load(NEW_MARKETS)
        report = stats(*run(v, market_data, btc))
        report["passes"] = (report["mean_r"] > BASE_ON_TEST["mean_r"] and report["p_mean_positive"] >= 0.95
                            and (report["profit_factor"] or 0) >= 1.15 and report["markets_positive"] >= 6
                            and all((x or 0) > 0 for x in report["halves_mean_r"]))
        saved["test"] = report
        OUT.write_text(json.dumps(saved, indent=2) + "\n")
        print(json.dumps({"chosen": saved["chosen"], "test": report}, indent=1))
        return
    market_data, btc = load(LIVE_MARKETS)
    dev = {}
    for v in SINGLES:
        dev[v.name] = stats(*run(v, market_data, btc))
        print(v.name, json.dumps(dev[v.name]))
    chosen = choose(dev, market_data, btc)
    OUT.write_text(json.dumps({"development": dev, "chosen": asdict(chosen)}, indent=2) + "\n")
    print("chosen", asdict(chosen))


if __name__ == "__main__":
    main()
