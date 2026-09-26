"""Combination study: can the swing ideas discussed with the owner add to Donchian 4h?

Pre-registered in docs/COMBINATION_STUDY.md before this was first run. Every
strategy uses fixed textbook parameters (no search) on the ten live markets,
2021-01-01..2026-09-01, with the lab engine's taker costs and historical
funding. Each market is a 1%-risk sleeve; a strategy's return stream is the
equal-weight average of its ten sleeves.

    python3 -m forex_ai_analyst.lab.combination_study
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from statistics import pstdev

from forex_ai_analyst.lab import data, engine, validation
from forex_ai_analyst.lab.candidates import CANDIDATES, xs_momentum_returns
from forex_ai_analyst.lab.strategies import donchian

MARKETS = ("BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "BNB-USDT",
           "DOGE-USDT", "ADA-USDT", "LINK-USDT", "AVAX-USDT", "LTC-USDT")
START, END = datetime(2021, 1, 1, tzinfo=timezone.utc), datetime(2026, 9, 1, tzinfo=timezone.utc)
HALVES = {"2021-2023": ("2021-01-01", "2024-01-01"), "2024-2026": ("2024-01-01", "2026-09-01")}
# Every trial run on this data so far: lab grid 108, edge lab 64, Donchian tests 2, this study 5.
N_TRIALS = 179
GATE = {"min_half_sharpe": 0.0, "min_full_sharpe": 0.5, "min_dsr": 0.90, "min_profit_factor": 1.2,
        "min_markets_positive": 6}
BASELINE = "donchian_4h (live)"


def _baseline(bars):
    return donchian(bars, entry_n=100, exit_n=20, stop_atr=3.0, sides="long")


def _trade_strategy(signal_fn, market_data) -> tuple[dict[str, float], list[dict]]:
    streams, trades = [], []
    for pair, (bars, funding) in market_data.items():
        result = engine.run(bars, signal_fn(bars), funding)
        streams.append(validation.daily_returns(result.equity))
        trades += [{**t, "pair": pair} for t in result.trades]
    return validation.combine(streams), trades


def _trade_stats(trades: list[dict], years: float) -> dict:
    if not trades:
        return {"trades": 0}
    r = [t["r"] for t in trades]
    gains, losses = sum(x for x in r if x > 0), -sum(x for x in r if x < 0)
    by_pair: dict[str, float] = {}
    for t in trades:
        by_pair[t["pair"]] = by_pair.get(t["pair"], 0.0) + t["r"]
    return {"trades": len(r), "trades_per_month": round(len(r) / years / 12, 1),
            "win_rate": round(sum(x > 0 for x in r) / len(r), 3), "mean_r": round(sum(r) / len(r), 3),
            "profit_factor": round(gains / losses, 2) if losses else None,
            "markets_positive": sum(v > 0 for v in by_pair.values()),
            "avg_hold_days": round(sum(t["bars"] for t in trades) / len(trades) * 4 / 24, 1)}


def _correlation(a: dict[str, float], b: dict[str, float]) -> float | None:
    days = sorted(set(a) & set(b))
    if len(days) < 30:
        return None
    x, y = [a[d] for d in days], [b[d] for d in days]
    mx, my = sum(x) / len(x), sum(y) / len(y)
    cov = sum((p - mx) * (q - my) for p, q in zip(x, y)) / len(x)
    sx, sy = pstdev(x), pstdev(y)
    return round(cov / (sx * sy), 3) if sx and sy else None


def evaluate(name: str, returns: dict[str, float], trades: list[dict] | None, all_sharpes: list[float]) -> dict:
    full = validation.stats(returns)
    halves = {k: validation.stats(validation.window(returns, *span)) for k, span in HALVES.items()}
    values = [returns[d] for d in sorted(returns)]
    dsr = validation.deflated_sharpe(values, all_sharpes, n_trials=N_TRIALS)
    row = {"name": name, "full": full, "halves": halves, "dsr": round(dsr, 3) if dsr is not None else None}
    reasons = []
    if any(h.get("sharpe", 0) <= GATE["min_half_sharpe"] for h in halves.values()):
        reasons.append("not positive in both halves")
    if full.get("sharpe", 0) < GATE["min_full_sharpe"]:
        reasons.append(f"full Sharpe {full.get('sharpe')} < {GATE['min_full_sharpe']}")
    if dsr is None or dsr < GATE["min_dsr"]:
        reasons.append(f"DSR {row['dsr']} < {GATE['min_dsr']} (N={N_TRIALS})")
    if trades is not None:
        row["trades"] = _trade_stats(trades, (END - START).days / 365)
        pf = row["trades"].get("profit_factor")
        if pf is None or pf < GATE["min_profit_factor"]:
            reasons.append(f"profit factor {pf} < {GATE['min_profit_factor']}")
        if row["trades"].get("markets_positive", 0) < GATE["min_markets_positive"]:
            reasons.append(f"{row['trades'].get('markets_positive')} markets positive < {GATE['min_markets_positive']}")
    row["passes"], row["reasons"] = not reasons, reasons
    return row


def portfolio(streams: dict[str, dict[str, float]], members: list[str], base_halves: dict) -> dict:
    """Inverse-volatility weights estimated on 2021-2023 only, scaled to the baseline's volatility."""
    vols = {m: pstdev(list(validation.window(streams[m], *HALVES["2021-2023"]).values())) or 1.0 for m in members}
    raw = {m: 1 / vols[m] for m in members}
    weights = {m: raw[m] / sum(raw.values()) for m in members}
    combined = validation.combine([streams[m] for m in members], [weights[m] for m in members])
    first_half_vol = pstdev(list(validation.window(combined, *HALVES["2021-2023"]).values())) or 1.0
    scale = vols[members[0]] / first_half_vol
    combined = {d: r * scale for d, r in combined.items()}
    result = {"members": members, "weights": {m: round(w * scale, 3) for m, w in weights.items()},
              "full": validation.stats(combined),
              "halves": {k: validation.stats(validation.window(combined, *span)) for k, span in HALVES.items()}}
    result["beats_donchian_in_both_halves"] = len(members) > 1 and all(
        result["halves"][k]["sharpe"] > base_halves[k]["sharpe"] for k in HALVES)
    return result


def run() -> dict:
    market_data, daily, funding = {}, {}, {}
    for pair in MARKETS:
        hourly = data.load_klines(pair, START, END, "1h")
        funding[pair] = data.load_funding(pair, START, END)
        market_data[pair] = (data.resample(hourly, 4), funding[pair])
        daily[pair] = data.resample(hourly, 24)

    streams, trade_lists = {}, {}
    streams[BASELINE], trade_lists[BASELINE] = _trade_strategy(_baseline, market_data)
    for name, fn in CANDIDATES.items():
        streams[name], trade_lists[name] = _trade_strategy(fn, market_data)
    streams["xs_momentum"], trade_lists["xs_momentum"] = xs_momentum_returns(daily, funding), None

    all_sharpes = [validation.stats(s)["sharpe"] for s in streams.values()]
    rows = []
    for name, returns in streams.items():
        row = evaluate(name, returns, trade_lists[name], all_sharpes)
        row["corr_with_donchian"] = _correlation(returns, streams[BASELINE])
        rows.append(row)
    members = [BASELINE] + [r["name"] for r in rows[1:] if r["passes"]]
    return {"generated_at": datetime.now(timezone.utc).isoformat(), "markets": MARKETS, "gate": GATE,
            "n_trials": N_TRIALS, "strategies": rows,
            "portfolio": portfolio(streams, members, rows[0]["halves"])}


def main() -> None:
    report = run()
    out = Path("research_output") / "combination_study.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=1, default=str))


if __name__ == "__main__":
    main()
