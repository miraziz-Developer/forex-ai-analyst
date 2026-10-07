"""Three moving averages (SMA 9/14/21) on H4, as in the owner's chart, traded in real time (docs/THREE_MA_STUDY.md).

    python3 -m forex_ai_analyst.forex.three_ma_study
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from pathlib import Path

from forex_ai_analyst.forex import dukascopy
from forex_ai_analyst.forex.index_study import sma

dukascopy.POINT.update({"XAGUSD": 1e-3})
MARKETS = ("EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "NZDUSD", "USDCAD", "EURJPY", "GBPJPY", "EURGBP",
           "XAUUSD", "XAGUSD")
FIRST_YEAR, LAST = 2010, (2026, 9)
H4 = 4 * 3_600_000
EXTRA = {"XAUUSD": 0.0002, "XAGUSD": 0.0005}       # on top of the real spread; FX pays 0.7 pip


def extra_cost(market: str, price: float) -> float:
    if market in EXTRA:
        return EXTRA[market]
    return 0.7 * (0.01 if market.endswith("JPY") else 0.0001) / price


def h4_bars(market: str) -> list[dict]:
    """Complete H4 bid/ask bars (four trading hours each) aligned to 00/04/08.. UTC; flat weekend and
    holiday hours (no range) are dropped."""
    hours = []
    year, month = FIRST_YEAR, 1
    while (year, month) <= LAST:
        hours += [h for h in dukascopy.hours(market, year, month) if h["bid_high"] > h["bid_low"]]
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    groups: dict[int, list[dict]] = {}
    for h in hours:
        groups.setdefault(h["datetime"] // H4 * H4, []).append(h)
    out = []
    for start in sorted(groups):
        g = groups[start]
        if len(g) != 4:
            continue
        out.append({"datetime": start, "bid_open": g[0]["bid_open"], "ask_open": g[0]["ask_open"],
                    "close": (g[-1]["bid_close"] + g[-1]["ask_close"]) / 2})
    return out


def trades(market: str, bars: list[dict]) -> list[dict]:
    """SMA 9 > 14 > 21 newly in place at a bar's close buys at the next bar's open (ask); the long ends at the
    next open after SMA 9 closes below SMA 14 (bid). Sells mirrored. Always measured from real-time values."""
    closes = [b["close"] for b in bars]
    f, m, s = sma(closes, 9), sma(closes, 14), sma(closes, 21)
    out, pos = [], None
    for i in range(1, len(bars) - 1):
        if None in (f[i - 1], s[i - 1]):
            continue
        nxt = bars[i + 1]
        if pos is not None:
            side, entry, t0 = pos
            if (side > 0 and f[i] < m[i]) or (side < 0 and f[i] > m[i]):
                exit_ = nxt["bid_open"] if side > 0 else nxt["ask_open"]
                gross = side * (exit_ / entry - 1)
                out.append({"market": market, "side": side, "entry_ms": t0, "exit_ms": nxt["datetime"],
                            "net": gross - extra_cost(market, entry), "gross": gross})
                pos = None
        if pos is None:
            up = f[i] > m[i] > s[i] and not (f[i - 1] > m[i - 1] > s[i - 1])
            down = f[i] < m[i] < s[i] and not (f[i - 1] < m[i - 1] < s[i - 1])
            if up or down:
                side = 1 if up else -1
                pos = (side, nxt["ask_open"] if side > 0 else nxt["bid_open"], nxt["datetime"])
    return out


def evaluate(rows: list[dict]) -> dict:
    if not rows:
        return {"trades": 0}
    net = [t["net"] for t in rows]
    gains, losses = sum(x for x in net if x > 0), -sum(x for x in net if x < 0)
    by_month: dict[str, float] = {}
    for t in rows:
        key = datetime.fromtimestamp(t["entry_ms"] / 1000, timezone.utc).strftime("%Y-%m")
        by_month[key] = by_month.get(key, 0.0) + t["net"]
    vals, rng = list(by_month.values()), random.Random(7)
    p = sum(sum(rng.choice(vals) for _ in vals) > 0 for _ in range(2000)) / 2000
    return {"trades": len(net), "hit_rate": round(sum(x > 0 for x in net) / len(net), 3),
            "mean_net_pct": round(100 * sum(net) / len(net), 4),
            "mean_gross_pct": round(100 * sum(t["gross"] for t in rows) / len(net), 4),
            "profit_factor": round(gains / losses, 3) if losses else None, "p_mean_positive": p}


def main() -> None:
    found = {m: trades(m, h4_bars(m)) for m in MARKETS}
    split = int(datetime(2018, 7, 1, tzinfo=timezone.utc).timestamp() * 1000)
    every = [t for ts in found.values() for t in ts]
    out = {"all": evaluate(every),
           "first_half_2010_2018": evaluate([t for t in every if t["entry_ms"] < split]),
           "second_half_2018_2026": evaluate([t for t in every if t["entry_ms"] >= split]),
           "fx_only": evaluate([t for t in every if not t["market"].startswith("XA")]),
           "metals_only": evaluate([t for t in every if t["market"].startswith("XA")]),
           "per_market": {m: evaluate(ts) for m, ts in found.items()}}
    pos = sum(1 for r in out["per_market"].values() if r.get("mean_net_pct", 0) > 0)
    a = out["all"]
    out["passes"] = (a["trades"] >= 300 and a["mean_net_pct"] > 0 and a["p_mean_positive"] >= 0.95
                     and (a["profit_factor"] or 0) >= 1.2 and pos >= 8
                     and out["first_half_2010_2018"]["mean_net_pct"] > 0
                     and out["second_half_2018_2026"]["mean_net_pct"] > 0)
    out["markets_positive"] = pos
    Path("research_output").mkdir(exist_ok=True)
    Path("research_output/three_ma.json").write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
