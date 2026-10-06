"""Live Donchian 4h with an added ATR chandelier trail (docs/DONCHIAN_TRAILING_STUDY.md).

    python3 -m forex_ai_analyst.lab.trailing_study
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from forex_ai_analyst.lab import combination_study as cs
from forex_ai_analyst.lab import data, engine, validation
from forex_ai_analyst.lab.engine2_study import LIVE_MARKETS
from forex_ai_analyst.lab.strategies import atr, donchian


def with_trail(multiple: float):
    def rule(bars):
        s = donchian(bars, entry_n=100, exit_n=20, stop_atr=3.0, sides="long")
        a = atr(bars)
        return engine.Signals(s.long_entry, s.short_entry, s.long_exit, s.short_exit, s.stop_distance,
                              trail_distance=[x * multiple if x else None for x in a])
    return rule


def main() -> None:
    market_data = {}
    for pair in LIVE_MARKETS:
        hourly = data.load_klines(pair, cs.START, cs.END, "1h")
        market_data[pair] = (data.resample(hourly, 4), data.load_funding(pair, cs.START, cs.END))
    report = {}
    for name, rule in (("live", cs._baseline), ("T1_trail_3atr", with_trail(3.0)), ("T2_trail_2atr", with_trail(2.0))):
        returns, trades = cs._trade_strategy(rule, market_data)
        report[name] = {"full": validation.stats(returns),
                        "halves": {k: validation.stats(validation.window(returns, *span)) for k, span in cs.HALVES.items()},
                        "trades": cs._trade_stats(trades, 5.67)}
    live = report["live"]
    for name in ("T1_trail_3atr", "T2_trail_2atr"):
        r = report[name]
        r["adopted"] = (r["full"]["sharpe"] > live["full"]["sharpe"]
                        and all(r["halves"][k]["sharpe"] > live["halves"][k]["sharpe"] for k in cs.HALVES)
                        and (r["trades"].get("profit_factor") or 0) >= (live["trades"].get("profit_factor") or 0))
    for name, r in report.items():
        print(name, json.dumps({"sharpe": r["full"]["sharpe"], "cagr": r["full"].get("cagr"),
                                "max_dd": r["full"].get("max_drawdown"),
                                "halves": {k: v["sharpe"] for k, v in r["halves"].items()},
                                "win": r["trades"].get("win_rate"), "mean_r": r["trades"].get("mean_r"),
                                "pf": r["trades"].get("profit_factor"), "adopted": r.get("adopted")}))
    Path("research_output").mkdir(exist_ok=True)
    Path("research_output/donchian_trailing_study.json").write_text(json.dumps(
        {"generated_at": datetime.now(timezone.utc).isoformat(), "report": report}, indent=2, default=str) + "\n")


if __name__ == "__main__":
    main()
