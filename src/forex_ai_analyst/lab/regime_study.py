"""Regime study: does a BTC 200-day regime filter (and bear-regime shorts) improve Donchian 4h?

Pre-registered in docs/REGIME_STUDY.md before this was first run.

    python3 -m forex_ai_analyst.lab.regime_study
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from pathlib import Path

from forex_ai_analyst.lab import data, engine, validation
from forex_ai_analyst.lab.candidates import btc_bull_regime, regime_donchian
from forex_ai_analyst.lab.combination_study import END, HALVES, MARKETS, START, _baseline, _trade_stats

VARIANTS = {"baseline (live)": None, "long_bull_filter": False, "long_bull_short_bear": True}


def _bootstrap_mean_positive(trades: list[dict], runs: int = 5000, seed: int = 11) -> float | None:
    by_day: dict[str, list[float]] = {}
    for t in trades:
        by_day.setdefault(validation.date_of(t["exit_time"]), []).append(t["r"])
    days = list(by_day)
    if len(days) < 5:
        return None
    rng, positive = random.Random(seed), 0
    for _ in range(runs):
        sample = [x for _ in days for x in by_day[rng.choice(days)]]
        positive += sum(sample) / len(sample) > 0
    return round(positive / runs, 3)


def run() -> dict:
    btc_daily = data.resample(data.load_klines("BTC-USDT", START, END, "1h"), 24)
    years = (END - START).days / 365
    results = {}
    for name, shorts in VARIANTS.items():
        streams, trades = [], []
        for pair in MARKETS:
            bars = data.resample(data.load_klines(pair, START, END, "1h"), 4)
            signals = _baseline(bars) if shorts is None else regime_donchian(bars, btc_bull_regime(bars, btc_daily),
                                                                             shorts=shorts)
            result = engine.run(bars, signals, data.load_funding(pair, START, END))
            streams.append(validation.daily_returns(result.equity))
            trades += [{**t, "pair": pair} for t in result.trades]
        returns = validation.combine(streams)
        shorts_only = [t for t in trades if t["direction"] < 0]
        results[name] = {
            "full": validation.stats(returns),
            "halves": {k: validation.stats(validation.window(returns, *span)) for k, span in HALVES.items()},
            "trades": _trade_stats(trades, years),
            "short_leg": {**_trade_stats(shorts_only, years), "p_mean_positive": _bootstrap_mean_positive(shorts_only)}
            if shorts_only else None,
        }
    base = results["baseline (live)"]
    for name, row in results.items():
        if name == "baseline (live)":
            continue
        reasons = [f"{k} Sharpe {row['halves'][k]['sharpe']} <= baseline {base['halves'][k]['sharpe']}"
                   for k in HALVES if row["halves"][k]["sharpe"] <= base["halves"][k]["sharpe"]]
        if row["full"]["max_drawdown_pct"] < base["full"]["max_drawdown_pct"] * 1.10:
            reasons.append(f"max drawdown {row['full']['max_drawdown_pct']}% worse than baseline by >10%")
        leg = row["short_leg"]
        if VARIANTS[name]:
            if not leg or (leg.get("p_mean_positive") or 0) < 0.90 or (leg.get("profit_factor") or 0) < 1.2:
                reasons.append(f"short leg not proven: {leg and {k: leg.get(k) for k in ('trades', 'mean_r', 'profit_factor', 'p_mean_positive')}}")
        row["passes"], row["reasons"] = not reasons, reasons
    passing = [n for n, r in results.items() if r.get("passes")]
    winner = max(passing, key=lambda n: results[n]["full"]["sharpe"]) if passing else "baseline (live)"
    return {"generated_at": datetime.now(timezone.utc).isoformat(), "variants": results, "adopt": winner}


def main() -> None:
    report = run()
    out = Path("research_output") / "regime_study.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=1, default=str))


if __name__ == "__main__":
    main()
