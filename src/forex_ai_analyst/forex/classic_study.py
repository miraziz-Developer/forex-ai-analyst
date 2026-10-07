"""Three "professional" chart strategies from a broker academy page, automated on H1 bid/ask
(docs/CLASSIC_STRATEGIES_STUDY.md): pivot point break, support/resistance with the daily trend, trendline bounce.

    python3 -m forex_ai_analyst.forex.classic_study
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from forex_ai_analyst.forex import dukascopy
from forex_ai_analyst.forex import h1_ml_study as h1
from forex_ai_analyst.forex.index_study import sma
from forex_ai_analyst.forex.three_ma_study import MARKETS, extra_cost
from forex_ai_analyst.forex.trendline_study import K, pivots
from forex_ai_analyst.lab.strategies import atr

dukascopy.POINT.update({"XAGUSD": 1e-3})
NEW_YORK = ZoneInfo("America/New_York")
SPLIT = int(datetime(2018, 7, 1, tzinfo=timezone.utc).timestamp() * 1000)
NEAR = 0.25                               # "within a predefined number of pips": a quarter of the H1 ATR(14)


def offset(market: str) -> float:
    """The few pips beyond a level used for stop entries and stops: 2 pips, or the metal's equivalent."""
    return {"XAUUSD": 0.5, "XAGUSD": 0.02}.get(market, 0.02 if market.endswith("JPY") else 0.0002)


def fx_day(ms: int):
    """Trading day that ends at 17:00 New York (the forex daily close)."""
    return (datetime.fromtimestamp(ms / 1000, timezone.utc).astimezone(NEW_YORK) + timedelta(hours=7)).date()


def mid(b: dict, k: str) -> float:
    return (b[f"bid_{k}"] + b[f"ask_{k}"]) / 2


class Book:
    """One position per market; stop checked before target inside a bar; long buys the ask, sells the bid.
    R is measured against the initial stop, net of the extra cost."""

    def __init__(self, market: str, strategy: str):
        self.market, self.strategy, self.pos, self.trades = market, strategy, None, []

    def open(self, side: int, price: float, stop: float, target: float, ms: int) -> None:
        if side * (price - stop) <= 0 or side * (target - price) <= 0:
            return
        self.pos = {"side": side, "entry": price, "stop": stop, "initial_stop": stop, "target": target,
                    "ms": ms, "be": None}

    def close(self, price: float, ms: int, how: str) -> None:
        p = self.pos
        risk = abs(p["entry"] - p["initial_stop"])
        cost = extra_cost(self.market, p["entry"]) * p["entry"]
        self.trades.append({"market": self.market, "strategy": self.strategy, "side": p["side"], "entry_ms": p["ms"],
                            "exit_ms": ms, "r": (p["side"] * (price - p["entry"]) - cost) / risk,
                            "r_gross": p["side"] * (price - p["entry"]) / risk, "how": how})
        self.pos = None

    def step(self, b: dict, check_target: bool = True) -> None:
        """Stop, then target, inside one bar."""
        p = self.pos
        if p is None:
            return
        if p["side"] > 0:
            if b["bid_low"] <= p["stop"]:
                self.close(min(p["stop"], b["bid_open"]), b["datetime"], "stop")
            elif check_target and b["bid_high"] >= p["target"]:
                self.close(max(p["target"], b["bid_open"]), b["datetime"], "target")
        else:
            if b["ask_high"] >= p["stop"]:
                self.close(max(p["stop"], b["ask_open"]), b["datetime"], "stop")
            elif check_target and b["ask_low"] <= p["target"]:
                self.close(min(p["target"], b["ask_open"]), b["datetime"], "target")


# ---------- 1. pivot point break ----------

def pivot_break(market: str, bars: list[dict]) -> list[dict]:
    """Floor pivots from the previous forex day. A day that opens below PP arms a buy stop x pips above PP;
    stop x pips below S1, target R2, stop moved to PP once R1 trades (from the next bar); closed at the
    day's last bar. Mirrored for a day that opens above PP. One trade per day."""
    days: dict = {}
    for b in bars:
        days.setdefault(fx_day(b["datetime"]), []).append(b)
    keys, book, x = sorted(days), Book(market, "pivot_break"), offset(market)
    for prev, day in zip(keys, keys[1:]):
        if (day - prev).days > 4:
            continue
        pb = days[prev]
        hi, lo, cl = max(mid(b, "high") for b in pb), min(mid(b, "low") for b in pb), mid(pb[-1], "close")
        pp = (hi + lo + cl) / 3
        r1, s1 = 2 * pp - lo, 2 * pp - hi
        r2, s2 = pp + (hi - lo), pp - (hi - lo)
        today = days[day]
        side = 1 if mid(today[0], "open") < pp else -1
        level, done = pp + side * x, False
        for b in today:
            if book.pos is None and not done:
                hit = b["ask_high"] >= level if side > 0 else b["bid_low"] <= level
                if hit:
                    price = max(level, b["ask_open"]) if side > 0 else min(level, b["bid_open"])
                    book.open(side, price, s1 - x if side > 0 else r1 + x, r2 if side > 0 else s2, b["datetime"])
                    done = True
                    book.step(b, check_target=False)                 # the fill bar: only the stop counts
                    continue
            if book.pos is not None:
                book.step(b)
                p = book.pos
                if p is not None and p["be"] is None and \
                        ((side > 0 and b["bid_high"] >= r1) or (side < 0 and b["ask_low"] <= s1)):
                    p["be"] = True
                    p["stop"] = pp                                   # applies from the next bar
        if book.pos is not None:
            last = today[-1]
            book.close(last["bid_close"] if book.pos["side"] > 0 else last["ask_close"], last["datetime"], "day end")
    return book.trades


# ---------- shared: market entries at the next bar's open ----------

def run_signals(market: str, name: str, bars: list[dict], signal) -> list[dict]:
    """`signal(i)` -> (side, stop) decided at bar i's close; entry at bar i+1's open, target 2:1."""
    book = Book(market, name)
    for i in range(len(bars) - 1):
        book.step(bars[i])
        if book.pos is None:
            s = signal(i)
            if s is not None:
                side, stop = s
                nxt = bars[i + 1]
                price = nxt["ask_open"] if side > 0 else nxt["bid_open"]
                book.open(side, price, stop, price + side * 2 * abs(price - stop), nxt["datetime"])
    return book.trades


def _levels(bars: list[dict]):
    hi = [mid(b, "high") for b in bars]
    lo = [mid(b, "low") for b in bars]
    cl = [mid(b, "close") for b in bars]
    a = atr([{"high": h, "low": low, "close": c} for h, low, c in zip(hi, lo, cl)], 14)
    return hi, lo, cl, a, pivots(hi, True), pivots(lo, False)


class Swings:
    """The swings known at bar i (a swing j is known at j + K), walked forward with i."""

    def __init__(self, points: list[int]):
        self.points, self.k = points, 0

    def known(self, i: int, count: int) -> list[int]:
        while self.k < len(self.points) and self.points[self.k] + K <= i:
            self.k += 1
        return self.points[self.k - count:self.k] if self.k >= count else []


# ---------- 2. support / resistance with the daily trend ----------

def support_resistance(market: str, bars: list[dict]) -> list[dict]:
    """Daily trend: the last completed forex day's close against its 50-day average. In an uptrend buy a bounce
    off the last swing low (low within a quarter ATR of it, close above it) or a close breaking above the last
    swing high; stop a quarter ATR below the last swing low; target 2:1. Mirrored in a downtrend."""
    hi, lo, cl, a, highs, lows = _levels(bars)
    day_of = [fx_day(b["datetime"]) for b in bars]
    closes, order = {}, []
    for d, c in zip(day_of, cl):
        if d not in closes:
            order.append(d)
        closes[d] = c
    avg = dict(zip(order, sma([closes[d] for d in order], 50)))
    prev_day = {d: p for p, d in zip(order, order[1:])}
    sw_high, sw_low = Swings(highs), Swings(lows)

    def signal(i):
        sh, sl = sw_high.known(i, 1), sw_low.known(i, 1)
        if a[i] is None or i == 0 or not sh or not sl:
            return None
        p = prev_day.get(day_of[i])
        if p is None or avg[p] is None:
            return None
        trend = 1 if closes[p] > avg[p] else -1
        support, resistance, near = lo[sl[0]], hi[sh[0]], NEAR * a[i]
        if trend > 0:
            bounce = support - near < lo[i] <= support + near and cl[i] > support
            breakout = cl[i - 1] <= resistance < cl[i]
            return (1, support - near) if bounce or breakout else None
        bounce = resistance - near <= hi[i] < resistance + near and cl[i] < resistance
        breakdown = cl[i - 1] >= support > cl[i]
        return (-1, resistance + near) if bounce or breakdown else None
    return run_signals(market, "support_resistance", bars, signal)


# ---------- 3. trendline bounce ----------

def trendline_bounce(market: str, bars: list[dict]) -> list[dict]:
    """Rising line through the last two known swing lows (newer higher), valid while no close has been below
    it since the newer low: buy when a bar's low comes within a quarter ATR of the line and the bar closes above
    it; stop a quarter ATR below the line; target 2:1. Falling lines through lower swing highs mirrored."""
    hi, lo, cl, a, highs, lows = _levels(bars)
    sw_high, sw_low = Swings(highs), Swings(lows)
    broken: dict = {}

    def line(two, values, i, rising):
        if not two:
            return None
        j1, j2 = two
        if (rising and values[j2] <= values[j1]) or (not rising and values[j2] >= values[j1]):
            return None
        slope = (values[j2] - values[j1]) / (j2 - j1)

        def through(t):
            value = values[j2] + slope * (t - j2)
            return cl[t] < value if rising else cl[t] > value
        key = (rising, j1, j2)
        if key not in broken:                      # check from the newer swing up to now, once per line
            broken[key] = next((t for t in range(j2 + 1, i + 1) if through(t)), None)
        elif broken[key] is None and through(i):
            broken[key] = i
        if broken[key] is not None and broken[key] <= i:
            return None
        return values[j2] + slope * (i - j2)

    def signal(i):
        if a[i] is None:
            return None
        near = NEAR * a[i]
        up = line(sw_low.known(i, 2), lo, i, True)
        if up is not None and up - near < lo[i] <= up + near and cl[i] > up:
            return 1, up - near
        down = line(sw_high.known(i, 2), hi, i, False)
        if down is not None and down - near <= hi[i] < down + near and cl[i] < down:
            return -1, down + near
        return None
    return run_signals(market, "trendline_bounce", bars, signal)


STRATEGIES = {"pivot_break": pivot_break, "support_resistance": support_resistance,
              "trendline_bounce": trendline_bounce}


def evaluate(rows: list[dict]) -> dict:
    if not rows:
        return {"trades": 0}
    r = [t["r"] for t in rows]
    gains, losses = sum(x for x in r if x > 0), -sum(x for x in r if x < 0)
    by_month: dict[str, float] = {}
    for t in rows:
        key = datetime.fromtimestamp(t["entry_ms"] / 1000, timezone.utc).strftime("%Y-%m")
        by_month[key] = by_month.get(key, 0.0) + t["r"]
    vals, rng = list(by_month.values()), random.Random(7)
    p = sum(sum(rng.choice(vals) for _ in vals) > 0 for _ in range(2000)) / 2000
    return {"trades": len(r), "hit_rate": round(sum(x > 0 for x in r) / len(r), 3),
            "mean_r": round(sum(r) / len(r), 4), "mean_r_gross": round(sum(t["r_gross"] for t in rows) / len(r), 4),
            "profit_factor": round(gains / losses, 3) if losses else None, "p_mean_positive": p}


def report(rows: list[dict]) -> dict:
    per = {m: evaluate([t for t in rows if t["market"] == m]) for m in MARKETS}
    out = {"all": evaluate(rows), "first_half": evaluate([t for t in rows if t["entry_ms"] < SPLIT]),
           "second_half": evaluate([t for t in rows if t["entry_ms"] >= SPLIT]), "per_market": per,
           "markets_positive": sum(1 for v in per.values() if v.get("mean_r", 0) > 0)}
    a = out["all"]
    out["passes"] = (a["trades"] >= 300 and a["mean_r"] > 0 and a["p_mean_positive"] >= 0.95
                     and (a["profit_factor"] or 0) >= 1.2 and out["markets_positive"] >= 8
                     and out["first_half"].get("mean_r", 0) > 0 and out["second_half"].get("mean_r", 0) > 0)
    return out


def main() -> None:
    rows: dict[str, list[dict]] = {name: [] for name in STRATEGIES}
    for market in MARKETS:
        bars = h1.load(market)
        for name, fn in STRATEGIES.items():
            rows[name] += fn(market, bars)
    out = {name: report(r) for name, r in rows.items()}
    Path("research_output").mkdir(exist_ok=True)
    Path("research_output/classic_strategies.json").write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
