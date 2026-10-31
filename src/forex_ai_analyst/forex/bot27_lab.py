"""The owner's BOT 27 v3 "Quantum Scalper" rules replayed on Dukascopy M1 bid/ask (docs/BOT27_LAB.md).

    python3 -m forex_ai_analyst.forex.bot27_lab

Entry (on each closed M5 bar, 06-20 UTC, no Friday entries from 18:00): H1 close vs EMA50/EMA200 and M15
EMA20/50 agree; M5 EMA20/50/200 structure; a pullback to EMA20 within 5 bars without breaking EMA50; RSI(5)
beyond 32/68 in that window and turning; an engulfing or pin-bar trigger; not stretched > 0.85 ATR from EMA20.
Stop 0.25 ATR beyond the 5-bar swing (0.8-2.2 ATR); spread <= 0.18 ATR; 2.2R target; at 0.7R the stop goes to
+0.08R, at 1.0R 60% is closed, from 1.15R the rest trails EMA20 -/+ 0.15 ATR with 0.45R locked; Friday 20:00
close unless locked; 3-bar cooldown; per-setup veto after 25 trades below -0.15R. Results in R of the full
position, net of the real spread plus `extra` pips per round trip.
"""
from __future__ import annotations

import json
import math
import random
import statistics
from bisect import bisect_right
from datetime import date, datetime, timezone
from pathlib import Path

from forex_ai_analyst.forex.scalp_3ema_study import minutes

PAIRS = ("EURUSD", "GBPUSD")
FIRST, LAST = date(2024, 10, 1), date(2026, 9, 30)
PIP = 0.0001
M5, M15, H1 = 300_000, 900_000, 3_600_000
EXTRAS = (0.0, 0.5, 1.0)


def bid_bars(m1: list[dict], width: int) -> list[dict]:
    """Bid OHLC bars (MT5 charts are bid) with their close time."""
    groups: dict[int, list[dict]] = {}
    for b in m1:
        groups.setdefault(b["datetime"] // width * width, []).append(b)
    return [{"end": s + width, "o": g[0]["bid_open"], "h": max(x["bid_high"] for x in g),
             "l": min(x["bid_low"] for x in g), "c": g[-1]["bid_close"]} for s, g in sorted(groups.items())]


def ema(x: list[float], p: int) -> list[float]:
    k, out = 2.0 / (p + 1), [x[0]]
    for v in x[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def rma(x: list[float], p: int) -> list[float]:
    out = [math.nan] * len(x)
    if len(x) >= p:
        out[p - 1] = sum(x[:p]) / p
        for i in range(p, len(x)):
            out[i] = (out[i - 1] * (p - 1) + x[i]) / p
    return out


def atr(bars: list[dict]) -> list[float]:
    tr = [bars[0]["h"] - bars[0]["l"]] + [max(b["h"] - b["l"], abs(b["h"] - a["c"]), abs(b["l"] - a["c"]))
                                         for a, b in zip(bars, bars[1:])]
    return rma(tr, 14)


def rsi5(c: list[float]) -> list[float]:
    d = [0.0] + [b - a for a, b in zip(c, c[1:])]
    g, lo = rma([max(x, 0.0) for x in d], 5), rma([max(-x, 0.0) for x in d], 5)
    return [100.0 if lw == 0 else (math.nan if math.isnan(lw) else 100 - 100 / (1 + gn / lw)) for gn, lw in zip(g, lo)]


class Context:
    """M5/M15/H1 bid bars and the bot's indicators. EMAs run over the whole history (the bot recomputes
    them over its last 220-250 bars, which differs slightly for EMA200)."""

    def __init__(self, m1: list[dict]):
        self.m5, self.m15, self.h1 = bid_bars(m1, M5), bid_bars(m1, M15), bid_bars(m1, H1)
        c5 = [b["c"] for b in self.m5]
        self.e20, self.e50, self.e200 = ema(c5, 20), ema(c5, 50), ema(c5, 200)
        self.rsi, self.atr = rsi5(c5), atr(self.m5)
        c15, c1h = [b["c"] for b in self.m15], [b["c"] for b in self.h1]
        self.m15_e20, self.m15_e50 = ema(c15, 20), ema(c15, 50)
        self.h1_e50, self.h1_e200 = ema(c1h, 50), ema(c1h, 200)
        self.m15_end, self.h1_end = [b["end"] for b in self.m15], [b["end"] for b in self.h1]

    def trend(self, t: int) -> int:
        i, j = bisect_right(self.h1_end, t) - 1, bisect_right(self.m15_end, t) - 1
        if i < 220 or j < 220:
            return 0
        c, e50, e200 = self.h1[i]["c"], self.h1_e50, self.h1_e200
        h1_up = c > e50[i] and (e50[i] > e50[i - 3] or e50[i] > e200[i])
        h1_dn = c < e50[i] and (e50[i] < e50[i - 3] or e50[i] < e200[i])
        m, m20, m50 = self.m15[j]["c"], self.m15_e20[j], self.m15_e50[j]
        return 1 if h1_up and m20 > m50 and m > m50 else -1 if h1_dn and m20 < m50 and m < m50 else 0

    def signal(self, k: int) -> dict | None:
        """The bot's analyze() on closed M5 bar k."""
        if k < 250:
            return None
        b, a = self.m5, self.atr[k]
        if not a > 0 or b[k]["h"] - b[k]["l"] > 2.8 * a:
            return None
        side = self.trend(b[k]["end"])
        if not side:
            return None
        win = range(k - 5, k + 1)
        o, h, l, c = b[k]["o"], b[k]["h"], b[k]["l"], b[k]["c"]
        rng = max(h - l, 1e-12)
        e20, e50, e200, r = self.e20, self.e50, self.e200, self.rsi
        if side > 0:
            if not (e20[k] > e50[k] and c > e200[k]):
                return None
            if not any(b[x]["l"] <= e20[x] + 0.15 * a for x in win) or any(b[x]["c"] < e50[x] - 0.25 * a for x in win):
                return None
            if min(r[x] for x in win) > 32.0 or r[k] <= r[k - 1]:
                return None
            body = c > o and c - o >= 0.15 * a and c > b[k - 1]["c"]
            pin = (min(o, c) - l) / rng >= 0.45 and (c - l) / rng >= 0.65
            if not (body or pin) or c < e20[k] - 0.10 * a or c - e20[k] > 0.85 * a:
                return None
            stop = min(b[x]["l"] for x in win) - 0.25 * a
        else:
            if not (e20[k] < e50[k] and c < e200[k]):
                return None
            if not any(b[x]["h"] >= e20[x] - 0.15 * a for x in win) or any(b[x]["c"] > e50[x] + 0.25 * a for x in win):
                return None
            if max(r[x] for x in win) < 68.0 or r[k] >= r[k - 1]:
                return None
            body = o > c and o - c >= 0.15 * a and c < b[k - 1]["c"]
            pin = (h - max(o, c)) / rng >= 0.45 and (h - c) / rng >= 0.65
            if not (body or pin) or c > e20[k] + 0.10 * a or e20[k] - c > 0.85 * a:
                return None
            stop = max(b[x]["h"] for x in win) + 0.25 * a
        state = ("BUY" if side > 0 else "SELL") + ("|M5_ENGULF" if body else "|M5_PINBAR")
        return {"side": side, "stop": stop, "atr": a, "state": state}


def entries_allowed(t: int) -> bool:
    d = datetime.fromtimestamp(t / 1000, timezone.utc)
    if d.weekday() >= 5 or (d.weekday() == 4 and d.hour >= 18):
        return False
    return 6 <= d.hour < 20


def manage(pos: dict, b: dict) -> float | None:
    """One minute of position management; the full-position R on exit, else None. Stop before target."""
    s, e, d = pos["side"], pos["entry"], pos["dist"]
    lo, hi, op, cl = ((b["bid_low"], b["bid_high"], b["bid_open"], b["bid_close"]) if s > 0 else
                      (b["ask_high"], b["ask_low"], b["ask_open"], b["ask_close"]))   # adverse, favourable
    exit_ = None
    if s * (lo - pos["sl"]) <= 0:
        exit_ = pos["sl"] if s * (op - pos["sl"]) > 0 else op
    elif s * (hi - pos["tp"]) >= 0:
        exit_ = pos["tp"]
    else:
        t = datetime.fromtimestamp(b["datetime"] / 1000, timezone.utc)
        if t.weekday() == 4 and t.hour >= 20 and s * (pos["sl"] - e) <= 0:
            exit_ = cl                                           # Friday close unless profit is locked
    if exit_ is not None:
        rest = s * (exit_ - e) / d
        return 0.6 * 1.0 + 0.4 * rest if pos["partial"] else rest
    best = s * (hi - e) / d
    if best >= 0.70 and s * (pos["sl"] - e) < 0:
        pos["sl"] = e + s * 0.08 * d                             # applies from the next minute
    if not pos["partial"] and best >= 1.0:
        pos["partial"] = True                                    # 60% closed at +1.0R
    return None


def simulate(pair: str, m1: list[dict]) -> list[dict]:
    """One position on the pair at a time. Each minute: first the M5 bars that closed by its open (trail an open
    position, or look for an entry at this minute's open), then manage the position inside the minute."""
    ctx = Context(m1)
    stats: dict[str, list[float]] = {}
    trades, pos, cooldown_until, k5 = [], None, 0, 0
    for b in m1:
        while k5 < len(ctx.m5) and ctx.m5[k5]["end"] <= b["datetime"]:
            k, k5 = k5, k5 + 1
            if pos is not None:
                s, e, d = pos["side"], pos["entry"], pos["dist"]
                if s * (ctx.m5[k]["c"] - e) / d >= 1.15:
                    lock, trail = e + s * 0.45 * d, ctx.e20[k] - s * 0.15 * ctx.atr[k]
                    cand = max(lock, trail) if s > 0 else min(lock, trail)
                    if s * (cand - pos["sl"]) > 0:
                        pos["sl"] = cand
                continue
            end = ctx.m5[k]["end"]
            if b["datetime"] - end > 10 * 60_000 or end < cooldown_until or not entries_allowed(end):
                continue                                         # stale (data gap), cooldown or out of hours
            sig = ctx.signal(k)
            if sig is None:
                continue
            past = stats.get(sig["state"], [])
            if len(past) >= 25 and sum(past) / len(past) < -0.15:
                continue
            s, spread = sig["side"], b["ask_open"] - b["bid_open"]
            if spread > 0.18 * sig["atr"]:
                continue
            entry = b["ask_open"] if s > 0 else b["bid_open"]
            dist = s * (entry - sig["stop"])
            if dist <= 0:
                continue
            dist = max(dist, 0.8 * sig["atr"], 2 * spread)
            if dist > 2.2 * sig["atr"]:
                continue
            pos = {"side": s, "entry": entry, "dist": dist, "sl": entry - s * dist, "tp": entry + s * 2.2 * dist,
                   "t": b["datetime"], "state": sig["state"], "partial": False}
            cooldown_until = end + 3 * M5
        if pos is not None and (r := manage(pos, b)) is not None:
            trades.append({"pair": pair, "side": pos["side"], "t": pos["t"], "r": r, "dist_pips": pos["dist"] / PIP,
                           "state": pos["state"]})
            stats.setdefault(pos["state"], []).append(r)
            pos = None
    return trades


def report(rows: list[dict], extra: float) -> dict:
    r = [t["r"] - extra / t["dist_pips"] for t in rows]
    if not r:
        return {"trades": 0}
    gains, losses = sum(x for x in r if x > 0), -sum(x for x in r if x < 0)
    rng = random.Random(7)
    p = sum(statistics.mean(rng.choice(r) for _ in r) > 0 for _ in range(2000)) / 2000

    def part(keep):
        x = [v for v, t in zip(r, rows) if keep(t)]
        return {"n": len(x), "mean_r": round(statistics.mean(x), 3) if x else None}
    year = lambda t: datetime.fromtimestamp(t["t"] / 1000, timezone.utc).year  # noqa: E731
    return {"trades": len(r), "win_rate": round(sum(x > 0 for x in r) / len(r), 3),
            "mean_r": round(statistics.mean(r), 3), "total_r": round(sum(r), 1),
            "profit_factor": round(gains / losses, 3) if losses else None, "p_mean_positive": p,
            "per_month": round(len(r) / 24, 1), "median_stop_pips": round(statistics.median(t["dist_pips"] for t in rows), 1),
            "buy": part(lambda t: t["side"] > 0), "sell": part(lambda t: t["side"] < 0),
            "per_pair": {pp: part(lambda t, pp=pp: t["pair"] == pp) for pp in PAIRS},
            "per_year": {y: part(lambda t, y=y: year(t) == y) for y in sorted({year(t) for t in rows})}}


def main() -> None:
    rows = [t for pair in PAIRS for t in simulate(pair, minutes(pair, FIRST, LAST))]
    out = {f"extra_{x}_pip": report(rows, x) for x in EXTRAS}
    Path("research_output").mkdir(exist_ok=True)
    Path("research_output/bot27_lab.json").write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
