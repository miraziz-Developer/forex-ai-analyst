"""Walk-forward machine-learning FX study (docs/ML_STUDY.md).

    python3 -m forex_ai_analyst.forex.ml_study      (needs: pip install scikit-learn numpy)

One "universal" model for 9 currency pairs + gold, retrained every January on
everything known before it (expanding window, 5-day purge), then used for that
year only. Features at day i use data through day i (cross-asset data through
i-1, interest rates through the previous month); trades enter at the next open
and are held 5 trading days. A trade is taken only when the model is confident.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from forex_ai_analyst.forex.ml_features import feature_row, load_context
from forex_ai_analyst.forex.regime_system_study import FX_ONLY

HORIZON, STEP = 5, 5
FIRST_TEST_YEAR = 2010
LONG_T, SHORT_T = 0.55, 0.45
GATE = {"min_trades": 200, "min_bootstrap_p": 0.90, "min_profit_factor": 1.2, "min_market_share_positive": 0.6}


def build_dataset() -> tuple[np.ndarray, np.ndarray, list[dict], list[str]]:
    ctx = load_context(FX_ONLY)
    rows, labels, meta = [], [], []
    feature_names: list[str] = []
    for market in FX_ONLY:
        c = ctx.caches[market.name]
        for i in range(200, len(c.bars) - HORIZON - 1, STEP):
            feats = feature_row(ctx, market.name, i)
            if feats is None:
                continue
            if not feature_names:
                feature_names = list(feats)
            entry, exit_ = c.bars[i + 1]["open"], c.bars[i + HORIZON]["close"]
            fwd = exit_ / entry - 1
            rows.append([feats[k] for k in feature_names])
            labels.append(1 if fwd > 0 else 0)
            meta.append({"market": market.name, "day": c.days[i], "exit_day": c.days[i + HORIZON], "fwd": fwd,
                         "cost": c.cost})
    return np.array(rows, dtype=float), np.array(labels), meta, feature_names


def models():
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    return {"logistic": lambda: make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=2000)),
            "gradient_boosting": lambda: HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=200,
                                                                        random_state=0)}


def walk_forward(X, y, meta, factory) -> list[dict]:
    trades = []
    years = sorted({m["day"][:4] for m in meta})
    for year in [y_ for y_ in years if int(y_) >= FIRST_TEST_YEAR]:
        cutoff = f"{year}-01-01"
        train = np.array([m["exit_day"] < cutoff for m in meta])           # purge: label must end before
        test = np.array([m["day"][:4] == year for m in meta])
        if train.sum() < 1000 or test.sum() == 0:
            continue
        model = factory()
        model.fit(X[train], y[train])
        prob = model.predict_proba(X[test])[:, 1]
        for p, m in zip(prob, [m for m, t in zip(meta, test) if t]):
            if p >= LONG_T or p <= SHORT_T:
                side = 1 if p >= LONG_T else -1
                trades.append({**m, "prob": float(p), "side": side, "net": side * m["fwd"] - m["cost"],
                               "hit": side * m["fwd"] > 0})
    return trades


def bootstrap_p(trades, runs=4000, seed=11) -> float:
    by_day: dict[str, list[float]] = {}
    for t in trades:
        by_day.setdefault(t["day"], []).append(t["net"])
    days = list(by_day)
    if len(days) < 10:
        return 0.0
    rng, positive = random.Random(seed), 0
    for _ in range(runs):
        sample = [x for _ in days for x in by_day[rng.choice(days)]]
        positive += sum(sample) > 0
    return positive / runs


def evaluate(trades) -> dict:
    if not trades:
        return {"trades": 0, "passes": False, "reasons": ["no trades"]}
    net = [t["net"] for t in trades]
    gains, losses = sum(x for x in net if x > 0), -sum(x for x in net if x < 0)
    first = [t["net"] for t in trades if t["day"] < "2018-01-01"]
    second = [t["net"] for t in trades if t["day"] >= "2018-01-01"]
    per_market: dict[str, float] = {}
    for t in trades:
        per_market[t["market"]] = per_market.get(t["market"], 0.0) + t["net"]
    positive = sum(v > 0 for v in per_market.values())
    p = bootstrap_p(trades)
    s = {"trades": len(net), "trades_per_year": round(len(net) / 16.8, 1), "hit_rate": round(sum(t["hit"] for t in trades) / len(net), 3),
         "mean_net_pct": round(sum(net) / len(net) * 100, 4), "profit_factor": round(gains / losses, 3) if losses else None,
         "halves_mean_pct": [round(sum(h) / len(h) * 100, 4) if h else None for h in (first, second)],
         "p_mean_positive": round(p, 3), "markets_positive": f"{positive}/{len(per_market)}",
         "per_market_total_pct": {k: round(v * 100, 1) for k, v in per_market.items()}}
    reasons = []
    if s["trades"] < GATE["min_trades"]:
        reasons.append(f"{s['trades']} trades < {GATE['min_trades']}")
    if p < GATE["min_bootstrap_p"]:
        reasons.append(f"P(mean > 0) {p:.2f} < {GATE['min_bootstrap_p']}")
    if (s["profit_factor"] or 0) < GATE["min_profit_factor"]:
        reasons.append(f"profit factor {s['profit_factor']} < {GATE['min_profit_factor']}")
    if not first or not second or min(sum(first), sum(second)) <= 0:
        reasons.append("not positive in both halves (2010-2017, 2018+)")
    if positive / len(per_market) < GATE["min_market_share_positive"]:
        reasons.append(f"only {positive}/{len(per_market)} markets positive")
    return {**s, "passes": not reasons, "reasons": reasons}


def main() -> None:
    X, y, meta, names = build_dataset()
    print(f"dataset: {len(y)} samples, {len(names)} features, base rate up {y.mean():.3f}")
    report = {}
    for name, factory in models().items():
        report[name] = evaluate(walk_forward(X, y, meta, factory))
        r = report[name]
        print(f"{name}: {json.dumps({k: v for k, v in r.items() if k != 'per_market_total_pct'})}")
    out = Path("research_output")
    out.mkdir(exist_ok=True)
    (out / "ml_study.json").write_text(json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(), "gate": GATE,
                                                   "features": names, "report": report}, indent=2) + "\n")


if __name__ == "__main__":
    main()
