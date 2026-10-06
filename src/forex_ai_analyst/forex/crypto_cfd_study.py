"""Crypto Donchian 4h on CFD costs with overnight swap (docs/CRYPTO_CFD_STUDY.md).

    python3 -m forex_ai_analyst.forex.crypto_cfd_study
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from forex_ai_analyst.forex.study import bootstrap_p_mean_positive, summarize
from forex_ai_analyst.lab import data, engine
from forex_ai_analyst.lab.strategies import donchian

COINS = ("BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "BNB-USDT", "DOGE-USDT", "ADA-USDT", "LINK-USDT",
         "AVAX-USDT", "LTC-USDT")
START, END = datetime(2021, 1, 1, tzinfo=timezone.utc), datetime(2026, 9, 1, tzinfo=timezone.utc)
SWAPS = (0.10, 0.20, 0.30)
PARAMS = {"entry_n": 100, "exit_n": 20, "stop_atr": 3.0, "sides": "long"}


def spread(coin: str) -> float:
    return 0.0015 if coin.startswith(("BTC", "ETH")) else 0.0030


def trades_for(coin: str) -> list[dict]:
    bars = data.resample(data.load_klines(coin, START, END), 4)
    costs = engine.Costs(fee_pct_per_side=spread(coin) / 2 * 100, slippage_pct_per_side=0.0, charge_funding=False)
    return [{**t, "coin": coin} for t in engine.run(bars, donchian(bars, **PARAMS), costs=costs).trades]


def with_swap(trades: list[dict], swap: float) -> list[dict]:
    out = []
    for t in trades:
        days = (t["exit_time"] - t["entry_time"]) / 86_400_000
        cost = t["qty"] * t["entry"] * swap * days / 365
        out.append({**t, "r": (t["net"] - cost) / t["risk"] if t["risk"] else 0.0, "days": days})
    return out


def portfolio(trades: list[dict], risk: float) -> dict:
    events = sorted([(t["entry_time"], 0, i) for i, t in enumerate(trades)] +
                    [(t["exit_time"], 1, i) for i, t in enumerate(trades)])
    eq = peak = 1.0
    dd, alloc = 0.0, {}
    for _, kind, i in events:
        if kind == 0:
            alloc[i] = eq * risk
        else:
            eq += alloc.pop(i) * trades[i]["r"]
            peak, dd = max(peak, eq), min(dd, eq / peak - 1)
    years = (END - START).days / 365.25
    return {"cagr": round(eq ** (1 / years) - 1, 4), "max_drawdown": round(dd, 4)}


def main() -> None:
    raw = {c: trades_for(c) for c in COINS}
    report = {}
    for swap in SWAPS:
        all_t = [t for c in COINS for t in with_swap(raw[c], swap)]
        s, p = summarize(all_t), bootstrap_p_mean_positive(all_t)
        per = {c: round(sum(t["r"] for t in all_t if t["coin"] == c), 1) for c in COINS}
        row = {**s, "p_mean_positive": round(p, 3), "coins_positive": sum(v > 0 for v in per.values()),
               "avg_days_held": round(sum(t["days"] for t in all_t) / len(all_t), 1), "per_coin_total_r": per,
               "portfolio_0.5pct": portfolio(all_t, 0.005), "portfolio_1pct": portfolio(all_t, 0.01)}
        if swap == 0.20:
            reasons = []
            if (s.get("profit_factor") or 0) < 1.3:
                reasons.append(f"PF {s.get('profit_factor')} < 1.3")
            if p < 0.95:
                reasons.append(f"P {p:.3f} < 0.95")
            if row["coins_positive"] < 6:
                reasons.append(f"only {row['coins_positive']}/10 coins positive")
            row.update(passes=not reasons, reasons=reasons)
        report[f"swap_{int(swap * 100)}pct"] = row
        print(f"swap {swap:.0%}: {json.dumps({k: v for k, v in row.items() if k != 'per_coin_total_r'})}")
        print(f"    per coin R: {per}")
    Path("research_output").mkdir(exist_ok=True)
    Path("research_output/crypto_cfd_study.json").write_text(json.dumps(
        {"generated_at": datetime.now(timezone.utc).isoformat(), "report": report}, indent=2) + "\n")


if __name__ == "__main__":
    main()
