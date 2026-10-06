"""Regime-switching multi-strategy system for FX (docs/REGIME_SYSTEM_STUDY.md).

A regime classifier built from four independent measurements decides which of
three modules may trade; each module then needs its own confluence of
conditions ("sniper" entries). Every value at bar i uses bars <= i only and
entries fill at the next bar's open. All parameters are textbook defaults
fixed before testing.
"""
from __future__ import annotations

from forex_ai_analyst.forex.strategies import _ema
from forex_ai_analyst.lab.candidates import rsi
from forex_ai_analyst.lab.engine import Signals
from forex_ai_analyst.lab.strategies import adx, atr, rolling_extreme, rolling_mean_std

TREND, RANGE, SQUEEZE, NONE = "trend", "range", "squeeze", "none"


def efficiency_ratio(closes: list[float], n: int = 20) -> list[float | None]:
    """Kaufman efficiency ratio: |net move| / sum of absolute moves over n bars."""
    out = [None] * len(closes)
    for i in range(n, len(closes)):
        path = sum(abs(closes[j] - closes[j - 1]) for j in range(i - n + 1, i + 1))
        out[i] = abs(closes[i] - closes[i - n]) / path if path else 0.0
    return out


def features(bars: list[dict]) -> dict:
    closes = [b["close"] for b in bars]
    a14 = atr(bars, 14)
    a100 = atr(bars, 100)
    e100 = _ema(closes, 100)
    return {"close": closes, "adx": adx(bars), "er": efficiency_ratio(closes),
            "vol_ratio": [a14[i] / a100[i] if a14[i] and a100[i] else None for i in range(len(bars))],
            "atr": a14, "ema20": _ema(closes, 20), "ema50": _ema(closes, 50), "ema200": _ema(closes, 200),
            "slope100": [e100[i] - e100[i - 10] if i >= 10 else None for i in range(len(bars))],
            "bb": rolling_mean_std(closes, 20), "rsi2": rsi(closes, 2),
            "hh20": rolling_extreme([b["high"] for b in bars], 20, True),
            "ll20": rolling_extreme([b["low"] for b in bars], 20, False)}


def regimes(f: dict) -> list[str]:
    out = []
    for i in range(len(f["close"])):
        adx_v, er, vr = f["adx"][i], f["er"][i], f["vol_ratio"][i]
        if None in (adx_v, er, vr) or i < 200:
            out.append(NONE)
        elif adx_v > 25 and er > 0.30:
            out.append(TREND)
        elif vr < 0.70:
            out.append(SQUEEZE)
        elif adx_v < 20 and er < 0.20 and vr < 1.0:
            out.append(RANGE)
        else:
            out.append(NONE)
    return out


def _empty(n):
    return [False] * n, [False] * n, [False] * n, [False] * n, [None] * n


def trend_module(bars: list[dict], f: dict, reg: list[str]) -> Signals:
    """Strong trend + aligned EMA50/200 and EMA100 slope + pullback to within 1 ATR of EMA20 +
    a close beyond the previous bar's extreme in the trend direction. Stop 2 ATR, 3 ATR trail,
    exit on a close through the opposite 20-bar channel."""
    n = len(bars)
    le, se, lx, sx, dist = _empty(n)
    for i in range(201, n):
        if reg[i] != TREND or not f["atr"][i]:
            continue
        a, c = f["atr"][i], f["close"][i]
        up = f["ema50"][i] > f["ema200"][i] and f["slope100"][i] > 0
        down = f["ema50"][i] < f["ema200"][i] and f["slope100"][i] < 0
        near = min(abs(bars[i]["low"] - f["ema20"][i]), abs(bars[i]["high"] - f["ema20"][i])) <= a
        if up and near and c > bars[i - 1]["high"]:
            le[i] = True
        elif down and near and c < bars[i - 1]["low"]:
            se[i] = True
        dist[i] = 2 * a
    for i in range(1, n):
        lx[i] = f["ll20"][i - 1] is not None and f["close"][i] < f["ll20"][i - 1]
        sx[i] = f["hh20"][i - 1] is not None and f["close"][i] > f["hh20"][i - 1]
    trail = [3 * a if a else None for a in f["atr"]]
    return Signals(le, se, lx, sx, dist, trail_distance=trail)


def range_module(bars: list[dict], f: dict, reg: list[str]) -> Signals:
    """Quiet range + previous bar closed outside the 20/2 Bollinger band with RSI(2) extreme +
    this bar closes back inside. Target the middle band, stop 1.5 ATR beyond the extreme, 10 bars max."""
    n = len(bars)
    le, se, lx, sx, dist = _empty(n)
    target = [None] * n
    mean, sd = f["bb"]
    for i in range(201, n):
        if reg[i] != RANGE or mean[i - 1] is None or mean[i] is None or not f["atr"][i] or f["rsi2"][i - 1] is None:
            continue
        a, c, prev = f["atr"][i], f["close"][i], f["close"][i - 1]
        lower_prev, upper_prev = mean[i - 1] - 2 * sd[i - 1], mean[i - 1] + 2 * sd[i - 1]
        lower, upper = mean[i] - 2 * sd[i], mean[i] + 2 * sd[i]
        if prev < lower_prev and f["rsi2"][i - 1] < 10 and lower < c < mean[i]:
            stop = c - (min(bars[i - 1]["low"], bars[i]["low"]) - 1.5 * a)
            le[i], dist[i], target[i] = True, stop, mean[i] - c
        elif prev > upper_prev and f["rsi2"][i - 1] > 90 and mean[i] < c < upper:
            stop = (max(bars[i - 1]["high"], bars[i]["high"]) + 1.5 * a) - c
            se[i], dist[i], target[i] = True, stop, c - mean[i]
    return Signals(le, se, lx, sx, dist, target_distance=target, max_bars=10)


def breakout_module(bars: list[dict], f: dict, reg: list[str]) -> Signals:
    """Volatility compressed on at least 5 of the last 10 bars + a close beyond the prior 20-bar
    channel on a bar whose range exceeds 1.2 ATR. Stop 2 ATR, 3 ATR trail, 20-bar channel exit."""
    n = len(bars)
    le, se, lx, sx, dist = _empty(n)
    for i in range(201, n):
        if not f["atr"][i] or f["hh20"][i - 1] is None:
            continue
        squeezed = sum(1 for j in range(i - 10, i) if reg[j] == SQUEEZE) >= 5
        wide = bars[i]["high"] - bars[i]["low"] > 1.2 * f["atr"][i]
        if squeezed and wide and f["close"][i] > f["hh20"][i - 1]:
            le[i] = True
        elif squeezed and wide and f["close"][i] < f["ll20"][i - 1]:
            se[i] = True
        dist[i] = 2 * f["atr"][i]
    for i in range(1, n):
        lx[i] = f["ll20"][i - 1] is not None and f["close"][i] < f["ll20"][i - 1]
        sx[i] = f["hh20"][i - 1] is not None and f["close"][i] > f["hh20"][i - 1]
    trail = [3 * a if a else None for a in f["atr"]]
    return Signals(le, se, lx, sx, dist, trail_distance=trail)


MODULES = {"trend": trend_module, "range": range_module, "breakout": breakout_module}


def module_signals(bars: list[dict]) -> dict[str, Signals]:
    f = features(bars)
    reg = regimes(f)
    return {name: fn(bars, f, reg) for name, fn in MODULES.items()}
