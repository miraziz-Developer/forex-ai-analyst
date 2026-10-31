"""The strategy catalogue run through one lab, with a development / holdout split (docs/HUNDRED_STUDY.md).

    python3 -m forex_ai_analyst.lab.hundred_study             development: every rule on every market group
    python3 -m forex_ai_analyst.lab.hundred_study --holdout   the qualifying rules, once, on the holdout years

Four market groups on daily bars: CRYPTO (62 coins, Binance USD-M, 2021-2026, real funding), FXM (10 FX pairs
and gold and silver, Dukascopy bid/ask, 2010-2026), IDX (14 stock indices, Yahoo, 2004-2026, long only) and
COMM (Yahoo continuous commodity futures, 2004-2026). Rules use the lab engine (entry at the next open, a
3 ATR(14) protective stop unless the rule has its own exit, costs per group). Results per trade in R.
"""
from __future__ import annotations

import json
import math
import random
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

from forex_ai_analyst.forex.data import CACHE_DIR
from forex_ai_analyst.lab import data, engine
from forex_ai_analyst.lab.candidates import rsi
from forex_ai_analyst.lab.strategies import adx, atr, ema, rolling_extreme, rolling_mean_std

DAY = 86_400_000
OUT = Path("research_output")
SPLIT = {"CRYPTO": "2024-01-01", "FXM": "2018-01-01", "IDX": "2014-01-01", "COMM": "2014-01-01"}
COST_PER_SIDE_PCT = {"CRYPTO": 0.07, "FXM": 0.01, "IDX": 0.03, "COMM": 0.05}     # fee + slippage, % of price
SWAP_PER_YEAR = {"CRYPTO": 0.0, "FXM": 0.0, "IDX": 0.04, "COMM": 0.03}           # on longs; CRYPTO pays funding
METAL_EXTRA = {"XAUUSD": 0.02, "XAGUSD": 0.04}
LONG_ONLY = {"IDX"}
COMM_SYMBOLS = ("CC", "CT", "GC", "GF", "HE", "HO", "KC", "LE", "OJ", "PA", "PL", "RB", "SB", "SI", "ZC", "ZL", "ZM",
                "ZO", "ZR", "ZS", "ZW")
IDX_SYMBOLS = ("^GSPC", "^NDX", "^DJI", "^GDAXI", "^FTSE", "^N225", "^AXJO", "^HSI", "^FCHI", "^STOXX50E", "^IBEX",
               "^AEX", "^SSMI", "^GSPTSE")


# ---------- data ----------

def yahoo_cached(symbol: str) -> list[dict]:
    """The newest cached Yahoo daily file (data frozen as first downloaded; no network)."""
    files = sorted(CACHE_DIR.glob(f"{symbol.replace('=', '_')}-1d-v3-*.json"))
    return json.loads(files[-1].read_text()) if files else []


def fxm_daily(market: str) -> list[dict]:
    from forex_ai_analyst.forex import dukascopy
    from forex_ai_analyst.forex import h1_ml_study as h1
    dukascopy.POINT.update({"XAGUSD": 1e-3})
    groups: dict[int, list[dict]] = {}
    for b in h1.load(market):
        groups.setdefault(b["datetime"] // DAY * DAY, []).append(b)

    def mid(b, k):
        return (b[f"bid_{k}"] + b[f"ask_{k}"]) / 2
    return [{"datetime": d, "open": mid(g[0], "open"), "high": max(mid(b, "high") for b in g),
             "low": min(mid(b, "low") for b in g), "close": mid(g[-1], "close"), "volume": 0}
            for d, g in sorted(groups.items()) if len(g) >= 10]


def load_universe(name: str) -> dict[str, tuple[list[dict], list[tuple[int, float]]]]:
    if name == "CRYPTO":
        from forex_ai_analyst.lab import combination_study as cs
        from forex_ai_analyst.lab.engine2_study import LIVE_MARKETS, NEW_MARKETS
        from forex_ai_analyst.lab.rebound_expansion import CANDIDATES, first_full_month
        out = {}
        for pair in LIVE_MARKETS + NEW_MARKETS + tuple(f"{c}-USDT" for c in CANDIDATES):
            start = cs.START if pair in LIVE_MARKETS + NEW_MARKETS else first_full_month(pair)
            out[pair] = (data.resample(data.load_klines(pair, start, cs.END, "1h"), 24),
                         data.load_funding(pair, start, cs.END))
        return out
    if name == "FXM":
        from forex_ai_analyst.forex.three_ma_study import MARKETS
        return {m: (fxm_daily(m), []) for m in MARKETS}
    symbols = IDX_SYMBOLS if name == "IDX" else tuple(f"{s}=F" for s in COMM_SYMBOLS)
    return {s: (bars, []) for s in symbols if len(bars := yahoo_cached(s)) > 500}


# ---------- helpers ----------

def sma(x: list[float], n: int) -> list[float | None]:
    out, total = [None] * len(x), 0.0
    for i, v in enumerate(x):
        total += v
        if i >= n:
            total -= x[i - n]
        if i >= n - 1:
            out[i] = total / n
    return out


def ok(*xs) -> bool:
    return all(v is not None and not (isinstance(v, float) and math.isnan(v)) for v in xs)


def cross_up(a, level, i) -> bool:
    return i > 0 and ok(a[i - 1], a[i]) and a[i - 1] <= level < a[i]


def cross_down(a, level, i) -> bool:
    return i > 0 and ok(a[i - 1], a[i]) and a[i - 1] >= level > a[i]


def closes(bars):
    return [b["close"] for b in bars]


def signals(bars, le, se, lx, sx, stop_atr=3.0, max_bars=None, trail_atr=None) -> engine.Signals:
    a = atr(bars, 14)
    return engine.Signals(le, se, lx, sx, [x * stop_atr if x else None for x in a],
                          trail_distance=[x * trail_atr if x else None for x in a] if trail_atr else None,
                          max_bars=max_bars)


def state_rule(bars, long_state, short_state, **kw) -> engine.Signals:
    """Enter when a state starts, leave when it ends."""
    n = len(bars)
    le = [bool(long_state[i] and not long_state[i - 1]) if i else False for i in range(n)]
    se = [bool(short_state[i] and not short_state[i - 1]) if i else False for i in range(n)]
    return signals(bars, le, se, [not x for x in long_state], [not x for x in short_state], **kw)


def states(c, f, g):
    """(f > g, f < g) per bar where both are known."""
    return ([ok(f[i], g[i]) and f[i] > g[i] for i in range(len(c))],
            [ok(f[i], g[i]) and f[i] < g[i] for i in range(len(c))])


# ---------- trend rules ----------

def ma_cross(fast, slow, kind="sma"):
    def rule(bars):
        c = closes(bars)
        avg = sma if kind == "sma" else ema
        return state_rule(bars, *states(c, avg(c, fast), avg(c, slow)))
    return rule


def price_vs_sma(n):
    def rule(bars):
        c = closes(bars)
        return state_rule(bars, *states(c, c, sma(c, n)))
    return rule


def donchian(entry_n, exit_n):
    def rule(bars):
        hi, lo = rolling_extreme([b["high"] for b in bars], entry_n, True), rolling_extreme([b["low"] for b in bars], entry_n, False)
        xhi, xlo = rolling_extreme([b["high"] for b in bars], exit_n, True), rolling_extreme([b["low"] for b in bars], exit_n, False)
        c, n = closes(bars), len(bars)

        def prev(s, i):
            return s[i - 1] if i else None
        return signals(bars, [ok(prev(hi, i)) and c[i] > prev(hi, i) for i in range(n)],
                       [ok(prev(lo, i)) and c[i] < prev(lo, i) for i in range(n)],
                       [ok(prev(xlo, i)) and c[i] < prev(xlo, i) for i in range(n)],
                       [ok(prev(xhi, i)) and c[i] > prev(xhi, i) for i in range(n)])
    return rule


def triple_ma(bars):
    c = closes(bars)
    x, y, z = sma(c, 5), sma(c, 20), sma(c, 50)
    return state_rule(bars, [ok(x[i], y[i], z[i]) and x[i] > y[i] > z[i] for i in range(len(c))],
                      [ok(x[i], y[i], z[i]) and x[i] < y[i] < z[i] for i in range(len(c))])


def macd_rule(zero_line=False):
    def rule(bars):
        c = closes(bars)
        line = [f - s if ok(f, s) else None for f, s in zip(ema(c, 12), ema(c, 26))]
        sig = ema([v if v is not None else 0.0 for v in line], 9)
        return state_rule(bars, *states(c, line, [0.0] * len(c) if zero_line else sig))
    return rule


def tsmom(n):
    def rule(bars):
        c = closes(bars)
        r = [c[i] / c[i - n] - 1 if i >= n else None for i in range(len(c))]
        return state_rule(bars, *states(c, r, [0.0] * len(c)))
    return rule


def channel_break(kind):
    """Keltner (EMA20 +- 2 ATR) or Bollinger (20, 2) breakout; out back through the middle."""
    def rule(bars):
        c, n, a = closes(bars), len(bars), atr(bars, 14)
        if kind == "keltner":
            mid = ema(c, 20)
            width = [2 * x if ok(x) else None for x in a]
        else:
            mid, sd = rolling_mean_std(c, 20)
            width = [2 * s if ok(s) else None for s in sd]
        up = [m + w if ok(m, w) else None for m, w in zip(mid, width)]
        dn = [m - w if ok(m, w) else None for m, w in zip(mid, width)]
        return signals(bars, [ok(up[i]) and c[i] > up[i] for i in range(n)], [ok(dn[i]) and c[i] < dn[i] for i in range(n)],
                       [ok(mid[i]) and c[i] < mid[i] for i in range(n)], [ok(mid[i]) and c[i] > mid[i] for i in range(n)])
    return rule


def di_lines(bars, n=14):
    up, dn, tr = [0.0], [0.0], [bars[0]["high"] - bars[0]["low"]]
    for a, b in zip(bars, bars[1:]):
        u, d = b["high"] - a["high"], a["low"] - b["low"]
        up.append(u if u > d and u > 0 else 0.0)
        dn.append(d if d > u and d > 0 else 0.0)
        tr.append(max(b["high"] - b["low"], abs(b["high"] - a["close"]), abs(b["low"] - a["close"])))

    def wilder(x):
        out, v = [], None
        for i, val in enumerate(x):
            v = val if v is None else (v * (n - 1) + val) / n
            out.append(v if i >= n else None)
        return out
    su, sd, st = wilder(up), wilder(dn), wilder(tr)
    return ([100 * a / t if ok(a, t) and t else None for a, t in zip(su, st)],
            [100 * a / t if ok(a, t) and t else None for a, t in zip(sd, st)])


def adx_di(bars):
    pdi, mdi = di_lines(bars)
    x, n = adx(bars, 14), len(bars)
    return state_rule(bars, [ok(pdi[i], mdi[i], x[i]) and pdi[i] > mdi[i] and x[i] > 25 for i in range(n)],
                      [ok(pdi[i], mdi[i], x[i]) and mdi[i] > pdi[i] and x[i] > 25 for i in range(n)])


def supertrend(bars, n=10, mult=3.0):
    a, line, trend = atr(bars, n), [0] * len(bars), 1
    upper = lower = None
    for i, b in enumerate(bars):
        if a[i] is None:
            continue
        mid = (b["high"] + b["low"]) / 2
        bu, bl, pc = mid + mult * a[i], mid - mult * a[i], bars[i - 1]["close"]
        upper = bu if upper is None or bu < upper or pc > upper else upper
        lower = bl if lower is None or bl > lower or pc < lower else lower
        trend = -1 if trend == 1 and b["close"] < lower else 1 if trend == -1 and b["close"] > upper else trend
        line[i] = trend
    return state_rule(bars, [t == 1 for t in line], [t == -1 for t in line])


def psar(bars, step=0.02, cap=0.2):
    n, trend = len(bars), [None] * len(bars)
    up, sar, ep, af = True, bars[0]["low"], bars[0]["high"], step
    for i in range(2, n):
        sar += af * (ep - sar)
        b = bars[i]
        if up:
            sar = min(sar, bars[i - 1]["low"], bars[i - 2]["low"])
            if b["low"] < sar:
                up, sar, ep, af = False, ep, b["low"], step
            elif b["high"] > ep:
                ep, af = b["high"], min(cap, af + step)
        else:
            sar = max(sar, bars[i - 1]["high"], bars[i - 2]["high"])
            if b["high"] > sar:
                up, sar, ep, af = True, ep, b["high"], step
            elif b["low"] < ep:
                ep, af = b["low"], min(cap, af + step)
        trend[i] = up
    return state_rule(bars, [t is True for t in trend], [t is False for t in trend])


def ichimoku(bars):
    hi, lo, n, c = [b["high"] for b in bars], [b["low"] for b in bars], len(bars), closes(bars)

    def mid(k):
        return [(h + l_) / 2 if ok(h, l_) else None for h, l_ in zip(rolling_extreme(hi, k, True), rolling_extreme(lo, k, False))]
    tenkan, kijun, b52 = mid(9), mid(26), mid(52)
    span_a = [None] * 26 + [(t + k) / 2 if ok(t, k) else None for t, k in zip(tenkan, kijun)][:n - 26]
    span_b = [None] * 26 + b52[:n - 26]
    above = [ok(span_a[i], span_b[i]) and c[i] > max(span_a[i], span_b[i]) for i in range(n)]
    below = [ok(span_a[i], span_b[i]) and c[i] < min(span_a[i], span_b[i]) for i in range(n)]
    tk_up, tk_dn = states(c, tenkan, kijun)
    return state_rule(bars, [above[i] and tk_up[i] for i in range(n)], [below[i] and tk_dn[i] for i in range(n)])


def aroon(bars, n=25):
    hi, lo, m = [b["high"] for b in bars], [b["low"] for b in bars], len(bars)
    up, dn = [None] * m, [None] * m
    for i in range(n, m):
        wh, wl = hi[i - n:i + 1], lo[i - n:i + 1]
        up[i] = 100 * wh.index(max(wh)) / n
        dn[i] = 100 * wl.index(min(wl)) / n
    return state_rule(bars, [ok(up[i], dn[i]) and up[i] > dn[i] and up[i] >= 70 for i in range(m)],
                      [ok(up[i], dn[i]) and dn[i] > up[i] and dn[i] >= 70 for i in range(m)])


def linreg_slope(bars, n=100):
    c = closes(bars)
    mx = (n - 1) / 2
    sxx = sum((x - mx) ** 2 for x in range(n))
    s = [None] * len(c)
    for i in range(n - 1, len(c)):
        w = c[i - n + 1:i + 1]
        my = sum(w) / n
        s[i] = sum((x - mx) * (y - my) for x, y in enumerate(w)) / sxx
    return state_rule(bars, *states(c, s, [0.0] * len(c)))


def heikin_ashi(bars):
    ha, n = [], len(bars)
    for i, b in enumerate(bars):
        hc = (b["open"] + b["high"] + b["low"] + b["close"]) / 4
        ho = (b["open"] + b["close"]) / 2 if i == 0 else (ha[-1][0] + ha[-1][1]) / 2
        ha.append((ho, hc))
    bull = [c > o for o, c in ha]
    le = [i >= 3 and bull[i] and bull[i - 1] and bull[i - 2] and not bull[i - 3] for i in range(n)]
    se = [i >= 3 and not (bull[i] or bull[i - 1] or bull[i - 2]) and bull[i - 3] for i in range(n)]
    return signals(bars, le, se, [not x for x in bull], bull)


def range_breakout(kind):
    """NR7 or inside day, then a close beyond its range; out after five bars."""
    def rule(bars):
        n, rng = len(bars), [b["high"] - b["low"] for b in bars]
        setup = [False] * n
        for i in range(7, n):
            setup[i] = (rng[i] == min(rng[i - 6:i + 1]) if kind == "nr7" else
                        bars[i]["high"] < bars[i - 1]["high"] and bars[i]["low"] > bars[i - 1]["low"])
        le = [i > 0 and setup[i - 1] and bars[i]["close"] > bars[i - 1]["high"] for i in range(n)]
        se = [i > 0 and setup[i - 1] and bars[i]["close"] < bars[i - 1]["low"] for i in range(n)]
        return signals(bars, le, se, [False] * n, [False] * n, max_bars=5)
    return rule


def high_52w(bars):
    c, n = closes(bars), len(bars)
    hi, lo, m50 = rolling_extreme(c, 252, True), rolling_extreme(c, 252, False), sma(c, 50)
    return signals(bars, [ok(hi[i]) and c[i] >= hi[i] for i in range(n)], [ok(lo[i]) and c[i] <= lo[i] for i in range(n)],
                   [ok(m50[i]) and c[i] < m50[i] for i in range(n)], [ok(m50[i]) and c[i] > m50[i] for i in range(n)])


def chandelier(bars):
    c, n = closes(bars), len(bars)
    e = ema(c, 50)
    up = [i > 5 and ok(e[i], e[i - 5]) and c[i] > e[i] > e[i - 5] for i in range(n)]
    dn = [i > 5 and ok(e[i], e[i - 5]) and c[i] < e[i] < e[i - 5] for i in range(n)]
    le = [up[i] and not up[i - 1] if i else False for i in range(n)]
    se = [dn[i] and not dn[i - 1] if i else False for i in range(n)]
    return signals(bars, le, se, [False] * n, [False] * n, trail_atr=3.0)


def squeeze_breakout(bars):
    c, n = closes(bars), len(bars)
    mid, sd = rolling_mean_std(c, 20)
    width = [s / m if ok(m, s) and m else None for m, s in zip(mid, sd)]
    le, se = [False] * n, [False] * n
    for i in range(140, n):
        w = sorted(x for x in width[i - 120:i] if x is not None)
        if not w or width[i - 1] is None or width[i - 1] > w[len(w) // 5] or not ok(mid[i], sd[i]):
            continue
        le[i], se[i] = c[i] > mid[i] + 2 * sd[i], c[i] < mid[i] - 2 * sd[i]
    return signals(bars, le, se, [ok(mid[i]) and c[i] < mid[i] for i in range(n)],
                   [ok(mid[i]) and c[i] > mid[i] for i in range(n)])


# ---------- mean-reversion rules ----------

def connors_rsi2(bars):
    c, n = closes(bars), len(bars)
    r, m200, m5 = rsi(c, 2), sma(c, 200), sma(c, 5)
    return signals(bars, [ok(r[i], m200[i]) and c[i] > m200[i] and r[i] < 10 for i in range(n)],
                   [ok(r[i], m200[i]) and c[i] < m200[i] and r[i] > 90 for i in range(n)],
                   [ok(m5[i]) and c[i] > m5[i] for i in range(n)], [ok(m5[i]) and c[i] < m5[i] for i in range(n)],
                   max_bars=10)


def oscillator(kind):
    def rule(bars):
        c, n = closes(bars), len(bars)
        if kind == "rsi14":
            x, lo, hi, mid = rsi(c, 14), 30.0, 70.0, 50.0
        elif kind == "stoch":
            h, l_ = rolling_extreme([b["high"] for b in bars], 14, True), rolling_extreme([b["low"] for b in bars], 14, False)
            raw = [100 * (c[i] - l_[i]) / (h[i] - l_[i]) if ok(h[i], l_[i]) and h[i] > l_[i] else 50.0 for i in range(n)]
            x, lo, hi, mid = sma(raw, 3), 20.0, 80.0, 50.0
        elif kind == "cci":
            tp = [(b["high"] + b["low"] + b["close"]) / 3 for b in bars]
            m = sma(tp, 20)
            x = [None] * n
            for i in range(19, n):
                md = sum(abs(v - m[i]) for v in tp[i - 19:i + 1]) / 20
                x[i] = (tp[i] - m[i]) / (0.015 * md) if md else None
            lo, hi, mid = -100.0, 100.0, 0.0
        else:                                                             # z-score against the 20-day mean
            m, sd = rolling_mean_std(c, 20)
            x = [(c[i] - m[i]) / sd[i] if ok(m[i], sd[i]) and sd[i] else None for i in range(n)]
            lo, hi, mid = -2.0, 2.0, 0.0
        le = [cross_up(x, lo, i) if kind != "z" else ok(x[i]) and x[i] < lo for i in range(n)]
        se = [cross_down(x, hi, i) if kind != "z" else ok(x[i]) and x[i] > hi for i in range(n)]
        return signals(bars, le, se, [ok(x[i]) and x[i] > mid for i in range(n)],
                       [ok(x[i]) and x[i] < mid for i in range(n)], max_bars=20)
    return rule


def ibs(bars):
    v = [(b["close"] - b["low"]) / (b["high"] - b["low"]) if b["high"] > b["low"] else 0.5 for b in bars]
    return signals(bars, [x < 0.2 for x in v], [x > 0.8 for x in v], [x > 0.8 for x in v], [x < 0.2 for x in v],
                   max_bars=5)


def double_seven(bars):
    c, n = closes(bars), len(bars)
    m200, lo7, hi7 = sma(c, 200), rolling_extreme(c, 7, False), rolling_extreme(c, 7, True)
    return signals(bars, [ok(m200[i], lo7[i]) and c[i] > m200[i] and c[i] <= lo7[i] for i in range(n)],
                   [ok(m200[i], hi7[i]) and c[i] < m200[i] and c[i] >= hi7[i] for i in range(n)],
                   [ok(hi7[i]) and c[i] >= hi7[i] for i in range(n)], [ok(lo7[i]) and c[i] <= lo7[i] for i in range(n)])


def three_down(bars):
    c, n = closes(bars), len(bars)
    m200, m5 = sma(c, 200), sma(c, 5)
    le = [i >= 3 and ok(m200[i]) and c[i] > m200[i] and c[i] < c[i - 1] < c[i - 2] < c[i - 3] for i in range(n)]
    se = [i >= 3 and ok(m200[i]) and c[i] < m200[i] and c[i] > c[i - 1] > c[i - 2] > c[i - 3] for i in range(n)]
    return signals(bars, le, se, [ok(m5[i]) and c[i] > m5[i] for i in range(n)],
                   [ok(m5[i]) and c[i] < m5[i] for i in range(n)], max_bars=10)


def weekly_reversal(bars):
    c, n = closes(bars), len(bars)
    r5 = [c[i] / c[i - 5] - 1 if i >= 5 else None for i in range(n)]
    sd = [None] * n
    for i in range(110, n):
        sd[i] = statistics.pstdev([x for x in r5[i - 100:i] if x is not None])
    le = [ok(r5[i], sd[i]) and sd[i] > 0 and r5[i] < -2 * sd[i] for i in range(n)]
    se = [ok(r5[i], sd[i]) and sd[i] > 0 and r5[i] > 2 * sd[i] for i in range(n)]
    return signals(bars, le, se, [False] * n, [False] * n, max_bars=5)


# ---------- calendar rules (long only) ----------

def month_of(b) -> int:
    return datetime.fromtimestamp(b["datetime"] / 1000, timezone.utc).month


def turn_of_month(bars):
    n = len(bars)
    last_bar = [i + 1 < n and month_of(bars[i + 1]) != month_of(bars[i]) for i in range(n)]
    return signals(bars, last_bar, [False] * n, [False] * n, [False] * n, max_bars=4)


def halloween(bars):
    return state_rule(bars, [month_of(b) in (11, 12, 1, 2, 3, 4) for b in bars], [False] * len(bars), stop_atr=10.0)


RULES = {
    "sma_50_200": ma_cross(50, 200), "sma_20_50": ma_cross(20, 50), "ema_12_26": ma_cross(12, 26, "ema"),
    "price_sma200": price_vs_sma(200), "price_sma50": price_vs_sma(50),
    "donchian_20_10": donchian(20, 10), "donchian_55_20": donchian(55, 20), "donchian_100_20": donchian(100, 20),
    "triple_ma_5_20_50": triple_ma, "macd_signal": macd_rule(), "macd_zero": macd_rule(True),
    "tsmom_252": tsmom(252), "tsmom_126": tsmom(126), "tsmom_63": tsmom(63), "tsmom_21": tsmom(21),
    "keltner_break": channel_break("keltner"), "bollinger_break": channel_break("bollinger"),
    "adx_di": adx_di, "supertrend": supertrend, "psar": psar, "ichimoku": ichimoku, "aroon": aroon,
    "linreg_100": linreg_slope, "heikin_ashi": heikin_ashi, "nr7_break": range_breakout("nr7"),
    "inside_break": range_breakout("inside"), "high_52w": high_52w, "chandelier": chandelier,
    "squeeze_break": squeeze_breakout,
    "connors_rsi2": connors_rsi2, "rsi14_reversal": oscillator("rsi14"), "stoch_reversal": oscillator("stoch"),
    "cci_reversal": oscillator("cci"), "zscore_reversal": oscillator("z"), "ibs": ibs, "double_seven": double_seven,
    "three_down": three_down, "weekly_reversal": weekly_reversal,
    "turn_of_month": turn_of_month, "halloween": halloween,
}
CALENDAR = {"turn_of_month", "halloween"}


# ---------- running ----------

def long_only(sig: engine.Signals) -> engine.Signals:
    n = len(sig.long_entry)
    return engine.Signals(sig.long_entry, [False] * n, sig.long_exit, [False] * n, sig.stop_distance,
                          trail_distance=sig.trail_distance, target_distance=sig.target_distance, max_bars=sig.max_bars)


def run_rule(rule_name: str, universe: str, markets: dict) -> list[dict]:
    rows = []
    for name, (bars, funding) in markets.items():
        if len(bars) < 300:
            continue
        sig = RULES[rule_name](bars)
        if universe in LONG_ONLY or rule_name in CALENDAR:
            sig = long_only(sig)
        costs = engine.Costs(fee_pct_per_side=COST_PER_SIDE_PCT[universe] + METAL_EXTRA.get(name, 0.0),
                             slippage_pct_per_side=0.0, charge_funding=universe == "CRYPTO")
        for t in engine.run(bars, sig, funding, costs=costs).trades:
            days = (t["exit_time"] - t["entry_time"]) / DAY
            swap = t["qty"] * t["entry"] * SWAP_PER_YEAR[universe] * days / 365 if t["direction"] > 0 else 0.0
            rows.append({"market": name, "t": t["entry_time"], "side": t["direction"],
                         "r": (t["net"] - swap) / t["risk"] if t["risk"] else 0.0})
    return rows


def evaluate(rows: list[dict]) -> dict:
    if len(rows) < 2:
        return {"trades": len(rows), "t_stat": None, "mean_r": 0.0, "p": 0.0, "profit_factor": None,
                "markets_positive": 0.0}
    r = [x["r"] for x in rows]
    mean, sd = statistics.mean(r), statistics.stdev(r)
    gains, losses = sum(x for x in r if x > 0), -sum(x for x in r if x < 0)
    by_month: dict[str, float] = {}
    per: dict[str, float] = {}
    for x in rows:
        key = datetime.fromtimestamp(x["t"] / 1000, timezone.utc).strftime("%Y-%m")
        by_month[key] = by_month.get(key, 0.0) + x["r"]
        per[x["market"]] = per.get(x["market"], 0.0) + x["r"]
    vals, rng = list(by_month.values()), random.Random(7)
    p = sum(sum(rng.choice(vals) for _ in vals) > 0 for _ in range(1000)) / 1000
    return {"trades": len(r), "win": round(sum(x > 0 for x in r) / len(r), 3), "mean_r": round(mean, 4),
            "profit_factor": round(gains / losses, 3) if losses else None,
            "t_stat": round(mean / sd * math.sqrt(len(r)), 2) if sd else None, "p": p,
            "markets_positive": round(sum(v > 0 for v in per.values()) / max(1, len(per)), 2)}


def qualifies_dev(e: dict) -> bool:
    return ((e.get("t_stat") or 0) >= 2.0 and e["trades"] >= 100 and (e.get("profit_factor") or 0) >= 1.15
            and e["markets_positive"] >= 0.6)


def passes_holdout(e: dict) -> bool:
    return (e["trades"] >= 50 and e["mean_r"] > 0 and e["p"] >= 0.95 and (e.get("profit_factor") or 0) >= 1.15
            and e["markets_positive"] >= 0.55)


def split_rows(rows: list[dict], universe: str, holdout: bool) -> list[dict]:
    cut = int(datetime.fromisoformat(SPLIT[universe]).replace(tzinfo=timezone.utc).timestamp() * 1000)
    return [x for x in rows if (x["t"] >= cut) == holdout]


def main() -> None:
    OUT.mkdir(exist_ok=True)
    holdout = "--holdout" in sys.argv
    chosen = json.loads((OUT / "hundred_dev.json").read_text())["qualified"] if holdout else None
    results = {}
    for universe in ("CRYPTO", "FXM", "IDX", "COMM"):
        markets = load_universe(universe)
        print(universe, len(markets), "markets", flush=True)
        for rule in RULES:
            key = f"{rule}@{universe}"
            if holdout and key not in chosen:
                continue
            results[key] = evaluate(split_rows(run_rule(rule, universe, markets), universe, holdout))
            print(key, json.dumps(results[key]), flush=True)
    if holdout:
        passed = sorted((k for k, v in results.items() if passes_holdout(v)),
                        key=lambda k: results[k]["t_stat"] or 0, reverse=True)
        (OUT / "hundred_holdout.json").write_text(json.dumps({"results": results, "passed": passed,
                                                             "top10": passed[:10]}, indent=2) + "\n")
        print("PASSED", passed)
        return
    qualified = sorted((k for k, v in results.items() if qualifies_dev(v)),
                       key=lambda k: results[k]["t_stat"] or 0, reverse=True)[:25]
    (OUT / "hundred_dev.json").write_text(json.dumps({"results": results, "trials": len(results),
                                                     "qualified": qualified}, indent=2) + "\n")
    print("TRIALS", len(results), "QUALIFIED", qualified)


if __name__ == "__main__":
    main()
