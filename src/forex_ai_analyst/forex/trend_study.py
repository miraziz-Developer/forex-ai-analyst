"""Pre-registered trend study on metals, stock indices and crypto CFDs (docs/TREND_CFD_STUDY.md).

    python3 -m forex_ai_analyst.forex.trend_study
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from forex_ai_analyst.forex.data import Market, load_yahoo
from forex_ai_analyst.forex.study import bootstrap_p_mean_positive, summarize
from forex_ai_analyst.lab import engine
from forex_ai_analyst.lab.strategies import donchian


@dataclass(frozen=True)
class CfdMarket:
    name: str
    yahoo: str
    cost: float          # round trip, fraction of price
    swap_per_year: float  # overnight financing charged on notional, both directions (conservative)
    unseen: bool          # never used in an earlier study of this rule


MARKETS = (
    CfdMarket("XAGUSD", "SI=F", 0.0006, 0.05, True),
    CfdMarket("US500", "^GSPC", 0.0002, 0.06, True),
    CfdMarket("USTEC", "^NDX", 0.0002, 0.06, True),
    CfdMarket("US30", "^DJI", 0.0002, 0.06, True),
    CfdMarket("DE40", "^GDAXI", 0.0002, 0.06, True),
    CfdMarket("JP225", "^N225", 0.0002, 0.06, True),
    CfdMarket("UK100", "^FTSE", 0.0002, 0.06, True),
    CfdMarket("XAUUSD", "GC=F", 0.0002, 0.05, False),
    CfdMarket("BTCUSD", "BTC-USD", 0.0010, 0.20, False),
    CfdMarket("ETHUSD", "ETH-USD", 0.0015, 0.20, False),
)

VARIANTS = {
    "donchian_d1_both": dict(entry_n=55, exit_n=20, stop_atr=2.0, sides="both"),
    "donchian_d1_long": dict(entry_n=55, exit_n=20, stop_atr=2.0, sides="long"),
}
GATE = {"min_trades": 100, "min_bootstrap_p": 0.90, "min_profit_factor": 1.2, "min_unseen_positive": 5}


def run_market(market: CfdMarket, params: dict) -> list[dict]:
    bars = load_yahoo(Market(market.name, market.yahoo, market.cost, pct=True), "1d")
    result = engine.run(bars, donchian(bars, **params),
                        costs=engine.Costs(fee_pct_per_side=market.cost / 2 * 100, slippage_pct_per_side=0.0,
                                           charge_funding=False))
    trades = []
    for t in result.trades:
        days = (t["exit_time"] - t["entry_time"]) / 86_400_000
        swap = t["qty"] * t["entry"] * market.swap_per_year * days / 365
        r = (t["net"] - swap) / t["risk"] if t["risk"] else 0.0
        trades.append({**t, "market": market.name, "unseen": market.unseen, "r_before_swap": t["r"], "r": r})
    return trades


def evaluate(name: str, params: dict) -> dict:
    per_market, unseen_trades, all_trades = {}, [], []
    for market in MARKETS:
        trades = run_market(market, params)
        per_market[market.name] = {**summarize(trades), "unseen": market.unseen}
        all_trades += trades
        if market.unseen:
            unseen_trades += trades
    unseen_trades.sort(key=lambda t: t["exit_time"])
    mid = unseen_trades[len(unseen_trades) // 2]["exit_time"]
    halves = [summarize([t for t in unseen_trades if t["exit_time"] < mid]),
              summarize([t for t in unseen_trades if t["exit_time"] >= mid])]
    pooled, p = summarize(unseen_trades), bootstrap_p_mean_positive(unseen_trades)
    positive = sum(1 for m in MARKETS if m.unseen and per_market[m.name].get("total_r", 0) > 0)
    reasons = []
    if pooled["trades"] < GATE["min_trades"]:
        reasons.append(f"{pooled['trades']} trades < {GATE['min_trades']}")
    if p < GATE["min_bootstrap_p"]:
        reasons.append(f"P(mean R > 0) {p:.2f} < {GATE['min_bootstrap_p']}")
    if (pooled.get("profit_factor") or 0) < GATE["min_profit_factor"]:
        reasons.append(f"profit factor {pooled.get('profit_factor')} < {GATE['min_profit_factor']}")
    if any(h.get("mean_r", 0) <= 0 for h in halves):
        reasons.append("not positive in both halves")
    if positive < GATE["min_unseen_positive"]:
        reasons.append(f"only {positive}/7 unseen markets positive")
    return {"name": name, "params": params, "unseen_pooled": pooled, "p_mean_positive": round(p, 3),
            "halves": halves, "unseen_positive": positive, "all_markets_pooled": summarize(all_trades),
            "per_market": per_market, "passes": not reasons, "reasons": reasons}


def main() -> None:
    results = [evaluate(name, params) for name, params in VARIANTS.items()]
    passing = [r for r in results if r["passes"]]
    adopted = max(passing, key=lambda r: r["unseen_pooled"]["mean_r"]) if passing else None
    out = Path("research_output")
    out.mkdir(exist_ok=True)
    (out / "trend_cfd_study.json").write_text(json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(),
                                                          "gate": GATE, "results": results,
                                                          "adopted": adopted and adopted["name"]}, indent=2) + "\n")
    approved = ([{"name": adopted["name"], "timeframe": "D1", "params": adopted["params"],
                  "markets": [m.name for m in MARKETS]}] if adopted else [])
    (out / "approved_strategies.json").write_text(json.dumps(approved, indent=2) + "\n")
    for r in results:
        s = r["unseen_pooled"]
        print(f"{r['name']}: unseen trades {s['trades']} win {s['win_rate']} meanR {s['mean_r']} PF {s['profit_factor']} "
              f"P {r['p_mean_positive']} halves {r['halves'][0].get('mean_r')}/{r['halves'][1].get('mean_r')} "
              f"unseen+ {r['unseen_positive']}/7 -> {'PASS' if r['passes'] else 'FAIL: ' + '; '.join(r['reasons'])}")
        for m, v in r["per_market"].items():
            print(f"    {m:7s} {'new ' if v['unseen'] else 'seen'} trades {v.get('trades')} win {v.get('win_rate')} "
                  f"totalR {v.get('total_r')} PF {v.get('profit_factor')}")
    print("adopted:", adopted and adopted["name"])


if __name__ == "__main__":
    main()
