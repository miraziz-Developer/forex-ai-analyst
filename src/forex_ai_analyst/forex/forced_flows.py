"""Forced-flow event studies in FX (docs/FORCED_FLOWS_STUDY.md).

    python3 -m forex_ai_analyst.forex.forced_flows

Each test isolates moments when some participant must trade regardless of price
and measures what the currency does next, net of costs, with entries placed
strictly after the information is known.
"""
from __future__ import annotations

import calendar
import json
import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from forex_ai_analyst.forex.data import MARKETS, Market, load_yahoo

HOUR = 3_600_000


def _utc(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, timezone.utc)


def bootstrap_p(values: list[float], runs: int = 5000, seed: int = 11) -> float:
    if len(values) < 5:
        return 0.0
    rng, positive = random.Random(seed), 0
    for _ in range(runs):
        positive += sum(rng.choice(values) for _ in values) > 0
    return positive / runs


def verdict(net: list[float], min_events: int, extra: dict | None = None) -> dict:
    half = len(net) // 2
    mean = sum(net) / len(net) if net else 0.0
    p = bootstrap_p(net)
    halves = (sum(net[:half]) / half if half else 0.0, sum(net[half:]) / (len(net) - half) if net else 0.0)
    reasons = []
    if len(net) < min_events:
        reasons.append(f"{len(net)} events < {min_events}")
    if p < 0.90:
        reasons.append(f"P(mean net > 0) {p:.2f} < 0.90")
    if min(halves) <= 0:
        reasons.append("not positive in both halves")
    for key, ok in (extra or {}).items():
        if not ok:
            reasons.append(key)
    return {"events": len(net), "mean_net_pct": round(mean * 100, 4), "win_rate": round(sum(v > 0 for v in net) / len(net), 3)
            if net else None, "p_mean_positive": round(p, 3),
            "halves_pct": [round(h * 100, 4) for h in halves], "passes": not reasons, "reasons": reasons}


# ---------- 1. Gotobi: Japanese importers buy USD into the Tokyo 9:55 fix ----------

def gotobi_dates(start: date, end: date) -> set[date]:
    """5th, 10th, 15th, 20th, 25th and last day of each month; weekend dates move to the Friday before."""
    out, d = set(), date(start.year, start.month, 1)
    while d <= end:
        last = calendar.monthrange(d.year, d.month)[1]
        for day in (5, 10, 15, 20, 25, last):
            g = date(d.year, d.month, day)
            while g.weekday() >= 5:
                g -= timedelta(days=1)
            out.add(g)
        d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return out


def gotobi_study(cost: float = 0.014 / 150) -> dict:
    """Long USDJPY from 23:00 UTC (08:00 JST) to 01:00 UTC (10:00 JST, just after the 09:55 fix)."""
    bars = {b["datetime"]: b for b in load_yahoo(MARKETS["USDJPY"], "1h")}
    days = sorted({_utc(ms).date() for ms in bars})
    gotobi = gotobi_dates(days[0], days[-1])
    events, control = [], []
    for d in days:
        if d.weekday() >= 5:
            continue
        start = int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000) - HOUR   # 23:00 prev day
        first, last = bars.get(start), bars.get(start + HOUR)          # the 23:00 and 00:00 UTC bars
        if not first or not last:
            continue
        ret = last["close"] / first["open"] - 1
        (events if d in gotobi else control).append(ret)
    net = [r - cost for r in events]
    control_mean = sum(control) / len(control)
    result = verdict(net, 100, {"gotobi mean not above other days": sum(events) / len(events) > control_mean})
    result["control_mean_pct"] = round(control_mean * 100, 4)
    result["gross_mean_pct"] = round(sum(events) / len(events) * 100, 4)
    return result


# ---------- 2. Month-end hedge rebalancing driven by relative equity performance ----------

def _closes(market: Market) -> dict[date, float]:
    return {_utc(b["datetime"]).date(): b["close"] for b in load_yahoo(market, "1d")}


def _close_on_or_before(series: dict[date, float], day: date) -> float | None:
    known = [d for d in series if d <= day]
    return series[max(known)] if known else None


PAIRS = {"EUR": ("EURUSD", False), "GBP": ("GBPUSD", False), "AUD": ("AUDUSD", False), "NZD": ("NZDUSD", False),
         "JPY": ("USDJPY", True), "CAD": ("USDCAD", True), "CHF": ("USDCHF", True)}


def month_end_study(cost: float = 0.00015) -> dict:
    """If US stocks beat foreign stocks this month (through 3 trading days before month end), foreign
    holders' USD hedges must grow: sell USD from that close to the last close of the month."""
    us = _closes(Market("US500", "^GSPC", 0, pct=True))
    foreign = [_closes(Market(n, y, 0, pct=True)) for n, y in (("DE40", "^GDAXI"), ("UK100", "^FTSE"), ("JP225", "^N225"))]
    fx = {c: _closes(MARKETS[p]) for c, (p, _) in PAIRS.items()}
    days = sorted(set.intersection(*(set(s) for s in fx.values())))
    by_month: dict[str, list[date]] = {}
    for d in days:
        by_month.setdefault(d.strftime("%Y-%m"), []).append(d)
    months = sorted(by_month)
    net, signs = [], []
    for prev, month in zip(months, months[1:]):
        trading = by_month[month]
        if len(trading) < 15:
            continue
        base, signal_day, last = by_month[prev][-1], trading[-4], trading[-1]
        values = [_close_on_or_before(s, d) for s in [us] + foreign for d in (base, signal_day)]
        if None in values:
            continue
        r_us = values[1] / values[0] - 1
        r_foreign = sum(values[2 * k + 1] / values[2 * k] - 1 for k in range(1, 4)) / 3
        sign = 1 if r_us > r_foreign else -1          # +1: sell USD (buy the other currencies)
        basket = 0.0
        for c, (_, inverted) in PAIRS.items():
            change = fx[c][last] / fx[c][signal_day] - 1
            basket += (-change if inverted else change) / len(PAIRS)   # other currency vs USD
        net.append(sign * basket - cost)
        signs.append(sign)
    result = verdict(net, 150)
    result["share_sell_usd_signals"] = round(sum(s > 0 for s in signs) / len(signs), 3)
    return result


# ---------- 3. Risk-off: forced carry unwinds after a VIX spike ----------

def risk_off_study(cost: float = 0.00028, jump: float = 0.20, hold: int = 5) -> dict:
    """VIX up >= 20% on day t: short AUDJPY from the next FX day's open for `hold` FX days."""
    vix = _closes(Market("VIX", "^VIX", 0, pct=True))
    bars = load_yahoo(Market("AUDJPY", "AUDJPY=X", 0, pct=True), "1d")
    fx_days = [_utc(b["datetime"]).date() for b in bars]
    vix_days = sorted(vix)
    net, busy_until = [], date.min
    for prev, day in zip(vix_days, vix_days[1:]):
        if vix[day] / vix[prev] - 1 < jump or day < busy_until:
            continue
        later = [i for i, d in enumerate(fx_days) if d > day]
        if len(later) < hold:
            continue
        entry, exit_ = bars[later[0]], bars[later[0] + hold - 1]
        net.append(-(exit_["close"] / entry["open"] - 1) - cost)
        busy_until = fx_days[later[0] + hold - 1]
    return verdict(net, 40)


def main() -> None:
    report = {"gotobi_usdjpy": gotobi_study(), "month_end_usd": month_end_study(), "risk_off_audjpy": risk_off_study()}
    out = Path("research_output")
    out.mkdir(exist_ok=True)
    (out / "forced_flows_study.json").write_text(json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(),
                                                             "report": report}, indent=2) + "\n")
    for name, r in report.items():
        print(name, json.dumps(r))


if __name__ == "__main__":
    main()
