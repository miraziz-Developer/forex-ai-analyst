"""Commodity trend on unseen MT5 markets (docs/COMMODITY_TREND_STUDY.md).

    python3 -m forex_ai_analyst.forex.commodity_trend_study
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from forex_ai_analyst.forex import dukascopy
from forex_ai_analyst.forex.data import Market, load_yahoo
from forex_ai_analyst.forex.study import bootstrap_p_mean_positive, summarize
from forex_ai_analyst.forex.trend_study import VARIANTS
from forex_ai_analyst.lab import engine
from forex_ai_analyst.lab.strategies import donchian

GATE = {"min_trades": 100, "min_bootstrap_p": 0.95, "min_profit_factor": 1.2, "min_unseen_positive": 4}
SWAP_PER_YEAR = 0.05
dukascopy.POINT.update({"LIGHTCMDUSD": 1e-3, "BRENTCMDUSD": 1e-3, "COPPERCMDUSD": 1e-4, "XAGUSD": 1e-3})


@dataclass(frozen=True)
class Commodity:
    name: str
    source: str           # "dukascopy:<instrument>" or "yahoo:<symbol>"
    cost: float           # round trip, fraction of price
    unseen: bool


MARKETS = (
    Commodity("WTI", "dukascopy:LIGHTCMDUSD", 0.0005, True),
    Commodity("BRENT", "dukascopy:BRENTCMDUSD", 0.0005, True),
    Commodity("COPPER", "dukascopy:COPPERCMDUSD", 0.0010, True),
    Commodity("PLATINUM", "yahoo:PL=F", 0.0005, True),
    Commodity("PALLADIUM", "yahoo:PA=F", 0.0010, True),
    Commodity("XAUUSD", "dukascopy:XAUUSD", 0.0005, False),
    Commodity("XAGUSD", "dukascopy:XAGUSD", 0.0005, False),
)


REPLICATION = tuple(Commodity(n, f"yahoo:{s}", 0.0005, True) for n, s in (
    ("CORN", "ZC=F"), ("WHEAT", "ZW=F"), ("SOYBEAN", "ZS=F"), ("SOYOIL", "ZL=F"), ("SOYMEAL", "ZM=F"),
    ("SUGAR", "SB=F"), ("HEATOIL", "HO=F")))


def bars(market: Commodity) -> list[dict]:
    kind, symbol = market.source.split(":", 1)
    if kind == "yahoo":
        return load_yahoo(Market(market.name, symbol, market.cost, pct=True), "1d")
    return dukascopy.days(symbol, 2004, datetime.now(timezone.utc).year)


def run_market(market: Commodity, params: dict) -> list[dict]:
    b = bars(market)
    result = engine.run(b, donchian(b, **params), costs=engine.Costs(fee_pct_per_side=market.cost / 2 * 100,
                                                                     slippage_pct_per_side=0.0, charge_funding=False))
    out = []
    for t in result.trades:
        days = (t["exit_time"] - t["entry_time"]) / 86_400_000
        swap = t["qty"] * t["entry"] * SWAP_PER_YEAR * days / 365
        out.append({**t, "market": market.name, "r": (t["net"] - swap) / t["risk"] if t["risk"] else 0.0})
    return out


def evaluate(name: str, params: dict, markets=MARKETS, min_positive: int | None = None) -> dict:
    per_market, unseen = {}, []
    for market in markets:
        trades = run_market(market, params)
        per_market[market.name] = {**summarize(trades), "unseen": market.unseen}
        if market.unseen:
            unseen += trades
    unseen.sort(key=lambda t: t["exit_time"])
    mid = unseen[len(unseen) // 2]["exit_time"]
    halves = [summarize([t for t in unseen if t["exit_time"] < mid]),
              summarize([t for t in unseen if t["exit_time"] >= mid])]
    pooled, p = summarize(unseen), bootstrap_p_mean_positive(unseen)
    positive = sum(1 for m in markets if m.unseen and per_market[m.name].get("total_r", 0) > 0)
    need = min_positive or GATE["min_unseen_positive"]
    reasons = []
    if pooled["trades"] < GATE["min_trades"]:
        reasons.append(f"{pooled['trades']} trades < {GATE['min_trades']}")
    if p < GATE["min_bootstrap_p"]:
        reasons.append(f"P(mean R > 0) {p:.2f} < {GATE['min_bootstrap_p']}")
    if (pooled.get("profit_factor") or 0) < GATE["min_profit_factor"]:
        reasons.append(f"profit factor {pooled.get('profit_factor')} < {GATE['min_profit_factor']}")
    if any(h.get("mean_r", 0) <= 0 for h in halves):
        reasons.append("not positive in both halves")
    if positive < need:
        reasons.append(f"only {positive}/{sum(m.unseen for m in markets)} unseen markets positive")
    return {"name": name, "params": params, "unseen_pooled": pooled, "p_mean_positive": round(p, 3),
            "halves": halves, "unseen_positive": positive, "per_market": per_market,
            "passes": not reasons, "reasons": reasons}


def main_replication() -> None:
    r = evaluate("donchian_d1_long", VARIANTS["donchian_d1_long"], REPLICATION, min_positive=5)
    Path("research_output").mkdir(exist_ok=True)
    Path("research_output/commodity_trend_replication.json").write_text(json.dumps(
        {"generated_at": datetime.now(timezone.utc).isoformat(), "result": r}, indent=2) + "\n")
    s = r["unseen_pooled"]
    print(f"replication: trades {s['trades']} win {s['win_rate']} meanR {s['mean_r']} PF {s['profit_factor']} "
          f"P {r['p_mean_positive']} halves {r['halves'][0].get('mean_r')}/{r['halves'][1].get('mean_r')} "
          f"positive {r['unseen_positive']}/7 -> {'PASS' if r['passes'] else 'FAIL: ' + '; '.join(r['reasons'])}")
    for m, v in r["per_market"].items():
        print(f"    {m:9s} trades {v.get('trades')} win {v.get('win_rate')} totalR {v.get('total_r')} "
              f"PF {v.get('profit_factor')}")


def main() -> None:
    import sys
    if "--replicate" in sys.argv:
        main_replication()
        return
    results = [evaluate(name, params) for name, params in VARIANTS.items()]
    Path("research_output").mkdir(exist_ok=True)
    Path("research_output/commodity_trend_study.json").write_text(json.dumps(
        {"generated_at": datetime.now(timezone.utc).isoformat(), "gate": GATE, "results": results}, indent=2) + "\n")
    for r in results:
        s = r["unseen_pooled"]
        print(f"{r['name']}: unseen trades {s['trades']} win {s['win_rate']} meanR {s['mean_r']} PF {s['profit_factor']} "
              f"P {r['p_mean_positive']} halves {r['halves'][0].get('mean_r')}/{r['halves'][1].get('mean_r')} "
              f"unseen+ {r['unseen_positive']}/5 -> {'PASS' if r['passes'] else 'FAIL: ' + '; '.join(r['reasons'])}")
        for m, v in r["per_market"].items():
            print(f"    {m:9s} {'new ' if v['unseen'] else 'seen'} trades {v.get('trades')} win {v.get('win_rate')} "
                  f"totalR {v.get('total_r')} PF {v.get('profit_factor')}")


if __name__ == "__main__":
    main()
