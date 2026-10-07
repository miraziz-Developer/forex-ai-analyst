"""Live Donchian 4h with a fixed take-profit or a wider ATR trail, on the thirty live markets
(docs/DONCHIAN_TRAILING_STUDY.md, second round).

    python3 -m forex_ai_analyst.lab.exit_study2
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from forex_ai_analyst.lab import combination_study as cs
from forex_ai_analyst.lab import data, engine, validation
from forex_ai_analyst.lab.engine2_study import LIVE_MARKETS, NEW_MARKETS
from forex_ai_analyst.lab.strategies import donchian
from forex_ai_analyst.lab.trailing_study import with_trail


def with_target(multiple_of_stop: float):
    """Take profit at `multiple_of_stop` times the 3 ATR stop distance; the exit channel and stop still apply."""
    def rule(bars):
        s = donchian(bars, entry_n=100, exit_n=20, stop_atr=3.0, sides="long")
        return engine.Signals(s.long_entry, s.short_entry, s.long_exit, s.short_exit, s.stop_distance,
                              target_distance=[d * multiple_of_stop if d else None for d in s.stop_distance])
    return rule


VARIANTS = (("TP2R", with_target(2.0)), ("TP3R", with_target(3.0)), ("TP5R", with_target(5.0)),
            ("T4_trail_4atr", with_trail(4.0)), ("T5_trail_5atr", with_trail(5.0)))


def main() -> None:
    market_data = {}
    for pair in LIVE_MARKETS + NEW_MARKETS:
        hourly = data.load_klines(pair, cs.START, cs.END, "1h")
        market_data[pair] = (data.resample(hourly, 4), data.load_funding(pair, cs.START, cs.END))
    report = {}
    for name, rule in (("live", cs._baseline),) + VARIANTS:
        returns, trades = cs._trade_strategy(rule, market_data)
        r = [t["r"] for t in trades]
        wins, losses = [x for x in r if x > 0], [x for x in r if x <= 0]
        report[name] = {"full": validation.stats(returns),
                        "halves": {k: validation.stats(validation.window(returns, *span)) for k, span in cs.HALVES.items()},
                        "trades": cs._trade_stats(trades, 5.67),
                        "avg_win_r": round(sum(wins) / len(wins), 2) if wins else None,
                        "avg_loss_r": round(sum(losses) / len(losses), 2) if losses else None,
                        "best_trade_r": round(max(r), 1) if r else None}
    live = report["live"]
    for name, _ in VARIANTS:
        x = report[name]
        x["adopted"] = (x["full"]["sharpe"] > live["full"]["sharpe"]
                        and all(x["halves"][k]["sharpe"] > live["halves"][k]["sharpe"] for k in cs.HALVES)
                        and (x["trades"].get("profit_factor") or 0) >= (live["trades"].get("profit_factor") or 0))
    for name, x in report.items():
        print(name, json.dumps({"sharpe": x["full"]["sharpe"], "cagr": x["full"].get("cagr"),
                                "max_dd": x["full"].get("max_drawdown"),
                                "halves": {k: v["sharpe"] for k, v in x["halves"].items()},
                                "win": x["trades"].get("win_rate"), "mean_r": x["trades"].get("mean_r"),
                                "pf": x["trades"].get("profit_factor"), "avg_win": x["avg_win_r"],
                                "avg_loss": x["avg_loss_r"], "best": x["best_trade_r"], "adopted": x.get("adopted")}))
    Path("research_output").mkdir(exist_ok=True)
    Path("research_output/donchian_exit_study2.json").write_text(json.dumps(
        {"generated_at": datetime.now(timezone.utc).isoformat(), "report": report}, indent=2, default=str) + "\n")


if __name__ == "__main__":
    main()
