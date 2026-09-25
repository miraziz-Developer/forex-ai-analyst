"""Walk-forward selection and overfitting diagnostics.

- Selection only ever sees the training window; the chosen config is then
  traded, unchanged, on the following test window. Test windows are stitched
  into one out-of-sample (OOS) return stream per family.
- The training score is a plateau score: a config's Sharpe averaged with its
  one-step grid neighbours, so an isolated lucky peak is not selected.
- The deflated Sharpe ratio (Bailey & Lopez de Prado, 2014) discounts the OOS
  Sharpe for the number of trials searched.
"""
from __future__ import annotations

import math
import random
from datetime import datetime, timezone
from statistics import NormalDist, mean, pstdev

DAYS_PER_YEAR = 365


def date_of(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).date().isoformat()


def daily_returns(equity: list[tuple[int, float]]) -> dict[str, float]:
    last: dict[str, float] = {}
    for ms, value in equity:
        last[date_of(ms)] = value
    days, out = sorted(last), {}
    for prev, day in zip(days, days[1:]):
        if last[prev] > 0:
            out[day] = last[day] / last[prev] - 1
    return out


def combine(streams: list[dict[str, float]], weights: list[float] | None = None) -> dict[str, float]:
    """Weighted sum of daily returns; a missing day counts as flat."""
    if not streams:
        return {}
    weights = weights or [1 / len(streams)] * len(streams)
    days = sorted(set().union(*streams))
    return {d: sum(w * s.get(d, 0.0) for s, w in zip(streams, weights)) for d in days}


def window(returns: dict[str, float], start: str, end: str) -> dict[str, float]:
    return {d: r for d, r in returns.items() if start <= d < end}


def sharpe(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    sd = pstdev(values)
    return mean(values) / sd * math.sqrt(DAYS_PER_YEAR) if sd else 0.0


def stats(returns: dict[str, float]) -> dict:
    values = [returns[d] for d in sorted(returns)]
    if not values:
        return {"days": 0}
    equity, peak, max_dd = 1.0, 1.0, 0.0
    for r in values:
        equity *= 1 + r
        peak = max(peak, equity)
        max_dd = min(max_dd, equity / peak - 1)
    years = len(values) / DAYS_PER_YEAR
    downside = math.sqrt(sum(min(r, 0.0) ** 2 for r in values) / len(values))
    cagr = equity ** (1 / years) - 1 if years > 0 and equity > 0 else -1.0
    return {"days": len(values), "total_return_pct": round((equity - 1) * 100, 2),
            "cagr_pct": round(cagr * 100, 2), "sharpe": round(sharpe(values), 3),
            "sortino": round(mean(values) / downside * math.sqrt(DAYS_PER_YEAR), 3) if downside else 0.0,
            "max_drawdown_pct": round(max_dd * 100, 2),
            "calmar": round(cagr / abs(max_dd), 3) if max_dd else None,
            "vol_pct": round(pstdev(values) * math.sqrt(DAYS_PER_YEAR) * 100, 2)}


def deflated_sharpe(values: list[float], trial_sharpes: list[float]) -> float | None:
    """Probability that the true Sharpe exceeds the best-of-N-trials noise level."""
    t, n = len(values), len(trial_sharpes)
    if t < 30 or n < 2:
        return None
    m, sd = mean(values), pstdev(values)
    if not sd:
        return None
    sr = m / sd
    skew = sum(((v - m) / sd) ** 3 for v in values) / t
    kurt = sum(((v - m) / sd) ** 4 for v in values) / t
    var_sr = pstdev([s / math.sqrt(DAYS_PER_YEAR) for s in trial_sharpes]) ** 2
    z, emc = NormalDist().inv_cdf, 0.5772156649
    sr0 = math.sqrt(var_sr) * ((1 - emc) * z(1 - 1 / n) + emc * z(1 - 1 / (n * math.e)))
    denom = 1 - skew * sr + (kurt - 1) / 4 * sr * sr
    if denom <= 0:
        return None
    return NormalDist().cdf((sr - sr0) * math.sqrt(t - 1) / math.sqrt(denom))


def block_bootstrap(values: list[float], block: int = 20, runs: int = 1000, seed: int = 7) -> dict:
    """Resample 20-day blocks to see how bad the path could plausibly have been."""
    if len(values) < block * 2:
        return {}
    rng, n, cagrs, dds = random.Random(seed), len(values), [], []
    for _ in range(runs):
        path = []
        while len(path) < n:
            start = rng.randrange(0, n - block)
            path.extend(values[start:start + block])
        s = stats({str(i).zfill(6): r for i, r in enumerate(path[:n])})
        cagrs.append(s["cagr_pct"])
        dds.append(s["max_drawdown_pct"])
    cagrs.sort()
    dds.sort()

    def pick(xs, q):
        return xs[int(q * (len(xs) - 1))]
    return {"cagr_p5": pick(cagrs, .05), "cagr_p50": pick(cagrs, .5),
            "max_dd_p5": pick(dds, .05), "max_dd_p50": pick(dds, .5),
            "prob_loss_pct": round(100 * sum(c < 0 for c in cagrs) / runs, 1)}


def walk_forward_windows(first: str, last: str, train_months: int = 24,
                         test_months: int = 6) -> list[tuple[str, str, str, str]]:
    def add(day: str, months: int) -> str:
        y, m = int(day[:4]), int(day[5:7]) - 1 + months
        return f"{y + m // 12:04d}-{m % 12 + 1:02d}-01"
    result, train_start = [], first[:8] + "01"
    while True:
        train_end = add(train_start, train_months)
        if train_end >= last:
            break
        result.append((train_start, train_end, train_end, min(add(train_end, test_months), last)))
        train_start = add(train_start, test_months)
    return result


def plateau_scores(configs: list[dict], train_sharpe: dict[str, float]) -> dict[str, float]:
    by_coord = {(c["family"], c["tf"], c["index"]): c["key"] for c in configs}
    scores = {}
    for c in configs:
        values = [train_sharpe[c["key"]]]
        for axis in range(len(c["index"])):
            for step in (-1, 1):
                idx = list(c["index"])
                idx[axis] += step
                neighbour = by_coord.get((c["family"], c["tf"], tuple(idx)))
                if neighbour:
                    values.append(train_sharpe[neighbour])
        scores[c["key"]] = mean(values)
    return scores


def walk_forward(configs: list[dict], returns: dict[str, dict[str, float]],
                 windows: list[tuple[str, str, str, str]]) -> dict[str, dict]:
    """Per family: stitched OOS returns plus the config chosen in each window."""
    families = sorted({c["family"] for c in configs})
    out = {f: {"oos": {}, "selections": []} for f in families}
    for train_start, train_end, test_start, test_end in windows:
        train_sharpe = {key: sharpe(list(window(r, train_start, train_end).values())) for key, r in returns.items()}
        scores = plateau_scores(configs, train_sharpe)
        for family in families:
            best = max((c for c in configs if c["family"] == family), key=lambda c: scores[c["key"]])
            test = window(returns[best["key"]], test_start, test_end)
            out[family]["oos"].update(test)
            out[family]["selections"].append({
                "test_window": f"{test_start}..{test_end}", "config": best["key"],
                "train_plateau_sharpe": round(scores[best["key"]], 3), "test": stats(test)})
    return out
