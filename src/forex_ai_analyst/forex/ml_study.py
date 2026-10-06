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
import math
import random
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

import numpy as np

from forex_ai_analyst.forex import fx_factors
from forex_ai_analyst.forex.data import Market, cost_fraction, load_yahoo
from forex_ai_analyst.forex.development import _fvg_touch, _sweeps
from forex_ai_analyst.forex.regime_system import features as base_features
from forex_ai_analyst.forex.regime_system_study import FX_ONLY
from forex_ai_analyst.lab.candidates import confirmed_pivots, rsi

HORIZON, STEP = 5, 5
FIRST_TEST_YEAR = 2010
LONG_T, SHORT_T = 0.55, 0.45
GATE = {"min_trades": 200, "min_bootstrap_p": 0.90, "min_profit_factor": 1.2, "min_market_share_positive": 0.6}
CARRY = {"EURUSD": ("EUR", "USD"), "GBPUSD": ("GBP", "USD"), "AUDUSD": ("AUD", "USD"), "NZDUSD": ("NZD", "USD"),
         "USDCAD": ("USD", "CAD"), "USDCHF": ("USD", "CHF"), "USDJPY": ("USD", "JPY"), "EURJPY": ("EUR", "JPY"),
         "GBPJPY": ("GBP", "JPY"), "XAUUSD": (None, "USD")}
USD_LEGS = {"EURUSD": -1, "GBPUSD": -1, "AUDUSD": -1, "NZDUSD": -1, "USDJPY": 1, "USDCAD": 1, "USDCHF": 1}


def _day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).date().isoformat()


def _lagged(series: dict[str, float], day: str) -> float | None:
    """Last value strictly before `day` (cross-asset data is used with a one-day lag)."""
    keys = [d for d in series if d < day]
    return series[max(keys)] if keys else None


def build_dataset() -> tuple[np.ndarray, np.ndarray, list[dict], list[str]]:
    spx = {_day(b["datetime"]): b["close"] for b in load_yahoo(Market("US500", "^GSPC", 0, pct=True), "1d")}
    vix = {_day(b["datetime"]): b["close"] for b in load_yahoo(Market("VIX", "^VIX", 0, pct=True), "1d")}
    spx_days, vix_days = sorted(spx), sorted(vix)
    rates = fx_factors.load_rates()
    all_bars = {m.name: fx_factors.load_yahoo(m, "1d") for m in FX_ONLY}
    closes_by_day = {name: {_day(b["datetime"]): b["close"] for b in bars} for name, bars in all_bars.items()}
    sorted_days = {name: sorted(series) for name, series in closes_by_day.items()}

    def spx_r20(day):
        idx = np.searchsorted(spx_days, day) - 1       # last day strictly before
        return spx[spx_days[idx]] / spx[spx_days[idx - 20]] - 1 if idx >= 20 else None

    def vix_feats(day):
        idx = np.searchsorted(vix_days, day) - 1
        if idx < 5:
            return None, None
        return vix[vix_days[idx]], vix[vix_days[idx]] / vix[vix_days[idx - 5]] - 1

    names = FX_ONLY
    rows, labels, meta = [], [], []
    feature_names: list[str] = []
    for m_index, market in enumerate(names):
        bars = all_bars[market.name]
        f = base_features(bars)
        closes = f["close"]
        rsi14 = rsi(closes, 14)
        swept_low, swept_high = _sweeps(bars)
        fvg_bull, fvg_bear = _fvg_touch(bars)
        swing_high, swing_low, _ = confirmed_pivots(bars)
        days = [_day(b["datetime"]) for b in bars]
        cost = cost_fraction(market, median(closes))
        base, quote = CARRY[market.name]
        for i in range(200, len(bars) - HORIZON - 1, STEP):
            a, mean, sd = f["atr"][i], f["bb"][0][i], f["bb"][1][i]
            if None in (a, f["adx"][i], f["er"][i], f["vol_ratio"][i], rsi14[i], f["rsi2"][i], mean) or not a or not sd:
                continue
            day = days[i]
            s20 = spx_r20(day)
            vix_level, vix_chg = vix_feats(day)
            month = day[:7]
            prev_month = f"{int(month[:4]) - (month[5:] == '01')}-{(int(month[5:]) - 2) % 12 + 1:02d}"
            rq = fx_factors._rate(rates, quote, prev_month)
            rb = fx_factors._rate(rates, base, prev_month) if base else 0.0
            usd = []
            for pair, sign in USD_LEGS.items():
                series, keys = closes_by_day[pair], sorted_days[pair]
                idx = int(np.searchsorted(keys, day, side="right")) - 1     # last day <= today
                if idx >= 20:
                    usd.append(sign * (series[keys[idx]] / series[keys[idx - 20]] - 1))
            if None in (s20, vix_level, rq, rb) or len(usd) < 5:
                continue
            ret = lambda k: math.log(closes[i] / closes[i - k])   # noqa: E731
            daily = [math.log(closes[j] / closes[j - 1]) for j in range(i - 19, i + 1)]
            vol20 = float(np.std(daily))
            next_month = f"{int(month[:4]) + (month[5:] == '12')}-{int(month[5:]) % 12 + 1:02d}-01"
            to_month_end = int(np.busday_count(np.datetime64(day) + np.timedelta64(1, "D"), np.datetime64(next_month)))   # calendar only
            feats = {
                "r1": ret(1), "r5": ret(5), "r20": ret(20), "r60": ret(60), "r120": ret(120),
                "vol20": vol20, "atr_ratio": f["vol_ratio"][i], "adx": f["adx"][i], "er": f["er"][i],
                "rsi14": rsi14[i], "rsi2": f["rsi2"][i], "bb_z": (closes[i] - mean) / sd,
                "d_ema20": (closes[i] - f["ema20"][i]) / a, "d_ema50": (closes[i] - f["ema50"][i]) / a,
                "d_ema200": (closes[i] - f["ema200"][i]) / a,
                "sweep_low": float(swept_low[i]), "sweep_high": float(swept_high[i]),
                "fvg_bull": float(fvg_bull[i]), "fvg_bear": float(fvg_bear[i]),
                "d_swing_high": (swing_high[i] - closes[i]) / a if swing_high[i] else 0.0,
                "d_swing_low": (closes[i] - swing_low[i]) / a if swing_low[i] else 0.0,
                "dow": datetime.fromisoformat(day).weekday(), "month_end_soon": float(to_month_end < 5),
                "carry": rb - rq, "spx_r20": s20, "vix": vix_level, "vix_chg5": vix_chg, "usd_mom20": sum(usd) / len(usd),
            }
            for k, other in enumerate(names):
                feats[f"mkt_{other.name}"] = float(k == m_index)
            if not feature_names:
                feature_names = list(feats)
            entry, exit_ = bars[i + 1]["open"], bars[i + HORIZON]["close"]
            fwd = exit_ / entry - 1
            rows.append([feats[k] for k in feature_names])
            labels.append(1 if fwd > 0 else 0)
            meta.append({"market": market.name, "day": day, "exit_day": days[i + HORIZON], "fwd": fwd, "cost": cost})
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
