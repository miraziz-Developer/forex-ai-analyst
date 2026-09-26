"""Candidate swing strategies for the 2026-09 combination study (docs/COMBINATION_STUDY.md).

Each idea discussed with the owner is reduced to one objective, non-repainting
rule with textbook parameters and no grid: pullback (RSI-2 in an uptrend),
structure breakout (zigzag / SMC break of structure), liquidity sweep
(ICT / SMC / support-resistance false break) and cross-sectional momentum.
Swing points are *confirmed* pivots: a pivot high at bar j is only known at
bar j + PIVOT_K, so no rule sees a swing before it could have been drawn live.
"""
from __future__ import annotations

from datetime import datetime, timezone

from forex_ai_analyst.lab.engine import Signals
from forex_ai_analyst.lab.strategies import atr, ema

PIVOT_K = 3


def rsi(closes: list[float], period: int) -> list[float | None]:
    """Wilder RSI."""
    out, gain, loss = [None] * len(closes), None, None
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        up, down = max(change, 0.0), max(-change, 0.0)
        if gain is None:
            gain, loss = up, down
        else:
            gain = (gain * (period - 1) + up) / period
            loss = (loss * (period - 1) + down) / period
        if i >= period:
            out[i] = 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)
    return out


def confirmed_pivots(bars: list[dict], k: int = PIVOT_K) -> tuple[list, list, list]:
    """Per bar i: (last confirmed pivot high, last confirmed pivot low, the pivot low before it),
    using only pivots whose k right-hand bars have closed by bar i."""
    n = len(bars)
    highs, lows = [b["high"] for b in bars], [b["low"] for b in bars]
    last_high, last_low, prior_low = [None] * n, [None] * n, [None] * n
    ph = pl = ppl = None
    for i in range(n):
        j = i - k                       # the pivot candidate that becomes known at bar i
        if j >= k:
            if highs[j] == max(highs[j - k:j + k + 1]):
                ph = highs[j]
            if lows[j] == min(lows[j - k:j + k + 1]):
                ppl, pl = pl, lows[j]
        last_high[i], last_low[i], prior_low[i] = ph, pl, ppl
    return last_high, last_low, prior_low


def pullback_rsi2(bars: list[dict]) -> Signals:
    """Buy short-term weakness inside an uptrend (Connors-style RSI-2)."""
    closes, n = [b["close"] for b in bars], len(bars)
    fast, slow, r, a = ema(closes, 50), ema(closes, 200), rsi(closes, 2), atr(bars)
    uptrend = [fast[i] is not None and slow[i] is not None and fast[i] > slow[i] and closes[i] > slow[i]
               for i in range(n)]
    le = [uptrend[i] and r[i] is not None and r[i] < 10 for i in range(n)]
    lx = [r[i] is not None and r[i] > 70 for i in range(n)]
    return Signals(le, [False] * n, lx, [False] * n, [x * 2.5 if x else None for x in a], max_bars=30)


def structure_breakout(bars: list[dict]) -> Signals:
    """Zigzag / SMC break of structure: close above the last confirmed swing high after a
    higher low; stop at that swing low (at least 1 ATR); exit when a close breaks the last
    confirmed swing low (change of character)."""
    closes, n = [b["close"] for b in bars], len(bars)
    last_high, last_low, prior_low = confirmed_pivots(bars)
    a = atr(bars)
    le, lx, dist = [False] * n, [False] * n, [None] * n
    for i in range(1, n):
        h, low, prior = last_high[i], last_low[i], prior_low[i]
        if h is not None and low is not None and prior is not None and a[i]:
            le[i] = closes[i] > h >= closes[i - 1] and low > prior
            dist[i] = max(closes[i] - low, a[i])
        lx[i] = low is not None and closes[i] < low
    return Signals(le, [False] * n, lx, [False] * n, dist)


def liquidity_sweep(bars: list[dict]) -> Signals:
    """ICT / SMC sweep: in an uptrend, a bar trades below the last confirmed swing low but
    closes back above it with a bullish body. Stop under the sweep, target 2R, 30-bar limit."""
    closes, n = [b["close"] for b in bars], len(bars)
    slow, a = ema(closes, 200), atr(bars)
    _, last_low, _ = confirmed_pivots(bars)
    le, dist, target = [False] * n, [None] * n, [None] * n
    for i in range(n):
        level, b = last_low[i], bars[i]
        if level is None or slow[i] is None or not a[i]:
            continue
        if closes[i] > slow[i] and b["low"] < level < b["close"] and b["close"] > b["open"]:
            le[i] = True
            dist[i] = b["close"] - b["low"] + 0.25 * a[i]
            target[i] = 2 * dist[i]
    return Signals(le, [False] * n, [False] * n, [False] * n, dist, target_distance=target, max_bars=30)


CANDIDATES = {"pullback_rsi2": pullback_rsi2, "structure_breakout": structure_breakout,
              "liquidity_sweep": liquidity_sweep}


def _day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).date().isoformat()


def xs_momentum_returns(daily: dict[str, list[dict]], funding: dict[str, list[tuple[int, float]]], *,
                        lookback: int = 30, hold: int = 3, rebalance_days: int = 7,
                        cost_per_side: float = 0.0007) -> dict[str, float]:
    """Daily returns of a long-only rotation: every `rebalance_days`, hold the `hold` markets with
    the best `lookback`-day return among those whose return is positive (else cash), equal weight.
    A decision uses closes through day t-1 and earns from day t; turnover pays cost_per_side and
    held positions pay historical funding."""
    closes = {p: {_day(b["datetime"]): b["close"] for b in bars} for p, bars in daily.items()}
    fund: dict[str, dict[str, float]] = {}
    for p, events in funding.items():
        for stamp, rate in events:
            fund.setdefault(p, {})[_day(stamp)] = fund.get(p, {}).get(_day(stamp), 0.0) + rate
    days = sorted(set.intersection(*(set(c) for c in closes.values())))
    weights: dict[str, float] = {}
    out: dict[str, float] = {}
    for t in range(1, len(days)):
        today, prev = days[t], days[t - 1]
        ret = 0.0
        if (t - 1) % rebalance_days == 0 and t - 1 >= lookback:
            past = days[t - 1 - lookback]
            mom = {p: closes[p][prev] / closes[p][past] - 1 for p in closes}
            picks = [p for p in sorted(mom, key=mom.get, reverse=True) if mom[p] > 0][:hold]
            new = {p: 1 / hold for p in picks}
            ret -= sum(abs(new.get(p, 0) - weights.get(p, 0)) for p in set(new) | set(weights)) * cost_per_side
            weights = new
        ret += sum(w * (closes[p][today] / closes[p][prev] - 1 - fund.get(p, {}).get(today, 0.0))
                   for p, w in weights.items())
        out[today] = ret
    return out


# ---------- regime study (docs/REGIME_STUDY.md) ----------

DAY_MS = 86_400_000


def btc_bull_regime(bars: list[dict], btc_daily: list[dict], sma_days: int = 200) -> list[bool | None]:
    """Per 4h bar: True when BTC's last *completed* daily close is above its sma_days SMA, False
    below, None before enough history. A daily bar counts only once it has closed by the 4h bar's close."""
    closes = [b["close"] for b in btc_daily]
    ends = [b["datetime"] + DAY_MS for b in btc_daily]
    out, j, window_sum = [], -1, 0.0
    step = (bars[1]["datetime"] - bars[0]["datetime"]) if len(bars) > 1 else 4 * 3_600_000
    for bar in bars:
        bar_end = bar["datetime"] + step
        while j + 1 < len(btc_daily) and ends[j + 1] <= bar_end:
            j += 1
            window_sum += closes[j]
            if j >= sma_days:
                window_sum -= closes[j - sma_days]
        out.append(None if j + 1 < sma_days else closes[j] > window_sum / sma_days)
    return out


def regime_donchian(bars: list[dict], bull: list[bool | None], *, shorts: bool) -> Signals:
    """Live Donchian (100/20/3 ATR); longs only in a BTC bull regime and, if shorts, shorts only in a
    bear regime. Exits are the normal channel exits so an open trade is never force-closed by the filter."""
    from forex_ai_analyst.lab.strategies import donchian

    base = donchian(bars, entry_n=100, exit_n=20, stop_atr=3.0, sides="both" if shorts else "long")
    le = [bool(e and bull[i] is True) for i, e in enumerate(base.long_entry)]
    se = [bool(shorts and e and bull[i] is False) for i, e in enumerate(base.short_entry)]
    return Signals(le, se, base.long_exit, base.short_exit, base.stop_distance)
