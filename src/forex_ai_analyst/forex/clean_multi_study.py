"""Owner's rules and the regime-switching system rerun on clean Dukascopy data (docs/CLEAN_MULTI_STUDY.md).

    python3 -m forex_ai_analyst.forex.clean_multi_study
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

from forex_ai_analyst.forex import dukascopy
from forex_ai_analyst.forex import h1_ml_study as h1
from forex_ai_analyst.forex.data import MARKETS, Market, cost_fraction
from forex_ai_analyst.forex.regime_system import module_signals
from forex_ai_analyst.forex.strategies import STRATEGIES
from forex_ai_analyst.forex.study import bootstrap_p_mean_positive, summarize
from forex_ai_analyst.lab import engine
from forex_ai_analyst.lab.data import resample

GATE = {"min_trades": 100, "min_bootstrap_p": 0.95, "min_profit_factor": 1.2, "min_market_share_positive": 0.6}
MARKET_SPEC = {name: MARKETS.get(name, Market(name, "", 0.00012)) for name in h1.MARKETS}


def hourly_mid(name: str) -> list[dict]:
    return [{"datetime": b["datetime"], "open": (b["bid_open"] + b["ask_open"]) / 2,
             "high": (b["bid_high"] + b["ask_high"]) / 2, "low": (b["bid_low"] + b["ask_low"]) / 2,
             "close": (b["bid_close"] + b["ask_close"]) / 2, "volume": 0} for b in h1.load(name)]


def bars(name: str, timeframe: str, cache: dict) -> list[dict]:
    key = (name, timeframe)
    if key not in cache:
        if timeframe == "D1":
            cache[key] = dukascopy.days(name, 2010, datetime.now(timezone.utc).year)
        else:
            hourly = cache.setdefault((name, "H1"), hourly_mid(name))
            cache[key] = hourly if timeframe == "H1" else resample(hourly, 4)
    return cache[key]


def judge(trades: list[dict]) -> dict:
    trades.sort(key=lambda t: t["exit_time"])
    if not trades:
        return {"pooled": {"trades": 0}, "passes": False, "reasons": ["no trades"]}
    mid = trades[len(trades) // 2]["exit_time"]
    halves = [summarize([t for t in trades if t["exit_time"] < mid]),
              summarize([t for t in trades if t["exit_time"] >= mid])]
    per_market: dict[str, float] = {}
    for t in trades:
        per_market[t["market"]] = per_market.get(t["market"], 0.0) + t["r"]
    positive = sum(v > 0 for v in per_market.values())
    pooled, p = summarize(trades), bootstrap_p_mean_positive(trades)
    reasons = []
    if pooled["trades"] < GATE["min_trades"]:
        reasons.append(f"{pooled['trades']} trades < {GATE['min_trades']}")
    if p < GATE["min_bootstrap_p"]:
        reasons.append(f"P {p:.2f} < {GATE['min_bootstrap_p']}")
    if (pooled.get("profit_factor") or 0) < GATE["min_profit_factor"]:
        reasons.append(f"PF {pooled.get('profit_factor')} < {GATE['min_profit_factor']}")
    if any(h.get("mean_r", 0) <= 0 for h in halves):
        reasons.append("not positive in both halves")
    if positive / len(per_market) < GATE["min_market_share_positive"]:
        reasons.append(f"only {positive}/{len(per_market)} markets positive")
    return {"pooled": pooled, "p_mean_positive": round(p, 3), "halves": [h.get("mean_r") for h in halves],
            "markets_positive": f"{positive}/{len(per_market)}",
            "per_market_total_r": {k: round(v, 1) for k, v in per_market.items()},
            "passes": not reasons, "reasons": reasons}


def costs_for(name: str, b: list[dict]) -> engine.Costs:
    side = cost_fraction(MARKET_SPEC[name], median(x["close"] for x in b)) / 2 * 100
    return engine.Costs(fee_pct_per_side=side, slippage_pct_per_side=0.0, charge_funding=False)


def main() -> None:
    cache: dict = {}
    report = {}
    for rule_name, (rule, timeframe) in STRATEGIES.items():
        trades = []
        for name in h1.MARKETS:
            b = bars(name, timeframe, cache)
            trades += [{**t, "market": name}
                       for t in engine.run(b, rule(b, MARKET_SPEC[name].point), costs=costs_for(name, b)).trades]
        report[rule_name] = judge(trades)
    for timeframe in ("H4", "D1"):
        modules: dict[str, list[dict]] = {"trend": [], "range": [], "breakout": []}
        for name in h1.MARKETS:
            b = bars(name, timeframe, cache)
            for module, signals in module_signals(b).items():
                modules[module] += [{**t, "market": name} for t in engine.run(b, signals, costs=costs_for(name, b)).trades]
        modules["combined"] = [t for m in ("trend", "range", "breakout") for t in modules[m]]
        for module, trades in modules.items():
            report[f"regime_{timeframe}_{module}"] = judge(trades)
    for name, r in report.items():
        s = r["pooled"]
        print(f"{name:22s} trades {s.get('trades', 0):5d} win {s.get('win_rate')} meanR {s.get('mean_r')} "
              f"PF {s.get('profit_factor')} P {r.get('p_mean_positive')} halves {r.get('halves')} "
              f"markets+ {r.get('markets_positive')} -> {'PASS' if r['passes'] else 'FAIL'}")
    Path("research_output").mkdir(exist_ok=True)
    Path("research_output/clean_multi_study.json").write_text(json.dumps(
        {"generated_at": datetime.now(timezone.utc).isoformat(), "gate": GATE, "report": report}, indent=2) + "\n")


if __name__ == "__main__":
    main()
