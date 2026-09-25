"""Parametric strategy families, each a small documented grid.

Grids are kept deliberately small: every extra variant is another trial the
deflated Sharpe ratio must pay for. All indicators are O(n) and use only data
available at each bar's close.
"""
from __future__ import annotations

from collections import deque
from itertools import product
from math import sqrt

from forex_ai_analyst.lab.engine import Signals


# ---------- O(n) indicator series ----------

def _closes(bars):
    return [b["close"] for b in bars]


def atr(bars: list[dict], period: int = 14) -> list[float | None]:
    out, value = [None] * len(bars), None
    for i in range(1, len(bars)):
        h, l, pc = bars[i]["high"], bars[i]["low"], bars[i - 1]["close"]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        value = tr if value is None else (value * (period - 1) + tr) / period
        if i >= period:
            out[i] = value
    return out


def ema(values: list[float], period: int) -> list[float | None]:
    out, value, k = [None] * len(values), None, 2 / (period + 1)
    for i, v in enumerate(values):
        value = v if value is None else v * k + value * (1 - k)
        if i >= period - 1:
            out[i] = value
    return out


def rolling_extreme(values: list[float], window: int, highest: bool) -> list[float | None]:
    out, q = [None] * len(values), deque()
    for i, v in enumerate(values):
        while q and ((values[q[-1]] <= v) if highest else (values[q[-1]] >= v)):
            q.pop()
        q.append(i)
        if q[0] <= i - window:
            q.popleft()
        if i >= window - 1:
            out[i] = values[q[0]]
    return out


def rolling_mean_std(values: list[float], window: int) -> tuple[list[float | None], list[float | None]]:
    means, stds, s, s2 = [None] * len(values), [None] * len(values), 0.0, 0.0
    for i, v in enumerate(values):
        s += v
        s2 += v * v
        if i >= window:
            old = values[i - window]
            s -= old
            s2 -= old * old
        if i >= window - 1:
            m = s / window
            means[i], stds[i] = m, sqrt(max(s2 / window - m * m, 0.0))
    return means, stds


def adx(bars: list[dict], period: int = 14) -> list[float | None]:
    out = [None] * len(bars)
    tr_s = plus_s = minus_s = adx_v = None
    dx_seen = 0
    for i in range(1, len(bars)):
        h, l, ph, pl, pc = bars[i]["high"], bars[i]["low"], bars[i - 1]["high"], bars[i - 1]["low"], bars[i - 1]["close"]
        up, down = h - ph, pl - l
        plus = up if up > down and up > 0 else 0.0
        minus = down if down > up and down > 0 else 0.0
        tr = max(h - l, abs(h - pc), abs(l - pc))
        if tr_s is None:
            tr_s, plus_s, minus_s = tr, plus, minus
        else:
            tr_s = tr_s - tr_s / period + tr
            plus_s = plus_s - plus_s / period + plus
            minus_s = minus_s - minus_s / period + minus
        if i < period or not tr_s:
            continue
        pdi, mdi = 100 * plus_s / tr_s, 100 * minus_s / tr_s
        dx = 100 * abs(pdi - mdi) / (pdi + mdi) if pdi + mdi else 0.0
        adx_v = dx if adx_v is None else (adx_v * (period - 1) + dx) / period
        dx_seen += 1
        if dx_seen >= period:
            out[i] = adx_v
    return out


def _prev(series, i):
    return series[i - 1] if i > 0 else None


# ---------- families ----------

def donchian(bars, *, entry_n, exit_n, stop_atr, sides):
    """Classic channel breakout (turtle-style) with a shorter exit channel."""
    highs, lows, closes, n = [b["high"] for b in bars], [b["low"] for b in bars], _closes(bars), len(bars)
    hh, ll = rolling_extreme(highs, entry_n, True), rolling_extreme(lows, entry_n, False)
    xh, xl = rolling_extreme(highs, exit_n, True), rolling_extreme(lows, exit_n, False)
    a = atr(bars)
    le = [bool(_prev(hh, i) and closes[i] > _prev(hh, i)) for i in range(n)]
    se = [sides == "both" and bool(_prev(ll, i) and closes[i] < _prev(ll, i)) for i in range(n)]
    lx = [bool(_prev(xl, i) and closes[i] < _prev(xl, i)) for i in range(n)]
    sx = [bool(_prev(xh, i) and closes[i] > _prev(xh, i)) for i in range(n)]
    return Signals(le, se, lx, sx, [x * stop_atr if x else None for x in a])


def ema_trend(bars, *, fast, slow, trail_atr, sides):
    """EMA crossover entry, opposite-cross exit, ATR chandelier trailing stop."""
    closes, n = _closes(bars), len(bars)
    ef, es, a = ema(closes, fast), ema(closes, slow), atr(bars)
    up = [ef[i] is not None and es[i] is not None and ef[i] > es[i] for i in range(n)]
    down = [ef[i] is not None and es[i] is not None and ef[i] < es[i] for i in range(n)]
    le = [up[i] and not up[i - 1] if i else False for i in range(n)]
    se = [sides == "both" and (down[i] and not down[i - 1] if i else False) for i in range(n)]
    dist = [x * trail_atr if x else None for x in a]
    return Signals(le, se, down, up, dist, trail_distance=dist)


def tsmom(bars, *, lookback_days, tf_hours, sides):
    """Time-series momentum: hold in the direction of the trailing return."""
    closes, n = _closes(bars), len(bars)
    lb = max(1, lookback_days * 24 // tf_hours)
    mom = [closes[i] / closes[i - lb] - 1 if i >= lb else None for i in range(n)]
    a = atr(bars)
    le = [m is not None and m > 0 for m in mom]
    se = [sides == "both" and m is not None and m < 0 for m in mom]
    lx = [m is not None and m <= 0 for m in mom]
    sx = [m is not None and m >= 0 for m in mom]
    dist = [x * 3 if x else None for x in a]
    return Signals(le, se, lx, sx, dist, trail_distance=dist)


def bb_reversion(bars, *, period, k, adx_max, sides):
    """Bollinger mean reversion, only when ADX says the market is not trending."""
    closes, n = _closes(bars), len(bars)
    mean, std = rolling_mean_std(closes, period)
    trend, a = adx(bars), atr(bars)
    calm = [trend[i] is not None and trend[i] < adx_max for i in range(n)]
    le = [calm[i] and mean[i] is not None and closes[i] < mean[i] - k * std[i] for i in range(n)]
    se = [sides == "both" and calm[i] and mean[i] is not None and closes[i] > mean[i] + k * std[i] for i in range(n)]
    lx = [mean[i] is not None and closes[i] >= mean[i] for i in range(n)]
    sx = [mean[i] is not None and closes[i] <= mean[i] for i in range(n)]
    return Signals(le, se, lx, sx, [x * 2 if x else None for x in a], max_bars=2 * period)


FAMILIES = {
    "donchian": (donchian, {"entry_n": [20, 55, 100], "exit_n": [10, 20], "stop_atr": [2.0, 3.0],
                            "sides": ["long", "both"]}),
    "ema_trend": (ema_trend, {"fast": [20, 50], "slow": [100, 200], "trail_atr": [3.0, 4.0],
                              "sides": ["long", "both"]}),
    "tsmom": (tsmom, {"lookback_days": [7, 14, 30], "sides": ["long", "both"]}),
    "bb_reversion": (bb_reversion, {"period": [20], "k": [2.0, 2.5], "adx_max": [20, 25],
                                    "sides": ["long", "both"]}),
}


def configs(timeframes: list[int]) -> list[dict]:
    """Every (family, timeframe, params) trial with its grid coordinates."""
    result = []
    for family, (_, grid) in FAMILIES.items():
        names = list(grid)
        for tf in timeframes:
            for index in product(*(range(len(grid[name])) for name in names)):
                params = {name: grid[name][j] for name, j in zip(names, index)}
                key = f"{family}|{tf}h|" + ",".join(f"{k}={v}" for k, v in params.items())
                result.append({"key": key, "family": family, "tf": tf, "params": params, "index": index})
    return result


def build_signals(config: dict, bars: list[dict]) -> Signals:
    fn = FAMILIES[config["family"]][0]
    params = dict(config["params"])
    if config["family"] == "tsmom":
        params["tf_hours"] = config["tf"]
    return fn(bars, **params)
