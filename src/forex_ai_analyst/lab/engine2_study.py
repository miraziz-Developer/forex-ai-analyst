"""Ten more markets for the live Donchian rule, and a capitulation-rebound engine (docs/CRYPTO_ENGINE2_STUDY.md).

    python3 -m forex_ai_analyst.lab.engine2_study
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from forex_ai_analyst.forex.study import bootstrap_p_mean_positive, summarize
from forex_ai_analyst.lab import combination_study as cs
from forex_ai_analyst.lab import data, engine, validation
from forex_ai_analyst.lab.strategies import atr

NEW_MARKETS = ("ZEC-USDT", "RLC-USDT", "YFI-USDT", "DASH-USDT", "SAND-USDT", "XMR-USDT", "TRB-USDT", "AXS-USDT",
               "CRV-USDT", "ALGO-USDT")
LIVE_MARKETS = cs.MARKETS + ("DOT-USDT", "TRX-USDT", "BCH-USDT", "UNI-USDT", "NEAR-USDT", "ATOM-USDT", "ETC-USDT",
                             "FIL-USDT", "AAVE-USDT", "XLM-USDT")
DROP, LOOKBACK, HOLD = 0.12, 24, 24


def capitulation_rebound(bars: list[dict]) -> engine.Signals:
    n = len(bars)
    closes = [b["close"] for b in bars]
    a = atr(bars, 14)
    entry, stop, target = [False] * n, [None] * n, [None] * n
    for i in range(LOOKBACK, n):
        if a[i] is None or closes[i] > closes[i - LOOKBACK] * (1 - DROP) or closes[i] <= bars[i]["open"]:
            continue
        low = min(b["low"] for b in bars[i - LOOKBACK + 1:i + 1])
        entry[i] = True
        stop[i] = closes[i] - low + 0.5 * a[i]
        target[i] = 0.5 * (closes[i - LOOKBACK] - closes[i])
    none = [False] * n
    return engine.Signals(entry, none, none, none, stop, target_distance=target, max_bars=HOLD)


def part_a() -> dict:
    trades, per = [], {}
    for pair in NEW_MARKETS:
        hourly = data.load_klines(pair, cs.START, cs.END, "1h")
        bars = data.resample(hourly, 4)
        t = [{**x, "market": pair} for x in engine.run(bars, cs._baseline(bars), data.load_funding(pair, cs.START, cs.END)).trades]
        trades += t
        per[pair] = round(sum(x["r"] for x in t), 1)
    s, p = summarize(trades), bootstrap_p_mean_positive(trades)
    positive_r = sum(v for v in per.values() if v > 0)
    largest = max((v / positive_r for v in per.values() if v > 0), default=1.0)
    reasons = []
    if s.get("mean_r", 0) <= 0 or p < 0.90:
        reasons.append(f"mean R {s.get('mean_r')} / P {p:.3f}")
    if (s.get("profit_factor") or 0) < 1.2:
        reasons.append(f"PF {s.get('profit_factor')} < 1.2")
    if sum(v > 0 for v in per.values()) < 6:
        reasons.append("fewer than 6 markets positive")
    if largest > 0.30:
        reasons.append(f"one market is {largest:.0%} of positive R")
    return {**s, "p_mean_positive": round(p, 3), "per_market_total_r": per, "largest_share": round(largest, 3),
            "passes": not reasons, "reasons": reasons}


def part_b() -> dict:
    hourly_data, four_hour = {}, {}
    for pair in LIVE_MARKETS:
        hourly = data.load_klines(pair, cs.START, cs.END, "1h")
        funding = data.load_funding(pair, cs.START, cs.END)
        hourly_data[pair] = (hourly, funding)
        four_hour[pair] = (data.resample(hourly, 4), funding)
    base_returns, _ = cs._trade_strategy(cs._baseline, four_hour)
    returns, trades = cs._trade_strategy(capitulation_rebound, hourly_data)
    cs.N_TRIALS, cs.GATE["min_markets_positive"] = 181, 12
    prior = [1.32, -0.63, 0.68, -0.12, 0.76]                    # combination-study trial Sharpes
    row = cs.evaluate("capitulation_rebound", returns, trades, prior + [validation.stats(returns)["sharpe"]])
    corr = cs._correlation(returns, base_returns)
    row["corr_with_donchian"] = corr
    if corr is None or corr > 0.5:
        row["passes"] = False
        row.setdefault("reasons", []).append(f"correlation with Donchian {corr} > 0.5")
    return row


def main() -> None:
    report = {"A_ten_more_markets": part_a(), "B_capitulation_rebound": part_b()}
    print(json.dumps(report, indent=1, default=str))
    Path("research_output").mkdir(exist_ok=True)
    Path("research_output/crypto_engine2_study.json").write_text(json.dumps(
        {"generated_at": datetime.now(timezone.utc).isoformat(), "report": report}, indent=2, default=str) + "\n")


if __name__ == "__main__":
    main()
