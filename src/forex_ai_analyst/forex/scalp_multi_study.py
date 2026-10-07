"""Seven Investopedia scalping rules and the ForexFactory 3-EMA rule, alone and combined (docs/SCALP_MULTI_STUDY.md).

    python3 -m forex_ai_analyst.forex.scalp_multi_study            development period: every rule and both combinations
    python3 -m forex_ai_analyst.forex.scalp_multi_study --final V  holdout, one named version, once
"""
from __future__ import annotations

import json
import random
import sys
from bisect import bisect_left
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from forex_ai_analyst.forex import scalp_3ema_study as base
from forex_ai_analyst.forex.index_study import sma
from forex_ai_analyst.lab.candidates import rsi
from forex_ai_analyst.lab.strategies import atr

PAIRS, PIP, DEV, HOLDOUT, M1 = base.PAIRS, base.PIP, base.DEV, base.HOLDOUT, base.M1
EXTRA_PIPS = 0.7          # Dukascopy's own commission ($35 per million per side), on top of its raw spread
RETAIL_EXTRA_PIPS = 1.2   # sensitivity: a retail no-commission account with ~1 pip wider spreads
GAP = 30 * M1             # positions are closed before any pause in the data longer than this (weekends)
STOP_ATR, MAX_HOLD = 2.0, 24


# ---------- indicators (None while warming up) ----------

def sma_opt(values: list, n: int) -> list:
    out = [None] * len(values)
    for i in range(n - 1, len(values)):
        window = values[i - n + 1:i + 1]
        if None not in window:
            out[i] = sum(window) / n
    return out


def ema_opt(values: list, n: int) -> list:
    out, value, k, seen = [None] * len(values), None, 2 / (n + 1), 0
    for i, v in enumerate(values):
        if v is None:
            continue
        value = v if value is None else v * k + value * (1 - k)
        seen += 1
        if seen >= n:
            out[i] = value
    return out


def stochastic(bars: list[dict], n: int = 5, smooth: int = 3, d: int = 3) -> tuple[list, list]:
    raw = [None] * len(bars)
    for i in range(n - 1, len(bars)):
        hi = max(b["high"] for b in bars[i - n + 1:i + 1])
        lo = min(b["low"] for b in bars[i - n + 1:i + 1])
        raw[i] = 50.0 if hi == lo else 100 * (bars[i]["close"] - lo) / (hi - lo)
    k = sma_opt(raw, smooth)
    return k, sma_opt(k, d)


def bollinger(closes: list[float], n: int, width: float) -> tuple[list, list, list]:
    mid, upper, lower = sma(closes, n), [None] * len(closes), [None] * len(closes)
    for i in range(n - 1, len(closes)):
        w = closes[i - n + 1:i + 1]
        sd = (sum((x - mid[i]) ** 2 for x in w) / n) ** 0.5
        upper[i], lower[i] = mid[i] + width * sd, mid[i] - width * sd
    return mid, upper, lower


def macd(closes: list[float]) -> tuple[list, list]:
    fast, slow = ema_opt(closes, 12), ema_opt(closes, 26)
    line = [f - s if f is not None and s is not None else None for f, s in zip(fast, slow)]
    return line, ema_opt(line, 9)


def rmi(closes: list[float], n: int = 14, momentum: int = 5) -> list:
    """Relative momentum index: RSI of the change over `momentum` bars, Wilder smoothing."""
    out, gain, loss = [None] * len(closes), None, None
    for i in range(momentum, len(closes)):
        change = closes[i] - closes[i - momentum]
        up, down = max(change, 0.0), max(-change, 0.0)
        gain = up if gain is None else (gain * (n - 1) + up) / n
        loss = down if loss is None else (loss * (n - 1) + down) / n
        if i >= momentum + n:
            out[i] = 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)
    return out


def supertrend(bars: list[dict], n: int = 10, mult: float = 3.0) -> list:
    """SuperTrend line per bar: below price in an uptrend, above it in a downtrend."""
    a, out = atr(bars, n), [None] * len(bars)
    upper = lower = None
    trend = 1
    for i, b in enumerate(bars):
        if a[i] is None:
            continue
        mid = (b["high"] + b["low"]) / 2
        bu, bl = mid + mult * a[i], mid - mult * a[i]
        prev_close = bars[i - 1]["close"]
        upper = bu if upper is None or bu < upper or prev_close > upper else upper
        lower = bl if lower is None or bl > lower or prev_close < lower else lower
        if trend == 1 and b["close"] < lower:
            trend = -1
        elif trend == -1 and b["close"] > upper:
            trend = 1
        out[i] = lower if trend == 1 else upper
    return out


def slope(closes: list[float], n: int = 20) -> list:
    out, mx = [None] * len(closes), (n - 1) / 2
    sxx = sum((x - mx) ** 2 for x in range(n))
    for i in range(n - 1, len(closes)):
        w = closes[i - n + 1:i + 1]
        my = sum(w) / n
        out[i] = sum((x - mx) * (y - my) for x, y in enumerate(w)) / sxx
    return out


def up_cross(a: list, b, i: int) -> bool:
    b0, b1 = (b[i - 1], b[i]) if isinstance(b, list) else (b, b)
    return i > 0 and None not in (a[i - 1], a[i], b0, b1) and a[i - 1] <= b0 and a[i] > b1


def down_cross(a: list, b, i: int) -> bool:
    b0, b1 = (b[i - 1], b[i]) if isinstance(b, list) else (b, b)
    return i > 0 and None not in (a[i - 1], a[i], b0, b1) and a[i - 1] >= b0 and a[i] < b1


# ---------- the seven article rules, as signals on completed bars ----------

def _empty(n: int) -> dict:
    return {key: [False] * n for key in ("le", "se", "xl", "xs")} | {"lstop": [None] * n, "sstop": [None] * n}


def _ribbon(bars):
    c = [b["close"] for b in bars]
    s5, s8, s13 = sma(c, 5), sma(c, 8), sma(c, 13)
    ok = [None not in (s5[i], s8[i], s13[i]) for i in range(len(c))]
    up = [ok[i] and s5[i] > s8[i] > s13[i] for i in range(len(c))]
    down = [ok[i] and s5[i] < s8[i] < s13[i] for i in range(len(c))]
    return c, s5, s8, s13, ok, up, down


def r1_ribbon(bars):
    """5-8-13 SMA ribbon on 2-minute bars: enter when it realigns and all three lines turn the same way;
    exit when price closes through the 13 SMA."""
    c, s5, s8, s13, ok, up, down = _ribbon(bars)
    out = _empty(len(bars))
    for i in range(1, len(bars)):
        if not (ok[i] and ok[i - 1]):
            continue
        rising = s5[i] > s5[i - 1] and s8[i] > s8[i - 1] and s13[i] > s13[i - 1]
        falling = s5[i] < s5[i - 1] and s8[i] < s8[i - 1] and s13[i] < s13[i - 1]
        out["le"][i] = up[i] and not up[i - 1] and rising
        out["se"][i] = down[i] and not down[i - 1] and falling
        out["xl"][i], out["xs"][i] = c[i] < s13[i], c[i] > s13[i]
    return out


def r2_ribbon_stochastic(bars):
    """Ribbon aligned + 5-3-3 stochastic turning up from oversold (down from overbought); exit into the
    13-bar 3-SD Bollinger band or when the stochastic crosses back against the position. 2-minute bars."""
    c, s5, s8, s13, ok, up, down = _ribbon(bars)
    k, d = stochastic(bars)
    _, upper, lower = bollinger(c, 13, 3.0)
    out = _empty(len(bars))
    for i in range(1, len(bars)):
        out["le"][i] = up[i] and up_cross(k, 20.0, i)
        out["se"][i] = down[i] and down_cross(k, 80.0, i)
        out["xl"][i] = (upper[i] is not None and bars[i]["high"] >= upper[i]) or down_cross(k, d, i)
        out["xs"][i] = (lower[i] is not None and bars[i]["low"] <= lower[i]) or up_cross(k, d, i)
    return out


def r3_momentum(bars):
    """MACD(12,26,9) + RSI(14) on 5-minute bars, entries and exits as the article lists them."""
    c = [b["close"] for b in bars]
    line, signal = macd(c)
    r = rsi(c, 14)
    out = _empty(len(bars))
    for i in range(1, len(bars)):
        if None in (line[i], signal[i], r[i]):
            continue
        out["le"][i] = (up_cross(line, signal, i) and r[i] > 50) or (up_cross(r, 30.0, i) and line[i] > signal[i])
        out["se"][i] = (down_cross(line, signal, i) and r[i] < 50) or (down_cross(r, 70.0, i) and line[i] < signal[i])
        out["xl"][i] = down_cross(line, signal, i) or down_cross(r, 70.0, i)
        out["xs"][i] = up_cross(line, signal, i) or up_cross(r, 30.0, i)
    return out


def r4_pivot_reversal(bars, left: int = 4, right: int = 2):
    """TradingView's pivot reversal: after a confirmed pivot high, a buy stop one tick (0.1 pip) above it,
    working until it fires; mirrored for pivot lows; stop and reverse. 5-minute bars."""
    out = _empty(len(bars))
    long_level = short_level = None
    for i in range(left + right, len(bars)):
        j = i - right
        highs = [b["high"] for b in bars[j - left:j + right + 1]]
        lows = [b["low"] for b in bars[j - left:j + right + 1]]
        if bars[j]["high"] == max(highs) and highs.count(bars[j]["high"]) == 1:
            long_level = bars[j]["high"] + 0.1 * PIP
        if bars[j]["low"] == min(lows) and lows.count(bars[j]["low"]) == 1:
            short_level = bars[j]["low"] - 0.1 * PIP
        out["lstop"][i], out["sstop"][i] = long_level, short_level
        if long_level is not None and i + 1 < len(bars) and bars[i + 1]["high"] >= long_level:
            long_level = None                          # fired: re-armed by the next pivot
        if short_level is not None and i + 1 < len(bars) and bars[i + 1]["low"] <= short_level:
            short_level = None
    return out


def r5_rmi_supertrend(bars):
    """RMI(14, 5) leaving oversold above SuperTrend(10, 3) buys, leaving overbought below it sells; exit when
    the RMI turns back or price closes through the SuperTrend. 5-minute bars."""
    c = [b["close"] for b in bars]
    m, st = rmi(c), supertrend(bars)
    out = _empty(len(bars))
    for i in range(1, len(bars)):
        if st[i] is None:
            continue
        out["le"][i] = up_cross(m, 30.0, i) and c[i] > st[i]
        out["se"][i] = down_cross(m, 70.0, i) and c[i] < st[i]
        out["xl"][i] = c[i] < st[i] or down_cross(m, 70.0, i)
        out["xs"][i] = c[i] > st[i] or up_cross(m, 30.0, i)
    return out


def r6_regression_bands(bars):
    """Close at or beyond the 20-bar 2-SD Bollinger band against a 20-bar regression slope pointing the other
    way (buy the lower band in an up-slope, sell the upper band in a down-slope); exit at the middle band."""
    c = [b["close"] for b in bars]
    mid, upper, lower = bollinger(c, 20, 2.0)
    s = slope(c)
    out = _empty(len(bars))
    for i in range(len(bars)):
        if mid[i] is None:
            continue
        out["le"][i] = c[i] <= lower[i] and s[i] > 0
        out["se"][i] = c[i] >= upper[i] and s[i] < 0
        out["xl"][i], out["xs"][i] = c[i] >= mid[i], c[i] <= mid[i]
    return out


def r7_ema_rsi(bars):
    """EMA 9/21 with RSI(14): close > fast > slow and RSI crossing up out of 30, or a fast-over-slow cross
    with RSI > 50, buys (mirrored for sells); exit on the opposite EMA cross. 5-minute bars."""
    c = [b["close"] for b in bars]
    fast, slow, r = ema_opt(c, 9), ema_opt(c, 21), rsi(c, 14)
    out = _empty(len(bars))
    for i in range(1, len(bars)):
        if None in (fast[i], slow[i], r[i]):
            continue
        out["le"][i] = (c[i] > fast[i] > slow[i] and up_cross(r, 30.0, i)) or (up_cross(fast, slow, i) and r[i] > 50)
        out["se"][i] = (c[i] < fast[i] < slow[i] and down_cross(r, 70.0, i)) or (down_cross(fast, slow, i) and r[i] < 50)
        out["xl"][i], out["xs"][i] = down_cross(fast, slow, i), up_cross(fast, slow, i)
    return out


@dataclass(frozen=True)
class Rule:
    name: str
    minutes: int
    signals: object
    stop_atr: float = STOP_ATR
    target_atr: float | None = None
    max_hold: int = MAX_HOLD


RULES = (Rule("r1_ribbon", 2, r1_ribbon), Rule("r2_ribbon_stochastic", 2, r2_ribbon_stochastic),
         Rule("r3_momentum", 5, r3_momentum), Rule("r4_pivot_reversal", 5, r4_pivot_reversal),
         Rule("r5_rmi_supertrend", 5, r5_rmi_supertrend), Rule("r6_regression_bands", 5, r6_regression_bands),
         Rule("r7_ema_rsi", 5, r7_ema_rsi))


# ---------- execution on one-minute bid/ask ----------

def execute(pair: str, name: str, m1: list[dict], bars: list[dict], sig: dict, stop_atr: float,
            target_atr: float | None, max_hold: int) -> list[dict]:
    """Market orders at the first minute after a bar's close; stop entries work during the next bar.
    Long trades buy the ask and sell the bid. Within a minute the protective stop is checked before the
    target and before stop entries. An opposite signal closes the position (and may reverse it)."""
    a = atr(bars, 14)
    times = [b["datetime"] for b in m1]
    trades, pos = [], None

    def close(price: float, i: int) -> None:
        nonlocal pos
        side, entry, stop = pos["side"], pos["entry"], pos["stop"]
        t = datetime.fromtimestamp(pos["ms"] / 1000, timezone.utc)
        trades.append({"pair": pair, "rule": name, "side": side, "entry_ms": pos["ms"], "day": t.date().isoformat(),
                       "hour": t.hour, "r_gross": side * (price - entry) / abs(entry - stop),
                       "risk_pips": abs(entry - stop) / PIP, "minutes": (times[i] - pos["ms"]) // M1})
        pos = None

    def open_(side: int, price: float, i: int, k: int) -> None:
        nonlocal pos
        if a[k] is None or a[k] <= 0:
            return
        stop = price - side * stop_atr * a[k]
        target = price + side * target_atr * a[k] if target_atr else None
        pos = {"side": side, "entry": price, "stop": stop, "target": target, "k": k, "ms": times[i]}

    for k, bar in enumerate(bars):
        lo, hi = bisect_left(times, bar["start"]), bisect_left(times, bar["end"])
        for i in range(lo, hi):
            b = m1[i]
            if pos is not None:
                if pos["side"] > 0:
                    if b["bid_low"] <= pos["stop"]:
                        close(min(pos["stop"], b["bid_open"]), i)
                    elif pos["target"] is not None and b["bid_high"] >= pos["target"]:
                        close(max(pos["target"], b["bid_open"]), i)
                else:
                    if b["ask_high"] >= pos["stop"]:
                        close(max(pos["stop"], b["ask_open"]), i)
                    elif pos["target"] is not None and b["ask_low"] <= pos["target"]:
                        close(min(pos["target"], b["ask_open"]), i)
            if k == 0:
                continue
            lstop, sstop = sig["lstop"][k - 1], sig["sstop"][k - 1]
            if lstop is not None and (pos is None or pos["side"] < 0) and b["ask_high"] >= lstop:
                price = max(lstop, b["ask_open"])
                if pos is not None:
                    close(price, i)
                open_(1, price, i, k - 1)
            elif sstop is not None and (pos is None or pos["side"] > 0) and b["bid_low"] <= sstop:
                price = min(sstop, b["bid_open"])
                if pos is not None:
                    close(price, i)
                open_(-1, price, i, k - 1)
        if hi == 0 or hi >= len(m1):
            continue
        last, nxt = m1[hi - 1], m1[hi]
        gap = times[hi] - times[hi - 1] > GAP
        if pos is not None:
            side = pos["side"]
            exit_signal = (sig["xl"][k] or sig["se"][k]) if side > 0 else (sig["xs"][k] or sig["le"][k])
            if gap:
                close(last["bid_close"] if side > 0 else last["ask_close"], hi - 1)
            elif exit_signal or k - pos["k"] >= max_hold:
                close(nxt["bid_open"] if side > 0 else nxt["ask_open"], hi)
        if pos is None and not gap and (sig["le"][k] or sig["se"][k]):
            side = 1 if sig["le"][k] else -1
            open_(side, nxt["ask_open"] if side > 0 else nxt["bid_open"], hi, k)
    return trades


def rule_trades(pair: str, m1: list[dict], rule: Rule) -> list[dict]:
    bars = base.aggregate(m1, rule.minutes * M1)
    return execute(pair, rule.name, m1, bars, rule.signals(bars), rule.stop_atr, rule.target_atr, rule.max_hold)


def forum_trades(pair: str, m1: list[dict]) -> list[dict]:
    """The ForexFactory 3-EMA rule as registered in docs/SCALP_3EMA_STUDY.md, in this study's units."""
    return [{"pair": pair, "rule": "r8_forum_3ema", "side": t["side"], "entry_ms": t["entry_ms"], "day": t["day"],
             "hour": t["hour"], "r_gross": t["r"], "risk_pips": t["risk_pips"], "minutes": t["minutes"]}
            for t in base.simulate(pair, m1, base.Params())]


def confluence(pair: str, m1: list[dict], components: list[dict], need: int = 2, window: int = 3) -> list[dict]:
    """Enter at the close of a 5-minute bar when at least `need` different rules opened a trade the same way
    during the last `window` bars and none opened the other way; 1.5 ATR stop, 1.5 ATR target, 24 bars."""
    bars = base.aggregate(m1, 5 * M1)
    ends = [b["end"] for b in bars]
    sig = _empty(len(bars))
    votes: list[dict] = [{} for _ in bars]
    for t in components:
        k = bisect_left(ends, t["entry_ms"] + 1)      # first bar ending after the entry
        for j in range(k, min(k + window, len(bars))):
            votes[j].setdefault(t["side"], set()).add(t["rule"])
    for k, v in enumerate(votes):
        longs, shorts = len(v.get(1, ())), len(v.get(-1, ()))
        sig["le"][k] = longs >= need and shorts == 0
        sig["se"][k] = shorts >= need and longs == 0
    return execute(pair, "confluence", m1, bars, sig, 1.5, 1.5, 24)


# ---------- evaluation in R (each trade risks the same amount) ----------

def evaluate(trades: list[dict], extra_pips: float = EXTRA_PIPS) -> dict:
    if not trades:
        return {"trades": 0}
    r = [t["r_gross"] - extra_pips / t["risk_pips"] for t in trades]
    gains, losses = sum(x for x in r if x > 0), -sum(x for x in r if x < 0)
    by_day: dict[str, float] = {}
    per: dict[str, float] = {}
    for t, x in zip(trades, r):
        by_day[t["day"]] = by_day.get(t["day"], 0.0) + x
        per[t["pair"]] = per.get(t["pair"], 0.0) + x
    vals, rng = list(by_day.values()), random.Random(11)
    p = sum(sum(rng.choice(vals) for _ in vals) > 0 for _ in range(2000)) / 2000
    return {"trades": len(r), "hit_rate": round(sum(x > 0 for x in r) / len(r), 3),
            "mean_r": round(sum(r) / len(r), 4), "total_r": round(sum(r), 1),
            "profit_factor": round(gains / losses, 3) if losses else None, "p_mean_positive": p,
            "median_risk_pips": round(sorted(t["risk_pips"] for t in trades)[len(r) // 2], 1),
            "cost_r_per_trade": round(sum(extra_pips / t["risk_pips"] for t in trades) / len(r), 3),
            "pairs_positive": sum(v > 0 for v in per.values()),
            "per_pair_total_r": {k: round(v, 1) for k, v in per.items()}}


def all_trades(period) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for pair in PAIRS:
        m1 = base.minutes(pair, *period)
        components = forum_trades(pair, m1)
        for rule in RULES:
            components += rule_trades(pair, m1, rule)
        for t in components:
            out.setdefault(t["rule"], []).append(t)
        out.setdefault("portfolio", []).extend(components)
        out.setdefault("confluence", []).extend(confluence(pair, m1, components))
    return out


def passes(report: dict) -> bool:
    return (report["trades"] >= 300 and report["mean_r"] > 0 and report["p_mean_positive"] >= 0.95
            and (report["profit_factor"] or 0) >= 1.2 and report["pairs_positive"] >= 2)


def main() -> None:
    Path("research_output").mkdir(exist_ok=True)
    if "--final" in sys.argv:
        version = sys.argv[sys.argv.index("--final") + 1]
        trades = all_trades(HOLDOUT)[version]
        report = evaluate(trades)
        out = {"version": version, "holdout": report, "retail_cost": evaluate(trades, RETAIL_EXTRA_PIPS),
               "passes": passes(report)}
        Path("research_output/scalp_multi_final.json").write_text(json.dumps(out, indent=2) + "\n")
        print(json.dumps(out))
        return
    out = {}
    for name, trades in sorted(all_trades(DEV).items()):
        out[name] = {"cost_0.7": evaluate(trades), "cost_0": evaluate(trades, 0.0),
                     "cost_1.2": evaluate(trades, RETAIL_EXTRA_PIPS)}
        print(name, json.dumps(out[name]["cost_0.7"]), "| no extra cost mean_r", out[name]["cost_0"]["mean_r"])
    Path("research_output/scalp_multi_development.json").write_text(json.dumps(out, indent=2) + "\n")


if __name__ == "__main__":
    main()
