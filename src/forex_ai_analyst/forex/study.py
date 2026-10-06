"""Pre-registered forex strategy study (docs/FOREX_STUDY.md).

    python3 -m forex_ai_analyst.forex.study            # Yahoo data (development)

Each rule runs unchanged on every market with that market's round-trip cost.
Only rules passing every gate are written to approved_strategies.json, which is
the only list the MT5 trader is allowed to trade.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

from forex_ai_analyst.forex.data import MARKETS, cost_fraction, load_yahoo
from forex_ai_analyst.forex.strategies import STRATEGIES
from forex_ai_analyst.lab import engine
from forex_ai_analyst.lab.data import resample

GATE = {"min_trades": 100, "min_bootstrap_p": 0.90, "min_profit_factor": 1.2, "min_market_share_positive": 0.6}


CLEAN_DAILY = False      # --clean: daily FX bars from Dukascopy day candles instead of Yahoo (docs/ML_STUDY.md)


def bars_for(market, timeframe: str) -> list[dict]:
    if timeframe == "D1":
        if CLEAN_DAILY and not market.pct:            # crypto keeps Yahoo, whose daily bars are sound
            from forex_ai_analyst.forex import dukascopy
            return dukascopy.days(market.name, 2004, datetime.now(timezone.utc).year)
        return load_yahoo(market, "1d")
    hourly = load_yahoo(market, "1h")
    return hourly if timeframe == "H1" else resample(hourly, 4)


def _day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).date().isoformat()


def bootstrap_p_mean_positive(trades: list[dict], runs: int = 4000, seed: int = 11) -> float:
    by_day: dict[str, list[float]] = {}
    for t in trades:
        by_day.setdefault(_day(t["exit_time"]), []).append(t["r"])
    days = list(by_day)
    if len(days) < 5:
        return 0.0
    rng, positive = random.Random(seed), 0
    for _ in range(runs):
        sample = [x for _ in days for x in by_day[rng.choice(days)]]
        positive += sum(sample) / len(sample) > 0
    return positive / runs


def summarize(trades: list[dict]) -> dict:
    if not trades:
        return {"trades": 0}
    r = [t["r"] for t in trades]
    gains, losses = sum(x for x in r if x > 0), -sum(x for x in r if x < 0)
    return {"trades": len(r), "win_rate": round(sum(x > 0 for x in r) / len(r), 3), "mean_r": round(sum(r) / len(r), 3),
            "total_r": round(sum(r), 1), "profit_factor": round(gains / losses, 2) if losses else None,
            "avg_win_r": round(gains / max(1, sum(x > 0 for x in r)), 2),
            "avg_loss_r": round(-losses / max(1, sum(x < 0 for x in r)), 2)}


def evaluate(name: str) -> dict:
    rule, timeframe = STRATEGIES[name]
    trades, per_market = [], {}
    for market in MARKETS.values():
        bars = bars_for(market, timeframe)
        if len(bars) < 300:
            continue
        side = cost_fraction(market, median(b["close"] for b in bars)) / 2 * 100
        result = engine.run(bars, rule(bars, market.point),
                            costs=engine.Costs(fee_pct_per_side=side, slippage_pct_per_side=0.0, charge_funding=False))
        market_trades = [{**t, "market": market.name} for t in result.trades]
        trades += market_trades
        per_market[market.name] = summarize(market_trades)
    trades.sort(key=lambda t: t["exit_time"])
    mid = trades[len(trades) // 2]["exit_time"] if trades else 0
    halves = {"first": summarize([t for t in trades if t["exit_time"] < mid]),
              "second": summarize([t for t in trades if t["exit_time"] >= mid])}
    pooled = summarize(trades)
    p = bootstrap_p_mean_positive(trades)
    positive = sum(1 for s in per_market.values() if s.get("total_r", 0) > 0)
    reasons = []
    if pooled.get("trades", 0) < GATE["min_trades"]:
        reasons.append(f"{pooled.get('trades', 0)} trades < {GATE['min_trades']}")
    if p < GATE["min_bootstrap_p"]:
        reasons.append(f"P(mean R > 0) {p:.2f} < {GATE['min_bootstrap_p']}")
    if (pooled.get("profit_factor") or 0) < GATE["min_profit_factor"]:
        reasons.append(f"profit factor {pooled.get('profit_factor')} < {GATE['min_profit_factor']}")
    if any(h.get("mean_r", 0) <= 0 for h in halves.values()):
        reasons.append("not positive in both halves")
    if per_market and positive / len(per_market) < GATE["min_market_share_positive"]:
        reasons.append(f"only {positive}/{len(per_market)} markets positive")
    span = (trades[-1]["exit_time"] - trades[0]["entry_time"]) / 86_400_000 / 30.4 if len(trades) > 1 else 0
    return {"name": name, "timeframe": timeframe, "pooled": pooled, "p_mean_positive": round(p, 3),
            "trades_per_month": round(len(trades) / span, 1) if span else None, "halves": halves,
            "markets_positive": f"{positive}/{len(per_market)}", "per_market": per_market,
            "passes": not reasons, "reasons": reasons}


def main() -> None:
    global CLEAN_DAILY
    import sys
    if "--clean" in sys.argv:          # data correction rerun of the daily rules only
        CLEAN_DAILY = True
        results = [evaluate(name) for name, spec in STRATEGIES.items() if spec[1] == "D1"]
        Path("research_output/forex_study_clean_daily.json").write_text(json.dumps(
            {"generated_at": datetime.now(timezone.utc).isoformat(), "gate": GATE, "results": results}, indent=2) + "\n")
        for r in results:
            s = r["pooled"]
            print(f"{r['name']:18s} trades {s.get('trades', 0):5d} win {s.get('win_rate')} meanR {s.get('mean_r')} "
                  f"PF {s.get('profit_factor')} P {r['p_mean_positive']} halves {r['halves']['first'].get('mean_r')}/"
                  f"{r['halves']['second'].get('mean_r')} markets+ {r['markets_positive']} per-market "
                  f"{ {m: v.get('total_r') for m, v in r['per_market'].items()} } -> "
                  f"{'PASS' if r['passes'] else 'FAIL: ' + '; '.join(r['reasons'])}")
        return
    results = [evaluate(name) for name in STRATEGIES]
    out = Path("research_output") / "forex_study.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(), "gate": GATE,
                               "results": results}, indent=2) + "\n")
    approved = [{"name": r["name"], "timeframe": r["timeframe"],
                 "markets": [m for m, s in r["per_market"].items() if s.get("total_r", 0) > 0]}
                for r in results if r["passes"]]
    Path("research_output/approved_strategies.json").write_text(json.dumps(approved, indent=2) + "\n")
    for r in results:
        s = r["pooled"]
        print(f"{r['name']:18s} trades {s.get('trades', 0):5d} ({r['trades_per_month']}/mo) win {s.get('win_rate')} "
              f"meanR {s.get('mean_r')} PF {s.get('profit_factor')} P {r['p_mean_positive']} "
              f"halves {r['halves']['first'].get('mean_r')}/{r['halves']['second'].get('mean_r')} "
              f"markets+ {r['markets_positive']} -> {'PASS' if r['passes'] else 'FAIL: ' + '; '.join(r['reasons'])}")


if __name__ == "__main__":
    main()
