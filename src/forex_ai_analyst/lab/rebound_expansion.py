"""Capitulation rebound (engine2_study rule B, unchanged) on 32 more BingX coins it has never seen
(docs/CRYPTO_ENGINE2_STUDY.md, expansion section).

    python3 -m forex_ai_analyst.lab.rebound_expansion
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from forex_ai_analyst.lab import combination_study as cs
from forex_ai_analyst.lab import data, engine
from forex_ai_analyst.lab.engine2_study import capitulation_rebound
from forex_ai_analyst.lab.rebound_replication import gate

# On BingX (public /quote/contracts, 2026-10-07) and not among the bot's thirty markets; fixed before any run.
CANDIDATES = ("XTZ", "THETA", "VET", "ICP", "MANA", "GALA", "CHZ", "ENJ", "KSM", "COMP", "SNX", "1INCH", "SUSHI",
              "ZIL", "IOTA", "NEO", "QTUM", "ONT", "BAT", "ZRX", "KAVA", "RUNE", "EGLD", "HBAR", "GRT", "CELO",
              "SKL", "ANKR", "CTSI", "DYDX", "ENS", "APE")


def first_full_month(pair: str) -> datetime | None:
    """2021-01 if the archive starts by then; otherwise the month after the first archived one."""
    for month in data.months(cs.START, cs.END):
        symbol = data.symbol_for(pair)
        path = data._cached(data.KLINES_URL.format(symbol=symbol, interval="1h", month=month),
                            f"{symbol}-1h-{month}.zip")
        if path is not None:
            y, m = int(month[:4]), int(month[5:])
            if (y, m) == (cs.START.year, cs.START.month):
                return cs.START
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
            return datetime(y, m, 1, tzinfo=timezone.utc)
    return None


def main() -> None:
    trades, per, excluded, periods = [], {}, {}, {}
    for coin in CANDIDATES:
        pair = f"{coin}-USDT"
        start = first_full_month(pair)
        if start is None:
            excluded[coin] = "no archive"
            continue
        try:
            hourly = data.load_klines(pair, start, cs.END, "1h")
        except RuntimeError as exc:               # a month missing inside the period, or too many gaps
            excluded[coin] = str(exc)
            continue
        result = engine.run(hourly, capitulation_rebound(hourly), data.load_funding(pair, start, cs.END))
        t = [{**x, "market": pair} for x in result.trades]
        trades += t
        per[pair] = round(sum(x["r"] for x in t), 1)
        periods[coin] = start.date().isoformat()
    report = gate(trades, per)
    positive = sum(v > 0 for v in per.values())
    if per and positive < 0.6 * len(per):
        report["passes"] = False
        report["reasons"].append(f"only {positive} of {len(per)} coins positive (< 60%)")
    report.update({"coins_tested": len(per), "coins_positive": positive, "excluded": excluded, "start": periods})
    print(json.dumps(report, indent=1, default=str))
    Path("research_output").mkdir(exist_ok=True)
    Path("research_output/rebound_expansion.json").write_text(json.dumps(
        {"generated_at": datetime.now(timezone.utc).isoformat(), "report": report}, indent=2, default=str) + "\n")


if __name__ == "__main__":
    main()
