"""FX ML v3: weekly cross-sectional currency ranking (docs/ML_XS_STUDY.md).

    python3 -m forex_ai_analyst.forex.ml_xs      (needs: pip install scikit-learn numpy)
"""
from __future__ import annotations

import json
import math
import random
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import pstdev

import numpy as np

from forex_ai_analyst.forex import cot, fx_factors
from forex_ai_analyst.forex.data import MARKETS, cost_fraction, load_yahoo

CCYS = ("USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF")
PAIR = fx_factors.PAIR                     # ccy -> (USD pair, inverted)
HORIZON, FIRST_TEST_YEAR, TOP = 5, 2010, 2
SWAP_PER_YEAR = 0.01
FEATURES = ("mom5", "mom20", "mom60", "mom120", "mom250", "ma200_z", "vol20", "carry", "cot_net", "cot_pct52",
            "cot_chg4")
GATE = {"min_weeks": 600, "min_p": 0.95, "min_pf": 1.2, "min_sharpe": 0.5, "min_positive": 5}


def day_of(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).date().isoformat()


def load_panel():
    """Common trading days and, per currency, open/close of c/USD (USD = 1)."""
    raw = {}
    for c, (pair, inverted) in PAIR.items():
        bars = {day_of(b["datetime"]): b for b in load_yahoo(MARKETS[pair], "1d")}
        raw[c] = {d: ((1 / b["open"], 1 / b["close"]) if inverted else (b["open"], b["close"])) for d, b in bars.items()}
    days = sorted(set.intersection(*(set(v) for v in raw.values())))
    opens = {c: [raw[c][d][0] for d in days] for c in PAIR}
    closes = {c: [raw[c][d][1] for d in days] for c in PAIR}
    opens["USD"] = closes["USD"] = [1.0] * len(days)
    return days, opens, closes


def relative_log(closes: dict) -> dict[str, list[float]]:
    n = len(closes["USD"])
    logs = {c: [math.log(x) for x in closes[c]] for c in CCYS}
    mean = [sum(logs[c][i] for c in CCYS) / len(CCYS) for i in range(n)]
    return {c: [logs[c][i] - mean[i] for i in range(n)] for c in CCYS}


def cot_for(positions: dict, ccy: str, day: str) -> dict | None:
    pair, inverted = PAIR[ccy]
    f = cot.features_at(positions, pair, day)
    if f is None:
        return None
    if inverted:                               # USDJPY exposure is -JPY; flip back to the currency's own view
        f = {"cot_net": -f["cot_net"], "cot_pct52": 100 - f["cot_pct52"], "cot_chg4": -f["cot_chg4"]}
    return f


def raw_features(rel: dict, rates: dict, positions: dict, days: list[str], i: int) -> dict[str, dict] | None:
    if i < 260:
        return None
    month = days[i][:7]
    y, m = int(month[:4]), int(month[5:])
    prev_month = f"{y - (m == 1)}-{(m - 2) % 12 + 1:02d}"
    rate = {c: fx_factors._rate(rates, c, prev_month) for c in CCYS}
    if None in rate.values():
        return None
    avg_rate = sum(rate.values()) / len(CCYS)
    cots = {c: cot_for(positions, c, days[i]) for c in CCYS if c != "USD"}
    if None in cots.values():
        return None
    cots["USD"] = {k: -sum(v[k] for v in cots.values()) / 7 for k in ("cot_net", "cot_chg4")}
    cots["USD"]["cot_pct52"] = 100 - sum(v["cot_pct52"] for c, v in cots.items() if c != "USD") / 7
    out = {}
    for c in CCYS:
        r = rel[c]
        changes = [r[j] - r[j - 1] for j in range(i - 19, i + 1)]
        vol = pstdev(changes) or 1e-9
        out[c] = {"mom5": r[i] - r[i - 5], "mom20": r[i] - r[i - 20], "mom60": r[i] - r[i - 60],
                  "mom120": r[i] - r[i - 120], "mom250": r[i] - r[i - 250],
                  "ma200_z": (r[i] - sum(r[i - 199:i + 1]) / 200) / vol, "vol20": vol,
                  "carry": rate[c] - avg_rate, **cots[c]}
    return out


def zscore_across(feats: dict[str, dict]) -> dict[str, list[float]]:
    rows = {c: [] for c in CCYS}
    for name in FEATURES:
        values = [feats[c][name] for c in CCYS]
        mu, sd = sum(values) / len(values), pstdev(values)
        for c, v in zip(CCYS, values):
            rows[c].append((v - mu) / sd if sd > 0 else 0.0)
    return rows


def build():
    days, opens, closes = load_panel()
    rel = relative_log(closes)
    rates, positions = fx_factors.load_rates(), cot.load_positions()
    X, y, meta = [], [], []
    for i, d in enumerate(days):
        if date.fromisoformat(d).weekday() != 0 or i + HORIZON >= len(days):
            continue
        feats = raw_features(rel, rates, positions, days, i)
        if feats is None:
            continue
        z = zscore_across(feats)
        # relative return next open -> close at i + HORIZON, in the basket's terms
        ret = {c: math.log(closes[c][i + HORIZON] / opens[c][i + 1]) for c in CCYS}
        basket = sum(ret.values()) / len(CCYS)
        for c in CCYS:
            X.append(z[c])
            y.append((ret[c] - basket) / feats[c]["vol20"])
            meta.append({"day": d, "exit_day": days[i + HORIZON], "ccy": c,
                         "usd_ret": closes[c][i + HORIZON] / opens[c][i + 1] - 1})
    return np.array(X), np.array(y), meta


def models():
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.linear_model import Ridge
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    return {"XS1_ridge": lambda: make_pipeline(StandardScaler(), Ridge(alpha=10.0)),
            "XS2_boosting": lambda: HistGradientBoostingRegressor(max_depth=3, learning_rate=0.05, max_iter=200,
                                                                  random_state=0)}


def leg_cost() -> dict[str, float]:
    costs = {"USD": 0.0}
    for c, (pair, _) in PAIR.items():
        bars = load_yahoo(MARKETS[pair], "1d")
        costs[c] = cost_fraction(MARKETS[pair], sorted(b["close"] for b in bars)[len(bars) // 2])
    return costs


def portfolio(meta: list[dict], scores: np.ndarray, costs: dict[str, float]) -> list[dict]:
    """Weekly long-top / short-bottom returns. A currency's return vs USD; USD itself returns 0."""
    by_day: dict[str, list[tuple[float, dict]]] = {}
    for s, m in zip(scores, meta):
        by_day.setdefault(m["day"], []).append((float(s), m))
    weeks = []
    for d in sorted(by_day):
        ranked = sorted(by_day[d], key=lambda x: x[0])
        if len(ranked) != len(CCYS):
            continue
        longs, shorts = [m for _, m in ranked[-TOP:]], [m for _, m in ranked[:TOP]]
        w = 1 / TOP
        gross = w * sum(m["usd_ret"] for m in longs) - w * sum(m["usd_ret"] for m in shorts)
        cost = w * sum(costs[m["ccy"]] for m in longs + shorts) + SWAP_PER_YEAR * 2 * 7 / 365
        contrib = {m["ccy"]: w * m["usd_ret"] for m in longs}
        for m in shorts:
            contrib[m["ccy"]] = contrib.get(m["ccy"], 0.0) - w * m["usd_ret"]
        weeks.append({"day": d, "gross": gross, "net": gross - cost, "contrib": contrib,
                      "longs": [m["ccy"] for m in longs], "shorts": [m["ccy"] for m in shorts]})
    return weeks


def walk_forward(X, y, meta, factory) -> tuple[list[dict], np.ndarray]:
    years = sorted({m["day"][:4] for m in meta})
    sel_meta, sel_scores = [], []
    for year in [yr for yr in years if int(yr) >= FIRST_TEST_YEAR]:
        cutoff = f"{year}-01-01"
        train = np.array([m["exit_day"] < cutoff for m in meta])
        test = np.array([m["day"][:4] == year for m in meta])
        if train.sum() < 2000 or test.sum() == 0:
            continue
        model = factory().fit(X[train], y[train])
        sel_scores.append(model.predict(X[test]))
        sel_meta += [m for m, t in zip(meta, test) if t]
    return sel_meta, np.concatenate(sel_scores)


def bootstrap_p(values: list[float], runs: int = 4000, seed: int = 11) -> float:
    rng = random.Random(seed)
    return sum(sum(rng.choice(values) for _ in values) > 0 for _ in range(runs)) / runs


def evaluate(weeks: list[dict]) -> dict:
    net = [w["net"] for w in weeks]
    gains, losses = sum(x for x in net if x > 0), -sum(x for x in net if x < 0)
    halves = [[w["net"] for w in weeks if w["day"] < "2018-01-01"], [w["net"] for w in weeks if w["day"] >= "2018-01-01"]]
    per_ccy: dict[str, float] = {}
    for w in weeks:
        for c, v in w["contrib"].items():
            per_ccy[c] = per_ccy.get(c, 0.0) + v
    mean, sd = sum(net) / len(net), pstdev(net)
    equity = peak = 1.0
    max_dd = 0.0
    for x in net:
        equity *= 1 + x
        peak = max(peak, equity)
        max_dd = min(max_dd, equity / peak - 1)
    s = {"weeks": len(net), "hit_rate": round(sum(x > 0 for x in net) / len(net), 3),
         "mean_gross_pct": round(sum(w["gross"] for w in weeks) / len(net) * 100, 4),
         "mean_net_pct": round(mean * 100, 4), "profit_factor": round(gains / losses, 3) if losses else None,
         "sharpe": round(mean / sd * math.sqrt(52), 3) if sd else 0.0, "p_mean_positive": bootstrap_p(net),
         "halves_mean_pct": [round(sum(h) / len(h) * 100, 4) if h else None for h in halves],
         "cagr_unlevered": round(equity ** (52 / len(net)) - 1, 4), "max_drawdown": round(max_dd, 4),
         "ccys_positive": sum(v > 0 for v in per_ccy.values()),
         "per_ccy_total_pct": {k: round(v * 100, 1) for k, v in per_ccy.items()}}
    reasons = []
    if s["weeks"] < GATE["min_weeks"]:
        reasons.append("too few weeks")
    if s["p_mean_positive"] < GATE["min_p"]:
        reasons.append(f"P {s['p_mean_positive']:.3f} < {GATE['min_p']}")
    if (s["profit_factor"] or 0) < GATE["min_pf"]:
        reasons.append(f"PF {s['profit_factor']} < {GATE['min_pf']}")
    if s["sharpe"] < GATE["min_sharpe"]:
        reasons.append(f"Sharpe {s['sharpe']} < {GATE['min_sharpe']}")
    if not all(halves) or min(sum(h) for h in halves) <= 0:
        reasons.append("not positive in both halves")
    if s["ccys_positive"] < GATE["min_positive"]:
        reasons.append(f"only {s['ccys_positive']}/8 currencies positive")
    return {**s, "passes": not reasons, "reasons": reasons}


def main() -> None:
    X, y, meta = build()
    print(f"dataset: {len(y)} rows ({len(y) // len(CCYS)} Mondays), {X.shape[1]} features")
    costs = leg_cost()
    report = {}
    for name, factory in models().items():
        sel_meta, scores = walk_forward(X, y, meta, factory)
        report[name] = evaluate(portfolio(sel_meta, scores, costs))
        print(f"{name}: {json.dumps(report[name])}")
    Path("research_output").mkdir(exist_ok=True)
    Path("research_output/ml_xs_study.json").write_text(json.dumps(
        {"generated_at": datetime.now(timezone.utc).isoformat(), "gate": GATE, "features": FEATURES,
         "report": report}, indent=2) + "\n")


if __name__ == "__main__":
    main()
