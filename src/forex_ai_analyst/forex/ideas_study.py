"""Six untested, documented ideas, each with its own fixed rule and gate (docs/IDEAS_STUDY.md).

    python3 -m forex_ai_analyst.forex.ideas_study pfd        pre-FOMC drift, US500 H1
    python3 -m forex_ai_analyst.forex.ideas_study overnight  overnight drift, US500 H1
    python3 -m forex_ai_analyst.forex.ideas_study xsmom      crypto cross-sectional momentum, weekly, long-short
    python3 -m forex_ai_analyst.forex.ideas_study carry      crypto funding carry, weekly, long-short
    python3 -m forex_ai_analyst.forex.ideas_study pairs      BTC/ETH and gold/silver ratio reversion
    python3 -m forex_ai_analyst.forex.ideas_study orb        5-minute opening-range breakout, NAS100 M1
"""
from __future__ import annotations

import json
import math
import random
import statistics
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from forex_ai_analyst.forex import dukascopy

NEW_YORK = ZoneInfo("America/New_York")
HOUR = 3_600_000
DAY = 24 * HOUR
WEEK = 7 * DAY
OUT = Path("research_output")


# ---------- shared ----------

def boot_p(values: list[float], seed: int = 7, runs: int = 4000) -> float:
    """Bootstrap P(mean > 0) over the given values (each value one event, night or week)."""
    if len(values) < 5:
        return 0.0
    rng = random.Random(seed)
    return sum(sum(rng.choice(values) for _ in values) > 0 for _ in range(runs)) / runs


def summary(rows: list[dict], key: str, split_ms: int, periods_per_year: float | None = None) -> dict:
    """Mean, hit rate, PF, P and halves of rows[key]; an annualized Sharpe when rows are periodic returns."""
    x = [r[key] for r in rows]
    if not x:
        return {"n": 0, "mean": 0.0, "p_mean_positive": 0.0, "halves_mean": [None, None], "profit_factor": None}
    gains, losses = sum(v for v in x if v > 0), -sum(v for v in x if v < 0)
    first = [r[key] for r in rows if r["t"] < split_ms]
    second = [r[key] for r in rows if r["t"] >= split_ms]
    out = {"n": len(x), "mean": round(statistics.mean(x), 6), "hit": round(sum(v > 0 for v in x) / len(x), 3),
           "profit_factor": round(gains / losses, 3) if losses else None, "p_mean_positive": boot_p(x),
           "halves_mean": [round(statistics.mean(first), 6) if first else None,
                           round(statistics.mean(second), 6) if second else None]}
    if periods_per_year and len(x) > 1 and statistics.stdev(x) > 0:
        out["sharpe"] = round(statistics.mean(x) / statistics.stdev(x) * math.sqrt(periods_per_year), 3)
    return out


def ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def ny(day: date, hour: int, minute: int = 0) -> int:
    return ms(datetime(day.year, day.month, day.day, hour, minute, tzinfo=NEW_YORK))


def hourly(instrument: str, first=(2013, 1), last=(2026, 9)) -> dict[int, dict]:
    out, (y, m) = {}, first
    while (y, m) <= last:
        for b in dukascopy.hours(instrument, y, m):
            if b["bid_high"] > b["bid_low"]:
                out[b["datetime"]] = b
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def weekdays(first: date, last: date):
    d = first
    while d <= last:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def next_weekday(d: date) -> date:
    d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def prev_weekday(d: date) -> date:
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def write(name: str, report: dict) -> None:
    OUT.mkdir(exist_ok=True)
    (OUT / f"ideas_{name}.json").write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(name, json.dumps(report, indent=1, default=str))


SPLIT_2020 = ms(datetime(2020, 1, 1, tzinfo=timezone.utc))
SPLIT_2024 = ms(datetime(2024, 1, 1, tzinfo=timezone.utc))
INDEX_EXTRA, NIGHT_SWAP = 0.0002, 0.0002          # round trip on top of the spread; per night held


# ---------- 1. pre-FOMC drift (Lucca and Moench 2015) ----------

UNSCHEDULED = {"20191011", "20200303", "20200315", "20200323", "20200331", "20200827"}


def fomc_days() -> list[date]:
    """Scheduled FOMC statement days from 2013: Tuesday-Thursday, minus the known unscheduled releases."""
    from forex_ai_analyst.forex.fomc_study import statement_urls
    out = []
    for url in statement_urls():
        d = url[-13:-5]
        day = date(int(d[:4]), int(d[4:6]), int(d[6:]))
        if day.year >= 2013 and day.weekday() in (1, 2, 3) and d not in UNSCHEDULED:
            out.append(day)
    return out


def window_return(bars: dict[int, dict], start_ms: int, end_open_ms: int) -> float | None:
    """Buy the ask at the open of the bar starting at start_ms; sell the bid at the close of the bar that
    opens one hour before end_open_ms (just before a 14:00 announcement). One night of swap."""
    a, b = bars.get(start_ms), bars.get(end_open_ms - HOUR)
    if a is None or b is None:
        return None
    return b["bid_close"] / a["ask_open"] - 1 - INDEX_EXTRA - NIGHT_SWAP


def pfd() -> dict:
    bars = hourly("USA500IDXUSD")
    days = fomc_days()
    events = []
    for day in days:
        r = window_return(bars, ny(prev_weekday(day), 14), ny(day, 14))
        if r is not None:
            events.append({"t": ny(day, 14), "day": day.isoformat(), "r": r})
    fomc = set(days)
    base = [r for d in weekdays(date(2013, 2, 1), date(2026, 9, 30))
            if d.weekday() in (1, 2, 3) and d not in fomc
            and (r := window_return(bars, ny(prev_weekday(d), 14), ny(d, 14))) is not None]
    s = summary(events, "r", SPLIT_2020)
    s["baseline_mean_other_days"] = round(statistics.mean(base), 6) if base else None
    s["passes"] = (s["n"] >= 80 and s["mean"] > 0 and s["p_mean_positive"] >= 0.95
                   and all((h or 0) > 0 for h in s["halves_mean"]))
    return s


# ---------- 4. overnight drift ----------

def overnight() -> dict:
    """Buy at the cash close (the close of the 15:00 New York bar), sell at the open of the first bar from
    09:00 New York on the next weekday; swap for every calendar night held. Intraday shown for comparison."""
    bars = hourly("USA500IDXUSD")
    nights, days = [], []
    for d in weekdays(date(2013, 1, 2), date(2026, 9, 29)):
        close_bar, nxt = bars.get(ny(d, 15)), next_weekday(d)
        open_bar = next((bars[k] for h in range(9, 12) if (k := ny(nxt, h)) in bars), None)
        if close_bar and open_bar:
            held = (nxt - d).days
            gross = open_bar["bid_open"] / close_bar["ask_close"] - 1
            nights.append({"t": ny(d, 15), "gross": gross, "r1": gross - INDEX_EXTRA - 0.0001 * held,
                           "r2": gross - INDEX_EXTRA - NIGHT_SWAP * held, "r4": gross - INDEX_EXTRA - 0.0004 * held})
        o, c = bars.get(ny(d, 10)), bars.get(ny(d, 15))
        if o and c:
            days.append({"t": ny(d, 10), "gross": c["bid_close"] / o["ask_open"] - 1})
    report = {name: summary(nights, key, SPLIT_2020, 252) for name, key in
              (("gross", "gross"), ("swap_1bp", "r1"), ("swap_2bp", "r2"), ("swap_4bp", "r4"))}
    report["intraday_gross"] = summary(days, "gross", SPLIT_2020, 252)
    g = report["swap_2bp"]
    report["passes"] = (g["n"] >= 1000 and g.get("sharpe", 0) >= 0.5 and g["p_mean_positive"] >= 0.95
                        and all((h or 0) > 0 for h in g["halves_mean"]))
    return report


# ---------- 2 and 3. crypto cross-sectional momentum and funding carry ----------

CRYPTO_FEE = 0.0007          # per side: taker 0.05% + slippage 0.02%


def crypto_universe() -> dict[str, tuple[dict[int, float], list[tuple[int, float]]]]:
    """Hourly opens and funding for the bot's 62 rebound markets (Binance USD-M archives)."""
    from forex_ai_analyst.lab import combination_study as cs
    from forex_ai_analyst.lab import data
    from forex_ai_analyst.lab.engine2_study import LIVE_MARKETS, NEW_MARKETS
    from forex_ai_analyst.lab.rebound_expansion import CANDIDATES, first_full_month
    out = {}
    for pair in LIVE_MARKETS + NEW_MARKETS + tuple(f"{c}-USDT" for c in CANDIDATES):
        start = cs.START if pair in LIVE_MARKETS + NEW_MARKETS else first_full_month(pair)
        bars = data.load_klines(pair, start, cs.END, "1h")
        out[pair] = ({b["datetime"]: b["open"] for b in bars}, data.load_funding(pair, start, cs.END))
    return out


def funding_between(events: list[tuple[int, float]], a: int, b: int) -> float:
    return sum(rate for t, rate in events if a < t <= b)


def weekly_long_short(universe, score) -> list[dict]:
    """Every Monday 00:00 UTC: rank coins by score(pair, t) (higher = long); long the top fifth and short the
    bottom fifth, equal weight, for one week. Net of fees (full turnover assumed) and of funding paid."""
    first = ms(datetime(2021, 2, 1, tzinfo=timezone.utc))
    first += (7 - datetime.fromtimestamp(first / 1000, timezone.utc).weekday()) % 7 * DAY
    end = ms(datetime(2026, 8, 31, tzinfo=timezone.utc))
    rows, t = [], first
    while t + WEEK <= end:
        ranked = []
        for pair, (opens, _) in universe.items():
            if t in opens and t + WEEK in opens and t - 35 * DAY in opens:
                s = score(pair, t)
                if s is not None:
                    ranked.append((s, pair))
        if len(ranked) >= 15:
            ranked.sort()
            k = len(ranked) // 5

            def leg(chosen, side):
                rets = []
                for _, p in chosen:
                    opens, funding = universe[p]
                    price = opens[t + WEEK] / opens[t] - 1
                    rets.append(side * price - side * funding_between(funding, t, t + WEEK) - 2 * CRYPTO_FEE)
                return statistics.mean(rets)
            long_, short_ = leg(ranked[-k:], 1), leg(ranked[:k], -1)
            everyone = statistics.mean(universe[p][0][t + WEEK] / universe[p][0][t] - 1 for _, p in ranked)
            rows.append({"t": t, "ls": (long_ + short_) / 2, "long": long_, "long_minus_all": long_ - everyone})
        t += WEEK
    return rows


def weekly_report(rows: list[dict]) -> dict:
    report = {k: summary(rows, k, SPLIT_2024, 52) for k in ("ls", "long", "long_minus_all")}
    g = report["ls"]
    report["passes"] = (g["n"] >= 100 and g.get("sharpe", 0) >= 0.5 and g["p_mean_positive"] >= 0.95
                        and all((h or 0) > 0 for h in g["halves_mean"]))
    return report


def xsmom() -> dict:
    u = crypto_universe()
    return weekly_report(weekly_long_short(
        u, lambda p, t: u[p][0][t] / u[p][0][t - 28 * DAY] - 1 if t - 28 * DAY in u[p][0] else None))


def carry() -> dict:
    u = crypto_universe()
    # long the coins whose funding was lowest last week (crowded shorts pay), short the highest
    return weekly_report(weekly_long_short(u, lambda p, t: -funding_between(u[p][1], t - WEEK, t)))


# ---------- 6. ratio reversion pairs ----------

def ratio_trades(times: list[int], a: list[float], b: list[float], lookback: int, max_hold: int,
                 cost: float, night_cost: float, bars_per_day: float) -> list[dict]:
    """z of ln(a/b) against the previous `lookback` bars: z > 2 sells a and buys b (z < -2 the reverse); out
    when z crosses 0, at |z| > 4, or after `max_hold` bars. Return per unit of notional, net of costs."""
    ratio = [math.log(x / y) for x, y in zip(a, b)]
    rows, pos = [], None
    for i in range(lookback, len(ratio)):
        w = ratio[i - lookback:i]
        mu, sd = statistics.mean(w), statistics.pstdev(w)
        if not sd:
            continue
        z = (ratio[i] - mu) / sd
        if pos is not None:
            held = i - pos["i"]
            if pos["side"] * z >= 0 or abs(z) > 4 or held >= max_hold:
                side = pos["side"]                         # +1: long a, short b
                gross = side * ((a[i] / a[pos["i"]] - 1) - (b[i] / b[pos["i"]] - 1)) / 2
                rows.append({"t": times[pos["i"]], "r": gross - cost - night_cost * held / bars_per_day})
                pos = None
            continue
        if z > 2:
            pos = {"side": -1, "i": i}
        elif z < -2:
            pos = {"side": 1, "i": i}
    return rows


def pairs() -> dict:
    from forex_ai_analyst.lab import combination_study as cs
    from forex_ai_analyst.lab import data
    eth = data.resample(data.load_klines("ETH-USDT", cs.START, cs.END, "1h"), 4)
    btc = {b["datetime"]: b["close"] for b in data.resample(data.load_klines("BTC-USDT", cs.START, cs.END, "1h"), 4)}
    common = [b for b in eth if b["datetime"] in btc]
    crypto = ratio_trades([b["datetime"] for b in common], [b["close"] for b in common],
                          [btc[b["datetime"]] for b in common], 180, 60, 2 * CRYPTO_FEE, 0.0, 6)
    dukascopy.POINT.update({"XAGUSD": 1e-3})
    gold, silver = hourly("XAUUSD", (2010, 1)), hourly("XAGUSD", (2010, 1))
    daily: dict[str, tuple[int, float, float]] = {}
    for t in sorted(set(gold) & set(silver)):
        d = datetime.fromtimestamp(t / 1000, timezone.utc).date().isoformat()
        daily[d] = (t, (gold[t]["bid_close"] + gold[t]["ask_close"]) / 2,
                    (silver[t]["bid_close"] + silver[t]["ask_close"]) / 2)
    keys = sorted(daily)
    metals = ratio_trades([daily[k][0] for k in keys], [daily[k][1] for k in keys], [daily[k][2] for k in keys],
                          60, 20, 0.0007, 2 * NIGHT_SWAP, 1)
    report = {"btc_eth_4h": summary(crypto, "r", SPLIT_2024), "gold_silver_d1": summary(metals, "r", SPLIT_2020)}
    for k in ("btc_eth_4h", "gold_silver_d1"):
        g = report[k]
        g["passes"] = (g["n"] >= 50 and g["mean"] > 0 and g["p_mean_positive"] >= 0.95
                       and (g["profit_factor"] or 0) >= 1.2 and all((h or 0) > 0 for h in g["halves_mean"]))
    return report


# ---------- 5. opening-range breakout (Zarattini and Aziz 2023) ----------

ORB_FIRST, ORB_LAST = date(2024, 10, 1), date(2026, 9, 30)


def orb_day(minutes: list[dict], day: date) -> dict | None:
    """First 5-minute candle of the cash session (09:30-09:35 New York): an up candle buys at 09:35 (ask) with
    the stop at its low; a down candle sells (bid) with the stop at its high; target 10R; out at 16:00.
    Result in R, net of the spread and 0.01%."""
    by = {b["datetime"]: b for b in minutes}
    t0 = ny(day, 9, 30)
    first = [by.get(t0 + k * 60_000) for k in range(5)]
    start = by.get(t0 + 5 * 60_000)
    if any(b is None for b in first) or start is None:
        return None
    o = (first[0]["bid_open"] + first[0]["ask_open"]) / 2
    c = (first[-1]["bid_close"] + first[-1]["ask_close"]) / 2
    hi = max((b["bid_high"] + b["ask_high"]) / 2 for b in first)
    lo = min((b["bid_low"] + b["ask_low"]) / 2 for b in first)
    if c == o:
        return None
    side = 1 if c > o else -1
    entry = start["ask_open"] if side > 0 else start["bid_open"]
    stop = lo if side > 0 else hi
    risk = abs(entry - stop)
    if risk <= 0 or side * (entry - stop) <= 0:
        return None
    target = entry + side * 10 * risk
    exit_, last = None, start
    for t in range(t0 + 5 * 60_000, ny(day, 16), 60_000):
        b = by.get(t)
        if b is None:
            continue
        last = b
        if side > 0:
            if b["bid_low"] <= stop:
                exit_ = min(stop, b["bid_open"])
            elif b["bid_high"] >= target:
                exit_ = target
        else:
            if b["ask_high"] >= stop:
                exit_ = max(stop, b["ask_open"])
            elif b["ask_low"] <= target:
                exit_ = target
        if exit_ is not None:
            break
    if exit_ is None:
        exit_ = last["bid_close"] if side > 0 else last["ask_close"]
    return {"t": t0, "r": (side * (exit_ - entry) - 0.0001 * entry) / risk}


def orb() -> dict:
    rows = []
    for day in weekdays(ORB_FIRST, ORB_LAST):
        m = dukascopy.minutes("USATECHIDXUSD", day)
        if m and (r := orb_day(m, day)) is not None:
            rows.append(r)
    g = summary(rows, "r", ms(datetime(2025, 10, 1, tzinfo=timezone.utc)))
    g["passes"] = (g["n"] >= 300 and g["mean"] > 0 and g["p_mean_positive"] >= 0.95
                   and (g["profit_factor"] or 0) >= 1.15 and all((h or 0) > 0 for h in g["halves_mean"]))
    return g


STUDIES = {"pfd": pfd, "overnight": overnight, "xsmom": xsmom, "carry": carry, "pairs": pairs, "orb": orb}


def main() -> None:
    for name in sys.argv[1:] or list(STUDIES):
        write(name, STUDIES[name]())


if __name__ == "__main__":
    main()
