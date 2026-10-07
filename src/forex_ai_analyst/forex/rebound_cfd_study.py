"""Crypto capitulation rebound on MT5 CFD costs (docs/CRYPTO_CFD_STUDY.md, rebound section).

    python3 -m forex_ai_analyst.forex.rebound_cfd_study
"""
from __future__ import annotations

import json
from pathlib import Path

from forex_ai_analyst.forex.crypto_cfd_study import COINS, END, START, SWAPS, portfolio, spread, with_swap
from forex_ai_analyst.forex.study import bootstrap_p_mean_positive, summarize
from forex_ai_analyst.lab import data, engine
from forex_ai_analyst.lab.engine2_study import capitulation_rebound


def trades_for(coin: str) -> list[dict]:
    bars = data.load_klines(coin, START, END)
    costs = engine.Costs(fee_pct_per_side=spread(coin) / 2 * 100, slippage_pct_per_side=0.0, charge_funding=False)
    return [{**t, "coin": coin} for t in engine.run(bars, capitulation_rebound(bars), costs=costs).trades]


def main() -> None:
    raw = {c: trades_for(c) for c in COINS}
    report = {}
    for swap in SWAPS:
        rows = [t for c in COINS for t in with_swap(raw[c], swap)]
        per = {c: round(sum(t["r"] for t in with_swap(raw[c], swap)), 1) for c in COINS}
        row = {**summarize(rows), "p_mean_positive": round(bootstrap_p_mean_positive(rows), 3),
               "coins_positive": sum(v > 0 for v in per.values()), "per_coin_total_r": per,
               "portfolio_0_5pct_risk": portfolio(rows, 0.005)}
        if swap == 0.20:
            row["passes"] = (row["mean_r"] > 0 and (row["profit_factor"] or 0) >= 1.15
                             and row["p_mean_positive"] >= 0.90 and row["coins_positive"] >= 6)
        report[f"swap_{int(swap * 100)}pct"] = row
        print(f"swap {swap:.0%}: {json.dumps(row)}")
    Path("research_output").mkdir(exist_ok=True)
    Path("research_output/rebound_cfd.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
