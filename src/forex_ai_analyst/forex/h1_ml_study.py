"""Universal intraday FX model on Dukascopy hourly bid/ask candles (docs/H1_ML_STUDY.md).

    python3 -m forex_ai_analyst.forex.h1_ml_study      (needs: pip install scikit-learn numpy)
"""
from __future__ import annotations

import json
import math
import random
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np

from forex_ai_analyst.forex import dukascopy

MARKETS = ("EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD", "EURJPY", "GBPJPY", "EURGBP")
USD_SIGN = {"EURUSD": -1, "GBPUSD": -1, "AUDUSD": -1, "NZDUSD": -1, "USDJPY": 1, "USDCAD": 1, "USDCHF": 1}
FIRST_MONTH, LAST_MONTH = (2010, 1), (2026, 9)
FIRST_TEST_YEAR, STEP, HOLD = 2014, 4, 4
LONG_T, SHORT_T, MARKUP = 0.55, 0.45, 0.00003
SPLIT = "2020-01-01"
HOUR = 3_600_000
GATE = {"min_trades": 1000, "min_p": 0.95, "min_pf": 1.2, "min_positive": 6}


def months() -> list[tuple[int, int]]:
    out, (y, m) = [], FIRST_MONTH
    while (y, m) <= LAST_MONTH:
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def load(market: str) -> list[dict]:
    bars = [b for y, m in months() for b in dukascopy.hours(market, y, m)]
    # Dukascopy writes flat placeholder candles when the market is shut; keep only bars that traded
    return [b for b in bars if b["bid_high"] > b["bid_low"]]


def ema(values: list[float], n: int) -> list[float]:
    k, out, v = 2 / (n + 1), [], values[0]
    for x in values:
        v = x * k + v * (1 - k)
        out.append(v)
    return out


def rsi(values: list[float], n: int = 14) -> list[float | None]:
    out, gain, loss = [None] * len(values), 0.0, 0.0
    for i in range(1, len(values)):
        ch = values[i] - values[i - 1]
        up, down = max(ch, 0.0), max(-ch, 0.0)
        if i <= n:
            gain, loss = gain + up / n, loss + down / n
        else:
            gain, loss = (gain * (n - 1) + up) / n, (loss * (n - 1) + down) / n
        if i >= n:
            out[i] = 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)
    return out


def market_rows(name: str, bars: list[dict], usd: dict[int, float], index: int) -> list[tuple]:
    """(features, label, meta) for one market. Bar i closes at t_i + 1h; the decision is made then."""
    t = [b["datetime"] for b in bars]
    mid_c = [(b["bid_close"] + b["ask_close"]) / 2 for b in bars]
    mid_o = [(b["bid_open"] + b["ask_open"]) / 2 for b in bars]
    hi = [(b["bid_high"] + b["ask_high"]) / 2 for b in bars]
    lo = [(b["bid_low"] + b["ask_low"]) / 2 for b in bars]
    spread = [(b["ask_close"] - b["bid_close"]) / m for b, m in zip(bars, mid_c)]
    logs = [math.log(x) for x in mid_c]
    rets = [0.0] + [logs[i] - logs[i - 1] for i in range(1, len(logs))]
    e24, e120, r14 = ema(mid_c, 24), ema(mid_c, 120), rsi(mid_c)
    rows = []
    for i in range(130, len(bars) - HOLD - 1):
        close_hour = datetime.fromtimestamp((t[i] + HOUR) / 1000, timezone.utc)
        if close_hour.hour % STEP or close_hour.weekday() >= 5:
            continue
        if t[i + 1] != t[i] + HOUR or t[i + 1 + HOLD] != t[i + 1] + HOLD * HOUR or t[i] - t[i - 120] > 200 * HOUR:
            continue                                          # gap inside the trade, or patchy history
        v24 = math.sqrt(sum(r * r for r in rets[i - 23:i + 1]) / 24) or 1e-9
        v120 = math.sqrt(sum(r * r for r in rets[i - 119:i + 1]) / 120) or 1e-9
        h24, l24 = max(hi[i - 23:i + 1]), min(lo[i - 23:i + 1])
        sp_med = sorted(spread[i - 119:i + 1])[60] or 1e-9
        feats = [logs[i] - logs[i - k] for k in (1, 4, 12, 24, 72, 120)]
        feats += [v24, v120, v24 / v120, (mid_c[i] - l24) / (h24 - l24) if h24 > l24 else 0.5,
                  (mid_c[i] - e24[i]) / mid_c[i] / v24, (mid_c[i] - e120[i]) / mid_c[i] / v24,
                  r14[i] if r14[i] is not None else 50.0, spread[i] / sp_med]
        u4 = sum(usd.get(t[j], 0.0) for j in range(i - 3, i + 1))
        u24 = sum(usd.get(t[j], 0.0) for j in range(i - 23, i + 1))
        feats += [u4, u24]
        feats += [float(close_hour.hour == h) for h in range(0, 24, STEP)]
        feats += [float(close_hour.weekday() == d) for d in range(5)]
        feats += [float(k == index) for k in range(len(MARKETS))]
        entry, exit_ = bars[i + 1], bars[i + 1 + HOLD]
        label = 1 if mid_o[i + 1 + HOLD] > mid_o[i + 1] else 0
        meta = {"market": name, "day": close_hour.date().isoformat(), "entry_ms": entry["datetime"],
                "exit_day": datetime.fromtimestamp(exit_["datetime"] / 1000, timezone.utc).date().isoformat(),
                "long": exit_["bid_open"] / entry["ask_open"] - 1 - MARKUP,
                "short": 1 - exit_["ask_open"] / entry["bid_open"] - MARKUP}
        rows.append((feats, label, meta))
    return rows


def usd_index(data: dict[str, list[dict]]) -> dict[int, float]:
    """Average signed hourly log return of the USD legs, keyed by bar open time (includes that bar's close)."""
    acc: dict[int, list[float]] = {}
    for pair, sign in USD_SIGN.items():
        bars = data[pair]
        for a, b in zip(bars, bars[1:]):
            if b["datetime"] - a["datetime"] == HOUR:
                ma = (a["bid_close"] + a["ask_close"]) / 2
                mb = (b["bid_close"] + b["ask_close"]) / 2
                acc.setdefault(b["datetime"], []).append(sign * math.log(mb / ma))
    return {k: sum(v) / len(v) for k, v in acc.items()}


def build(data: dict[str, list[dict]]):
    usd = usd_index(data)
    X, y, meta = [], [], []
    for k, name in enumerate(MARKETS):
        for f, label, m in market_rows(name, data[name], usd, k):
            X.append(f)
            y.append(label)
            meta.append(m)
    return np.array(X), np.array(y), meta


def models():
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    return {"logistic": lambda: make_pipeline(StandardScaler(), LogisticRegression(C=0.1, max_iter=2000)),
            "gradient_boosting": lambda: HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05,
                                                                        max_iter=200, random_state=0)}


def walk_forward(X, y, meta, factory) -> list[dict]:
    trades = []
    for year in range(FIRST_TEST_YEAR, LAST_MONTH[0] + 1):
        cutoff = f"{year}-01-01"
        train = np.array([m["exit_day"] < cutoff for m in meta])
        test = np.array([m["day"][:4] == str(year) for m in meta])
        if train.sum() < 5000 or test.sum() == 0:
            continue
        prob = factory().fit(X[train], y[train]).predict_proba(X[test])[:, 1]
        for p, m in zip(prob, [m for m, s in zip(meta, test) if s]):
            if p >= LONG_T:
                trades.append({**m, "net": m["long"], "side": 1, "prob": float(p)})
            elif p <= SHORT_T:
                trades.append({**m, "net": m["short"], "side": -1, "prob": float(p)})
    return trades


def bootstrap_p(trades: list[dict], runs: int = 2000, seed: int = 11) -> float:
    by_day: dict[str, float] = {}
    for t in trades:
        by_day[t["day"]] = by_day.get(t["day"], 0.0) + t["net"]
    values = list(by_day.values())
    rng = random.Random(seed)
    return sum(sum(rng.choice(values) for _ in values) > 0 for _ in range(runs)) / runs


def evaluate(trades: list[dict]) -> dict:
    if not trades:
        return {"trades": 0, "passes": False, "reasons": ["no trades"]}
    net = [t["net"] for t in trades]
    gains, losses = sum(x for x in net if x > 0), -sum(x for x in net if x < 0)
    halves = [[t["net"] for t in trades if t["day"] < SPLIT], [t["net"] for t in trades if t["day"] >= SPLIT]]
    per: dict[str, float] = {}
    for t in trades:
        per[t["market"]] = per.get(t["market"], 0.0) + t["net"]
    s = {"trades": len(net), "per_year": round(len(net) / (LAST_MONTH[0] - FIRST_TEST_YEAR + 0.75), 1),
         "hit_rate": round(sum(x > 0 for x in net) / len(net), 3), "mean_net_bp": round(sum(net) / len(net) * 1e4, 3),
         "profit_factor": round(gains / losses, 3) if losses else None, "p_mean_positive": bootstrap_p(trades),
         "halves_mean_bp": [round(sum(h) / len(h) * 1e4, 3) if h else None for h in halves],
         "markets_positive": sum(v > 0 for v in per.values()),
         "per_market_total_pct": {k: round(v * 100, 1) for k, v in per.items()}}
    reasons = []
    if s["trades"] < GATE["min_trades"]:
        reasons.append("too few trades")
    if s["p_mean_positive"] < GATE["min_p"]:
        reasons.append(f"P {s['p_mean_positive']:.3f} < {GATE['min_p']}")
    if (s["profit_factor"] or 0) < GATE["min_pf"]:
        reasons.append(f"PF {s['profit_factor']} < {GATE['min_pf']}")
    if not all(halves) or min(sum(h) for h in halves) <= 0:
        reasons.append("not positive in both halves")
    if s["markets_positive"] < GATE["min_positive"]:
        reasons.append(f"only {s['markets_positive']}/10 markets positive")
    return {**s, "passes": not reasons, "reasons": reasons}


def prefetch() -> None:
    dukascopy.prefetch([(m, date(y, mo, 1)) for m in MARKETS for y, mo in months()], hourly=True)


def main() -> None:
    prefetch()
    data = {m: load(m) for m in MARKETS}
    X, y, meta = build(data)
    print(f"dataset: {len(y)} rows, {X.shape[1]} features, base rate up {y.mean():.3f}")
    report = {}
    for name, factory in models().items():
        report[name] = evaluate(walk_forward(X, y, meta, factory))
        print(f"{name}: {json.dumps({k: v for k, v in report[name].items() if k != 'per_market_total_pct'})}")
    Path("research_output").mkdir(exist_ok=True)
    Path("research_output/h1_ml_study.json").write_text(json.dumps(
        {"generated_at": datetime.now(timezone.utc).isoformat(), "gate": GATE, "report": report}, indent=2) + "\n")


if __name__ == "__main__":
    main()
