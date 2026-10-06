"""Pre-registered index pullback study (docs/INDEX_STUDY.md).

    python3 -m forex_ai_analyst.forex.index_study
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from pathlib import Path

from forex_ai_analyst.forex.data import Market, load_yahoo
from forex_ai_analyst.lab.candidates import rsi
from forex_ai_analyst.lab.strategies import atr

INDICES = (("US500", "^GSPC"), ("USTEC", "^NDX"), ("US30", "^DJI"), ("DE40", "^GDAXI"), ("UK100", "^FTSE"),
           ("JP225", "^N225"))
INFO_ONLY = (("XAUUSD", "GC=F"),)
COST, SWAP_PER_YEAR, STOP_ATR, MAX_HOLD = 0.0003, 0.06, 3.0, 10
SPLIT = "2015-01-01"
GATE = {"min_trades": 300, "min_p": 0.95, "min_pf": 1.3, "min_positive": 5, "min_drift_multiple": 2.0}


def day(bar: dict) -> str:
    return datetime.fromtimestamp(bar["datetime"] / 1000, timezone.utc).date().isoformat()


def sma(values: list[float], n: int) -> list[float | None]:
    out, total = [None] * len(values), 0.0
    for i, v in enumerate(values):
        total += v
        if i >= n:
            total -= values[i - n]
        if i >= n - 1:
            out[i] = total / n
    return out


def signals(bars: list[dict], variant: str) -> tuple[list[bool], list[bool]]:
    closes = [b["close"] for b in bars]
    s200, s5, r2 = sma(closes, 200), sma(closes, 5), rsi(closes, 2)
    entry, exit_ = [False] * len(bars), [False] * len(bars)
    for i in range(1, len(bars)):
        b = bars[i]
        if s200[i] is None:
            continue
        up = closes[i] > s200[i]
        if variant == "IDX1":
            entry[i] = up and r2[i] is not None and r2[i] < 10
            exit_[i] = s5[i] is not None and closes[i] > s5[i]
        else:
            rng = b["high"] - b["low"]
            ibs = (closes[i] - b["low"]) / rng if rng > 0 else 0.5
            entry[i] = up and ibs < 0.2 and closes[i] < closes[i - 1]
            exit_[i] = closes[i] > bars[i - 1]["high"]
    return entry, exit_


def days_between(a: str, b: str) -> int:
    return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).days


def backtest(bars: list[dict], variant: str, market: str) -> list[dict]:
    entry, exit_ = signals(bars, variant)
    a = atr(bars, 14)
    trades, i = [], 0
    while i < len(bars) - 2:
        if not entry[i] or a[i] is None:
            i += 1
            continue
        k = i + 1
        price = bars[k]["open"]
        stop = price - STOP_ATR * a[i]
        out, j = None, k
        while out is None:
            b = bars[j]
            if j > k and b["open"] <= stop:
                out = b["open"]
            elif b["low"] <= stop:
                out = stop
            elif j + 1 >= len(bars):
                out = b["close"]
            elif exit_[j] or j - k + 1 >= MAX_HOLD:
                j += 1
                out = bars[j]["open"]
                break
            else:
                j += 1
        held = max(days_between(day(bars[k]), day(bars[j])), 1)
        net = out / price - 1 - COST - SWAP_PER_YEAR * held / 365
        trades.append({"market": market, "entry_day": day(bars[k]), "exit_day": day(bars[j]), "net": net,
                       "bars_held": j - k + 1, "days_held": held})
        i = j
    return trades


def drift_baseline(bars: list[dict]) -> float:
    """Mean net return per bar of being long next-open to next-open while close > SMA(200)."""
    closes = [b["close"] for b in bars]
    s200 = sma(closes, 200)
    rets = []
    for i in range(len(bars) - 2):
        if s200[i] is not None and closes[i] > s200[i]:
            held = max(days_between(day(bars[i + 1]), day(bars[i + 2])), 1)
            rets.append(bars[i + 2]["open"] / bars[i + 1]["open"] - 1 - SWAP_PER_YEAR * held / 365)
    return sum(rets) / len(rets) if rets else 0.0


def bootstrap_p(trades: list[dict], runs: int = 4000, seed: int = 11) -> float:
    by_day: dict[str, list[float]] = {}
    for t in trades:
        by_day.setdefault(t["entry_day"], []).append(t["net"])
    days = list(by_day)
    rng = random.Random(seed)
    return sum(sum(x for _ in days for x in by_day[rng.choice(days)]) > 0 for _ in range(runs)) / runs


def evaluate(trades: list[dict], drift_per_bar: float) -> dict:
    net = [t["net"] for t in trades]
    gains, losses = sum(x for x in net if x > 0), -sum(x for x in net if x < 0)
    first = [t["net"] for t in trades if t["entry_day"] < SPLIT]
    second = [t["net"] for t in trades if t["entry_day"] >= SPLIT]
    per_market: dict[str, float] = {}
    for t in trades:
        per_market[t["market"]] = per_market.get(t["market"], 0.0) + t["net"]
    per_bar = sum(net) / sum(t["bars_held"] for t in trades)
    s = {"trades": len(net), "hit_rate": round(sum(x > 0 for x in net) / len(net), 3),
         "mean_net_pct": round(sum(net) / len(net) * 100, 4), "profit_factor": round(gains / losses, 3),
         "p_mean_positive": bootstrap_p(trades),
         "halves_mean_pct": [round(sum(h) / len(h) * 100, 4) if h else None for h in (first, second)],
         "avg_bars_held": round(sum(t["bars_held"] for t in trades) / len(net), 2),
         "net_per_bar_pct": round(per_bar * 100, 4), "drift_per_bar_pct": round(drift_per_bar * 100, 4),
         "markets_positive": sum(v > 0 for v in per_market.values()),
         "per_market_total_pct": {k: round(v * 100, 1) for k, v in per_market.items()}}
    reasons = []
    if s["trades"] < GATE["min_trades"]:
        reasons.append("too few trades")
    if s["p_mean_positive"] < GATE["min_p"]:
        reasons.append(f"P {s['p_mean_positive']:.3f} < {GATE['min_p']}")
    if s["profit_factor"] < GATE["min_pf"]:
        reasons.append(f"PF {s['profit_factor']} < {GATE['min_pf']}")
    if not first or not second or min(sum(first), sum(second)) <= 0:
        reasons.append("not positive in both periods")
    if s["markets_positive"] < GATE["min_positive"]:
        reasons.append(f"only {s['markets_positive']}/6 indices positive")
    if per_bar < GATE["min_drift_multiple"] * drift_per_bar:
        reasons.append("does not beat 2x the drift baseline per day held")
    return {**s, "passes": not reasons, "reasons": reasons}


def main() -> None:
    data = {name: load_yahoo(Market(name, symbol, COST, pct=True), "1d") for name, symbol in INDICES + INFO_ONLY}
    drift = [drift_baseline(data[name]) for name, _ in INDICES]
    drift_per_bar = sum(drift) / len(drift)
    report = {}
    for variant in ("IDX1", "IDX2"):
        trades = [t for name, _ in INDICES for t in backtest(data[name], variant, name)]
        report[variant] = evaluate(trades, drift_per_bar)
        gold = backtest(data["XAUUSD"], variant, "XAUUSD")
        report[variant]["gold_info"] = {"trades": len(gold),
                                        "mean_net_pct": round(sum(t["net"] for t in gold) / max(len(gold), 1) * 100, 4)}
        print(f"{variant}: {json.dumps(report[variant])}")
    Path("research_output").mkdir(exist_ok=True)
    Path("research_output/index_study.json").write_text(json.dumps(
        {"generated_at": datetime.now(timezone.utc).isoformat(), "gate": GATE, "report": report}, indent=2) + "\n")


if __name__ == "__main__":
    main()
