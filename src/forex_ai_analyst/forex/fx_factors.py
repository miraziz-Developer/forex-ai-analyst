"""Carry + trend + value currency portfolio, monthly (docs/FX_FACTOR_STUDY.md).

    python3 -m forex_ai_analyst.forex.fx_factors

Eight currencies (USD, EUR, GBP, JPY, AUD, NZD, CAD, CHF). A position in currency
c is held against USD; its monthly excess return is the spot change plus the
interest differential, minus a retail swap markup. Every decision made at the
end of month t uses only spot closes through t and interest rates through t-1.
"""
from __future__ import annotations

import csv
import io
import json
import math
import random
from datetime import datetime, timezone
from pathlib import Path

import requests

from forex_ai_analyst.forex.data import CACHE_DIR, MARKETS, load_yahoo

CURRENCIES = ("EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF")   # vs USD
PAIR = {"EUR": ("EURUSD", False), "GBP": ("GBPUSD", False), "AUD": ("AUDUSD", False), "NZD": ("NZDUSD", False),
        "JPY": ("USDJPY", True), "CAD": ("USDCAD", True), "CHF": ("USDCHF", True)}   # True: invert to get c/USD
OECD_AREA = {"USD": "USA", "EUR": "EA20", "GBP": "GBR", "JPY": "JPN", "AUD": "AUS", "NZD": "NZL", "CAD": "CAN",
             "CHF": "CHE"}
OECD_URL = ("https://sdmx.oecd.org/public/rest/data/OECD.SDD.STES,DSD_STES@DF_FINMARK,/"
            "USA+GBR+JPN+AUS+NZL+CAN+CHE+EA20.M.IR3TIB.......")
SWAP_MARKUP = 0.010     # per year against every open position, long or short
TURNOVER_COST = 0.0002  # per unit of weight traded (spread + commission)
TARGET_VOL, VOL_WINDOW, VOL_MIN = 0.10, 24, 12
GATE = {"min_sharpe": 0.4, "min_bootstrap_p": 0.90, "max_drawdown": -0.35}


def load_rates() -> dict[str, dict[str, float]]:
    """Monthly 3-month interbank rates in percent per year, by currency and 'YYYY-MM'."""
    CACHE_DIR.mkdir(exist_ok=True)
    path = CACHE_DIR / f"oecd-ir3tib-{datetime.now(timezone.utc).date().isoformat()}.csv"
    if not path.exists():
        response = requests.get(OECD_URL, params={"startPeriod": "2003-01", "format": "csvfile",
                                                  "dimensionAtObservation": "AllDimensions"},
                                headers={"User-Agent": "Mozilla/5.0"}, timeout=90)
        response.raise_for_status()
        path.write_text(response.text)
    area_to_ccy = {v: k for k, v in OECD_AREA.items()}
    rates: dict[str, dict[str, float]] = {}
    for row in csv.DictReader(io.StringIO(path.read_text())):
        if row["OBS_VALUE"] and row["REF_AREA"] in area_to_ccy:
            rates.setdefault(area_to_ccy[row["REF_AREA"]], {})[row["TIME_PERIOD"]] = float(row["OBS_VALUE"])
    return rates


def month_end_spots() -> dict[str, dict[str, float]]:
    """Last daily close of each month for c/USD."""
    spots: dict[str, dict[str, float]] = {}
    for ccy, (pair, invert) in PAIR.items():
        for bar in load_yahoo(MARKETS[pair], "1d"):
            month = datetime.fromtimestamp(bar["datetime"] / 1000, timezone.utc).strftime("%Y-%m")
            spots.setdefault(ccy, {})[month] = 1 / bar["close"] if invert else bar["close"]
    return spots


def _rate(rates: dict, ccy: str, month: str) -> float | None:
    """Latest rate published for a month <= `month` (forward-fills a lagging series)."""
    series = rates.get(ccy, {})
    known = [m for m in series if m <= month]
    return series[max(known)] if known else None


def build_panel(spots: dict, rates: dict) -> tuple[list[str], dict]:
    months = sorted(set.intersection(*(set(s) for s in spots.values())))
    excess = {}   # month t -> {ccy: excess return earned over month t (from t-1 close to t close)}
    for prev, month in zip(months, months[1:]):
        rate_month = prev   # the differential earned during `month` is the one known at its start
        r_usd = _rate(rates, "USD", rate_month)
        row = {}
        for c in CURRENCIES:
            rc = _rate(rates, c, rate_month)
            if r_usd is None or rc is None:
                continue
            row[c] = spots[c][month] / spots[c][prev] - 1 + (rc - r_usd) / 100 / 12
        excess[month] = row
    return months, excess


def _rank_weights(scores: dict[str, float], k: int = 3) -> dict[str, float]:
    """Long the k highest, short the k lowest of the 8 currencies (USD scores 0); dollar neutral."""
    ranked = sorted(scores, key=scores.get)
    weights = {c: 0.0 for c in scores}
    for c in ranked[-k:]:
        weights[c] += 1 / k
    for c in ranked[:k]:
        weights[c] -= 1 / k
    return weights


def signals(month_index: int, months: list[str], spots: dict, rates: dict, excess: dict) -> dict[str, dict]:
    """Weights per strategy decided at the end of months[month_index] (USD weight implicit)."""
    t = months[month_index]
    rate_month = months[month_index - 1] if month_index else t      # rates through t-1 only
    out = {}
    r_usd = _rate(rates, "USD", rate_month)
    carry_scores = {"USD": 0.0}
    for c in CURRENCIES:
        rc = _rate(rates, c, rate_month)
        if rc is not None and r_usd is not None:
            carry_scores[c] = rc - r_usd
    if len(carry_scores) == 8:
        out["carry"] = _rank_weights(carry_scores)
    if month_index >= 12:
        window = months[month_index - 11:month_index + 1]
        trend = {}
        for c in CURRENCIES:
            values = [excess.get(m, {}).get(c) for m in window]
            if None not in values:
                trend[c] = (1 / 7) * (1 if math.prod(1 + v for v in values) > 1 else -1)
        if len(trend) == 7:
            out["trend"] = trend
    if month_index >= 60:
        past = months[month_index - 60]
        value_scores = {"USD": 0.0}
        for c in CURRENCIES:
            value_scores[c] = -(spots[c][t] / spots[c][past] - 1)      # cheap after a 5-year fall
        out["value"] = _rank_weights(value_scores)
    return out


def backtest(months, spots, rates, excess) -> dict[str, dict[str, float]]:
    """Monthly net returns per strategy, raw (unscaled) gross exposure."""
    returns: dict[str, dict[str, float]] = {"carry": {}, "trend": {}, "value": {}}
    held: dict[str, dict] = {}
    for i in range(len(months) - 1):
        decided = signals(i, months, spots, rates, excess)
        nxt = months[i + 1]
        for name, weights in decided.items():
            before = held.get(name, {})
            turnover = sum(abs(weights.get(c, 0) - before.get(c, 0)) for c in set(weights) | set(before))
            gross = sum(abs(w) for c, w in weights.items() if c != "USD")
            ret = sum(w * excess[nxt].get(c, 0.0) for c, w in weights.items() if c != "USD")
            returns[name][nxt] = ret - turnover * TURNOVER_COST - gross * SWAP_MARKUP / 12
            held[name] = weights
    return returns


def vol_scaled(series: dict[str, float]) -> dict[str, float]:
    """Scale each month to TARGET_VOL using only the trailing VOL_WINDOW months."""
    months, out = sorted(series), {}
    for i, m in enumerate(months):
        past = [series[x] for x in months[max(0, i - VOL_WINDOW):i]]
        if len(past) < VOL_MIN:
            continue
        mean = sum(past) / len(past)
        vol = math.sqrt(sum((x - mean) ** 2 for x in past) / len(past)) * math.sqrt(12)
        if vol > 0:
            out[m] = series[m] * min(TARGET_VOL / vol, 3.0)
    return out


def stats(series: dict[str, float]) -> dict:
    values = [series[m] for m in sorted(series)]
    if len(values) < 24:
        return {"months": len(values)}
    mean = sum(values) / len(values)
    sd = math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))
    equity = peak = 1.0
    max_dd = 0.0
    for v in values:
        equity *= 1 + v
        peak = max(peak, equity)
        max_dd = min(max_dd, equity / peak - 1)
    years = len(values) / 12
    half = len(values) // 2
    return {"months": len(values), "start": min(series), "cagr": round(equity ** (1 / years) - 1, 4),
            "vol": round(sd * math.sqrt(12), 4), "sharpe": round(mean / sd * math.sqrt(12), 3) if sd else 0.0,
            "max_drawdown": round(max_dd, 4), "positive_months": round(sum(v > 0 for v in values) / len(values), 3),
            "first_half_mean": round(sum(values[:half]) / half * 12, 4),
            "second_half_mean": round(sum(values[half:]) / (len(values) - half) * 12, 4)}


def block_bootstrap_p(series: dict[str, float], block: int = 12, runs: int = 5000, seed: int = 11) -> float:
    values = [series[m] for m in sorted(series)]
    n = len(values)
    if n < 2 * block:
        return 0.0
    rng, positive = random.Random(seed), 0
    for _ in range(runs):
        sample = []
        while len(sample) < n:
            start = rng.randrange(0, n - block + 1)
            sample += values[start:start + block]
        positive += sum(sample[:n]) > 0
    return positive / runs


def verdict(series: dict[str, float]) -> dict:
    s, p = stats(series), block_bootstrap_p(series)
    reasons = []
    if s.get("sharpe", 0) < GATE["min_sharpe"]:
        reasons.append(f"Sharpe {s.get('sharpe')} < {GATE['min_sharpe']}")
    if p < GATE["min_bootstrap_p"]:
        reasons.append(f"P(mean > 0) {p:.2f} < {GATE['min_bootstrap_p']}")
    if s.get("first_half_mean", 0) <= 0 or s.get("second_half_mean", 0) <= 0:
        reasons.append("not positive in both halves")
    if s.get("max_drawdown", -1) < GATE["max_drawdown"]:
        reasons.append(f"max drawdown {s.get('max_drawdown')} < {GATE['max_drawdown']}")
    return {**s, "p_mean_positive": round(p, 3), "passes": not reasons, "reasons": reasons}


def main() -> None:
    spots, rates = month_end_spots(), load_rates()
    months, excess = build_panel(spots, rates)
    raw = backtest(months, spots, rates, excess)
    scaled = {name: vol_scaled(series) for name, series in raw.items()}
    common = sorted(set.intersection(*(set(s) for s in scaled.values())))
    combined = {m: sum(scaled[n][m] for n in scaled) / len(scaled) for m in common}
    report = {name: verdict(series) for name, series in scaled.items()}
    report["combined"] = verdict(vol_scaled(combined))
    out = Path("research_output")
    out.mkdir(exist_ok=True)
    (out / "fx_factor_study.json").write_text(json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(),
                                                          "gate": GATE, "report": report}, indent=2) + "\n")
    for name, r in report.items():
        print(f"{name:9s} from {r.get('start')} months {r.get('months')} CAGR {r.get('cagr')} vol {r.get('vol')} "
              f"Sharpe {r.get('sharpe')} maxDD {r.get('max_drawdown')} halves {r.get('first_half_mean')}/"
              f"{r.get('second_half_mean')} P {r['p_mean_positive']} -> "
              f"{'PASS' if r['passes'] else 'FAIL: ' + '; '.join(r['reasons'])}")


if __name__ == "__main__":
    main()
