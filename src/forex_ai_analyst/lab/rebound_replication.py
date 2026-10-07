"""Capitulation rebound (engine2_study rule B, unchanged) on the ten markets it was never run on
(docs/CRYPTO_ENGINE2_STUDY.md, replication section).

    python3 -m forex_ai_analyst.lab.rebound_replication
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from forex_ai_analyst.forex.study import bootstrap_p_mean_positive, summarize
from forex_ai_analyst.lab import combination_study as cs
from forex_ai_analyst.lab import data, engine
from forex_ai_analyst.lab.engine2_study import NEW_MARKETS, capitulation_rebound

SPLIT = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)


def gate(trades: list[dict], per: dict[str, float]) -> dict:
    s, p = summarize(trades), bootstrap_p_mean_positive(trades)
    first = summarize([t for t in trades if t["entry_time"] < SPLIT])
    second = summarize([t for t in trades if t["entry_time"] >= SPLIT])
    reasons = []
    if s.get("mean_r", 0) <= 0 or p < 0.95:
        reasons.append(f"mean R {s.get('mean_r')} / P {p:.3f}")
    if (s.get("profit_factor") or 0) < 1.15:
        reasons.append(f"PF {s.get('profit_factor')} < 1.15")
    if sum(v > 0 for v in per.values()) < 6:
        reasons.append("fewer than 6 of 10 markets positive")
    if first.get("mean_r", 0) <= 0 or second.get("mean_r", 0) <= 0:
        reasons.append(f"halves {first.get('mean_r')} / {second.get('mean_r')}")
    return {**s, "p_mean_positive": round(p, 3), "first_half_2021_2023": first, "second_half_2024_2026": second,
            "per_market_total_r": per, "passes": not reasons, "reasons": reasons}


def main() -> None:
    trades, per = [], {}
    for pair in NEW_MARKETS:
        hourly = data.load_klines(pair, cs.START, cs.END, "1h")
        result = engine.run(hourly, capitulation_rebound(hourly), data.load_funding(pair, cs.START, cs.END))
        t = [{**x, "market": pair} for x in result.trades]
        trades += t
        per[pair] = round(sum(x["r"] for x in t), 1)
    report = gate(trades, per)
    print(json.dumps(report, indent=1, default=str))
    Path("research_output").mkdir(exist_ok=True)
    Path("research_output/rebound_replication.json").write_text(json.dumps(
        {"generated_at": datetime.now(timezone.utc).isoformat(), "report": report}, indent=2, default=str) + "\n")


if __name__ == "__main__":
    main()
