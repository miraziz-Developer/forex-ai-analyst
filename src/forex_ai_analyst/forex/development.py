"""Discovery -> validation -> holdout development of the range and trend modules
(docs/DEVELOPMENT_STUDY.md).

    python3 -m forex_ai_analyst.forex.development

Parameters, including SMC/ICT filters, may be optimised on DISCOVERY only. The
single best configuration per module must then pass VALIDATION, and only then
is HOLDOUT read, once.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from itertools import product
from pathlib import Path
from statistics import median

from forex_ai_analyst.forex.data import cost_fraction
from forex_ai_analyst.forex.regime_system import features
from forex_ai_analyst.forex.regime_system_study import FX_ONLY
from forex_ai_analyst.forex.study import bars_for, bootstrap_p_mean_positive, summarize
from forex_ai_analyst.lab import engine
from forex_ai_analyst.lab.candidates import confirmed_pivots
from forex_ai_analyst.lab.engine import Signals

PERIODS = {"discovery": ("2004-01-01", "2015-01-01"), "validation": ("2015-01-01", "2021-01-01"),
           "holdout": ("2021-01-01", "2100-01-01")}
RANGE_GRID = {"adx_max": [20, 25], "rsi_th": [10, 5], "target": ["mid", "far_band", "1.5R"],
              "stop_atr": [1.0, 1.5, 2.0], "smc": ["none", "sweep"]}
TREND_GRID = {"adx_min": [20, 25], "pullback_atr": [0.5, 1.0], "stop_atr": [1.5, 2.0, 3.0],
              "exit": ["trail3", "channel20", "2R"], "smc": ["none", "fvg"]}
MIN_DISCOVERY_TRADES = 60
VALIDATION_GATE = {"min_trades": 30, "min_bootstrap_p": 0.90, "min_profit_factor": 1.15}
HOLDOUT_GATE = {"min_trades": 20, "min_profit_factor": 1.10}


def grid(spec: dict) -> list[dict]:
    names = list(spec)
    return [dict(zip(names, values)) for values in product(*(spec[n] for n in names))]


def _sweeps(bars):
    """(swept_low[i], swept_high[i]): bar i or i-1 traded through the last confirmed swing low/high and closed back."""
    highs, lows, _ = confirmed_pivots(bars)
    n = len(bars)
    swept_low, swept_high = [False] * n, [False] * n
    for i in range(1, n):
        for j in (i - 1, i):
            if lows[j] is not None and bars[j]["low"] < lows[j] < bars[j]["close"]:
                swept_low[i] = True
            if highs[j] is not None and bars[j]["high"] > highs[j] > bars[j]["close"]:
                swept_high[i] = True
    return swept_low, swept_high


def _fvg_touch(bars):
    """(bull[i], bear[i]): a fair value gap formed in the last 5 bars and bar i traded back into it."""
    n = len(bars)
    bull, bear = [False] * n, [False] * n
    for i in range(7, n):
        for j in range(i - 5, i):
            if bars[j]["low"] > bars[j - 2]["high"] and bars[i]["low"] <= bars[j]["low"]:
                bull[i] = True
            if bars[j]["high"] < bars[j - 2]["low"] and bars[i]["high"] >= bars[j]["high"]:
                bear[i] = True
    return bull, bear


def range_signals(bars, f, cfg, sweeps) -> Signals:
    n = len(bars)
    le, se, dist, target = [False] * n, [False] * n, [None] * n, [None] * n
    mean, sd = f["bb"]
    swept_low, swept_high = sweeps
    for i in range(201, n):
        a = f["atr"][i]
        adx_v, er, vr = f["adx"][i], f["er"][i], f["vol_ratio"][i]
        if None in (adx_v, er, vr, mean[i - 1], mean[i], f["rsi2"][i - 1]) or not a:
            continue
        if not (adx_v < cfg["adx_max"] and er < 0.20 and vr < 1.0):
            continue
        c = f["close"][i]
        lower, upper = mean[i] - 2 * sd[i], mean[i] + 2 * sd[i]
        long_setup = f["close"][i - 1] < mean[i - 1] - 2 * sd[i - 1] and f["rsi2"][i - 1] < cfg["rsi_th"] and lower < c < mean[i]
        short_setup = f["close"][i - 1] > mean[i - 1] + 2 * sd[i - 1] and f["rsi2"][i - 1] > 100 - cfg["rsi_th"] and mean[i] < c < upper
        if cfg["smc"] == "sweep":
            long_setup, short_setup = long_setup and swept_low[i], short_setup and swept_high[i]
        if long_setup:
            stop = c - (min(bars[i - 1]["low"], bars[i]["low"]) - cfg["stop_atr"] * a)
            tgt = {"mid": mean[i] - c, "far_band": upper - c, "1.5R": 1.5 * stop}[cfg["target"]]
            le[i], dist[i], target[i] = True, stop, tgt
        elif short_setup:
            stop = (max(bars[i - 1]["high"], bars[i]["high"]) + cfg["stop_atr"] * a) - c
            tgt = {"mid": c - mean[i], "far_band": c - lower, "1.5R": 1.5 * stop}[cfg["target"]]
            se[i], dist[i], target[i] = True, stop, tgt
    return Signals(le, se, [False] * n, [False] * n, dist, target_distance=target, max_bars=15)


def trend_signals(bars, f, cfg, fvg) -> Signals:
    n = len(bars)
    le, se, lx, sx, dist = [False] * n, [False] * n, [False] * n, [False] * n, [None] * n
    target = [None] * n if cfg["exit"] == "2R" else None
    bull, bear = fvg
    for i in range(201, n):
        a = f["atr"][i]
        adx_v, er = f["adx"][i], f["er"][i]
        if None in (adx_v, er, f["slope100"][i]) or not a or not (adx_v > cfg["adx_min"] and er > 0.30):
            continue
        c = f["close"][i]
        up = f["ema50"][i] > f["ema200"][i] and f["slope100"][i] > 0
        down = f["ema50"][i] < f["ema200"][i] and f["slope100"][i] < 0
        reach = cfg["pullback_atr"] * a
        long_setup = up and bars[i]["low"] <= f["ema20"][i] + reach and c > bars[i - 1]["high"]
        short_setup = down and bars[i]["high"] >= f["ema20"][i] - reach and c < bars[i - 1]["low"]
        if cfg["smc"] == "fvg":
            long_setup, short_setup = long_setup and bull[i], short_setup and bear[i]
        if long_setup or short_setup:
            le[i], se[i] = long_setup, short_setup
            dist[i] = cfg["stop_atr"] * a
            if target is not None:
                target[i] = 2 * dist[i]
    if cfg["exit"] == "channel20":
        for i in range(1, n):
            lx[i] = f["ll20"][i - 1] is not None and f["close"][i] < f["ll20"][i - 1]
            sx[i] = f["hh20"][i - 1] is not None and f["close"][i] > f["hh20"][i - 1]
    trail = [3 * x if x else None for x in f["atr"]] if cfg["exit"] == "trail3" else None
    return Signals(le, se, lx, sx, dist, trail_distance=trail, target_distance=target)


def _period(ms: int) -> str:
    day = datetime.fromtimestamp(ms / 1000, timezone.utc).date().isoformat()
    return next(name for name, (start, end) in PERIODS.items() if start <= day < end)


def evaluate_all() -> dict:
    prepared = []
    for market in FX_ONLY:
        bars = bars_for(market, "D1")
        f = features(bars)
        side = cost_fraction(market, median(b["close"] for b in bars)) / 2 * 100
        prepared.append((market.name, bars, f, _sweeps(bars), _fvg_touch(bars),
                         engine.Costs(fee_pct_per_side=side, slippage_pct_per_side=0.0, charge_funding=False)))
    results = {}
    for module, spec, build in (("range", RANGE_GRID, lambda b, f, c, s, v: range_signals(b, f, c, s)),
                                ("trend", TREND_GRID, lambda b, f, c, s, v: trend_signals(b, f, c, v))):
        configs = []
        for cfg in grid(spec):
            by_period = {p: [] for p in PERIODS}
            for name, bars, f, sweeps, fvg, costs in prepared:
                for t in engine.run(bars, build(bars, f, cfg, sweeps, fvg), costs=costs).trades:
                    by_period[_period(t["entry_time"])].append({**t, "market": name})
            configs.append((cfg, by_period))
        results[module] = configs
    return results


def _score(trades: list[dict]) -> float:
    """Discovery ranking: t-statistic of R (mean / standard error)."""
    r = [t["r"] for t in trades]
    if len(r) < MIN_DISCOVERY_TRADES:
        return float("-inf")
    mean = sum(r) / len(r)
    sd = (sum((x - mean) ** 2 for x in r) / len(r)) ** 0.5
    return mean / sd * len(r) ** 0.5 if sd else float("-inf")


def main() -> None:
    results = evaluate_all()
    report = {}
    for module, configs in results.items():
        ranked = sorted(configs, key=lambda c: _score(c[1]["discovery"]), reverse=True)
        best_cfg, best = ranked[0]
        disc, val = summarize(best["discovery"]), summarize(best["validation"])
        p_val = bootstrap_p_mean_positive(best["validation"]) if best["validation"] else 0.0
        val_reasons = []
        if val.get("trades", 0) < VALIDATION_GATE["min_trades"]:
            val_reasons.append(f"{val.get('trades', 0)} trades < {VALIDATION_GATE['min_trades']}")
        if p_val < VALIDATION_GATE["min_bootstrap_p"]:
            val_reasons.append(f"P(mean R > 0) {p_val:.2f} < {VALIDATION_GATE['min_bootstrap_p']}")
        if (val.get("profit_factor") or 0) < VALIDATION_GATE["min_profit_factor"]:
            val_reasons.append(f"profit factor {val.get('profit_factor')} < {VALIDATION_GATE['min_profit_factor']}")
        entry = {"trials": len(configs), "best_config": best_cfg, "discovery": disc, "validation": val,
                 "validation_p": round(p_val, 3), "validation_passes": not val_reasons, "validation_reasons": val_reasons,
                 "top5_discovery": [{"config": c, "discovery": summarize(bp["discovery"])} for c, bp in ranked[:5]]}
        if not val_reasons:     # holdout is read only after validation passes
            hold = summarize(best["holdout"])
            hold_reasons = []
            if hold.get("trades", 0) < HOLDOUT_GATE["min_trades"]:
                hold_reasons.append(f"{hold.get('trades', 0)} trades < {HOLDOUT_GATE['min_trades']}")
            if (hold.get("profit_factor") or 0) < HOLDOUT_GATE["min_profit_factor"] or hold.get("mean_r", 0) <= 0:
                hold_reasons.append(f"holdout mean R {hold.get('mean_r')} / PF {hold.get('profit_factor')} below gate")
            entry.update(holdout=hold, holdout_passes=not hold_reasons, holdout_reasons=hold_reasons)
        report[module] = entry
    out = Path("research_output")
    out.mkdir(exist_ok=True)
    (out / "development_study.json").write_text(json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(),
                                                            "periods": PERIODS, "report": report}, indent=2) + "\n")
    for module, e in report.items():
        print(f"== {module}: {e['trials']} trials; best on discovery {e['best_config']}")
        print(f"   discovery  {e['discovery']}")
        print(f"   validation {e['validation']} P {e['validation_p']} -> {'PASS' if e['validation_passes'] else 'FAIL: ' + '; '.join(e['validation_reasons'])}")
        if "holdout" in e:
            print(f"   HOLDOUT    {e['holdout']} -> {'PASS' if e['holdout_passes'] else 'FAIL: ' + '; '.join(e['holdout_reasons'])}")


if __name__ == "__main__":
    main()
