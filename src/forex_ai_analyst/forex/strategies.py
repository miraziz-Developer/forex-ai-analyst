"""Forex strategy rules for the study (docs/FOREX_STUDY.md).

The three MT5 bots the owner ran are reproduced as written (same indicators,
thresholds, stops and targets), evaluated on closed bars with entry at the
next bar's open. Two trend-following baselines come from the crypto lab.
"""
from __future__ import annotations

from forex_ai_analyst.lab.engine import Signals
from forex_ai_analyst.lab.strategies import atr, donchian, ema_trend


def _ema(values: list[float], span: int) -> list[float]:
    """pandas ewm(span, adjust=False) as used by the original bots: seeded with the first value."""
    out, k, value = [], 2 / (span + 1), None
    for v in values:
        value = v if value is None else v * k + value * (1 - k)
        out.append(value)
    return out


def ema_pullback(bars: list[dict], point: float) -> Signals:
    """Bot 1 (TrendPro): close and EMA50 on the same side of EMA200 and close within 0.9 ATR of EMA50;
    stop 1.5 ATR, target 3 ATR."""
    closes, n = [b["close"] for b in bars], len(bars)
    e50, e200, a = _ema(closes, 50), _ema(closes, 200), atr(bars)
    le, se, dist, target = [False] * n, [False] * n, [None] * n, [None] * n
    for i in range(200, n):
        if not a[i]:
            continue
        near = abs(closes[i] - e50[i]) <= 0.9 * a[i]
        if closes[i] > e200[i] and e50[i] > e200[i] and near:
            le[i] = True
        elif closes[i] < e200[i] and e50[i] < e200[i] and near:
            se[i] = True
        dist[i], target[i] = 1.5 * a[i], 3.0 * a[i]
    return Signals(le, se, [False] * n, [False] * n, dist, target_distance=target)


def pinbar_snr(bars: list[dict], point: float) -> Signals:
    """Bot 2 (SnR Pro): pin bar touching the 38-bar high/low (within 0.1%) with a wick > 2x body;
    stop 25 points beyond the wick, target 2.5R."""
    n = len(bars)
    le, se, dist, target = [False] * n, [False] * n, [None] * n, [None] * n
    for i in range(38, n):
        window = bars[i - 38:i]
        resistance, support = max(b["high"] for b in window), min(b["low"] for b in window)
        b = bars[i]
        body = abs(b["close"] - b["open"])
        upper, lower = b["high"] - max(b["close"], b["open"]), min(b["close"], b["open"]) - b["low"]
        if abs(b["high"] - resistance) <= resistance * 0.001 and upper > 2 * body:
            d = b["high"] + 25 * point - b["close"]
            if d > 0:
                se[i], dist[i], target[i] = True, d, 2.5 * d
        elif abs(b["low"] - support) <= support * 0.001 and lower > 2 * body:
            d = b["close"] - (b["low"] - 25 * point)
            if d > 0:
                le[i], dist[i], target[i] = True, d, 2.5 * d
    return Signals(le, se, [False] * n, [False] * n, dist, target_distance=target)


def ict_fvg(bars: list[dict], point: float) -> Signals:
    """Bot 3 (ICT Pro): a fair value gap in the last 5 closed bars and price back inside it;
    stop 30 points beyond the 6-bar extreme, target 3R."""
    n = len(bars)
    le, se, dist, target = [False] * n, [False] * n, [None] * n, [None] * n
    for i in range(7, n):
        bull = bear = None
        for j in range(i - 4, i + 1):
            if bars[j]["low"] > bars[j - 2]["high"]:
                bull = (bars[j - 2]["high"], bars[j]["low"])
            elif bars[j]["high"] < bars[j - 2]["low"]:
                bear = (bars[j]["high"], bars[j - 2]["low"])
        price, recent = bars[i]["close"], bars[i - 5:i + 1]
        if bear and bear[0] <= price <= bear[1]:
            d = max(b["high"] for b in recent) + 30 * point - price
            if d > 0:
                se[i], dist[i], target[i] = True, d, 3 * d
        elif bull and bull[0] <= price <= bull[1]:
            d = price - (min(b["low"] for b in recent) - 30 * point)
            if d > 0:
                le[i], dist[i], target[i] = True, d, 3 * d
    return Signals(le, se, [False] * n, [False] * n, dist, target_distance=target)


def donchian_h4(bars: list[dict], point: float) -> Signals:
    """Live crypto rule, both directions: 100-bar breakout, 20-bar exit, 3 ATR stop."""
    return donchian(bars, entry_n=100, exit_n=20, stop_atr=3.0, sides="both")


def donchian_d1(bars: list[dict], point: float) -> Signals:
    """Turtle system 2: 55-day breakout, 20-day exit, 2 ATR stop, both directions."""
    return donchian(bars, entry_n=55, exit_n=20, stop_atr=2.0, sides="both")


def ema_cross_d1(bars: list[dict], point: float) -> Signals:
    """EMA50/EMA200 cross with a 3 ATR chandelier trail, both directions."""
    return ema_trend(bars, fast=50, slow=200, trail_atr=3.0, sides="both")


# name -> (rule, timeframe)
STRATEGIES = {
    "ema_pullback_h1": (ema_pullback, "H1"),
    "pinbar_snr_h1": (pinbar_snr, "H1"),
    "ict_fvg_h1": (ict_fvg, "H1"),
    "donchian_h4": (donchian_h4, "H4"),
    "donchian_d1": (donchian_d1, "D1"),
    "ema_cross_d1": (ema_cross_d1, "D1"),
}
