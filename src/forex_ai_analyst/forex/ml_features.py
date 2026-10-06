"""Feature rows for the FX ML model — one implementation for training and live use.

Pure Python (no numpy), so the live service can compute exactly the features the
model was trained on. Everything at bar i uses closes through i, cross-asset
data strictly before i's date, and interest rates through the previous month.
"""
from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from statistics import median, pstdev

from forex_ai_analyst.forex import fx_factors
from forex_ai_analyst.forex.data import Market, cost_fraction
from forex_ai_analyst.forex.development import _fvg_touch, _sweeps
from forex_ai_analyst.forex.regime_system import features as base_features
from forex_ai_analyst.lab.candidates import confirmed_pivots, rsi

CARRY = {"EURUSD": ("EUR", "USD"), "GBPUSD": ("GBP", "USD"), "AUDUSD": ("AUD", "USD"), "NZDUSD": ("NZD", "USD"),
         "USDCAD": ("USD", "CAD"), "USDCHF": ("USD", "CHF"), "USDJPY": ("USD", "JPY"), "EURJPY": ("EUR", "JPY"),
         "GBPJPY": ("GBP", "JPY"), "XAUUSD": (None, "USD")}
USD_LEGS = {"EURUSD": -1, "GBPUSD": -1, "AUDUSD": -1, "NZDUSD": -1, "USDJPY": 1, "USDCAD": 1, "USDCHF": 1}
SPX, VIX = Market("US500", "^GSPC", 0, pct=True), Market("VIX", "^VIX", 0, pct=True)


def day_of(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).date().isoformat()


def busday_count(start: date, end: date) -> int:
    """Weekdays in [start, end)."""
    count, d = 0, start
    while d < end:
        count += d.weekday() < 5
        d += timedelta(days=1)
    return count


@dataclass
class MarketCache:
    name: str
    index: int
    bars: list[dict]
    days: list[str]
    f: dict
    rsi14: list
    sweeps: tuple
    fvg: tuple
    swings: tuple
    cost: float


@dataclass
class Context:
    markets: list[Market]
    spx: dict[str, float]
    vix: dict[str, float]
    rates: dict
    closes_by_day: dict[str, dict[str, float]]
    caches: dict[str, MarketCache] = field(default_factory=dict)

    def __post_init__(self):
        self.spx_days, self.vix_days = sorted(self.spx), sorted(self.vix)
        self.sorted_days = {name: sorted(s) for name, s in self.closes_by_day.items()}


def build_context(markets: list[Market], all_bars: dict[str, list[dict]], spx_bars: list[dict],
                  vix_bars: list[dict], rates: dict) -> Context:
    ctx = Context(markets, {day_of(b["datetime"]): b["close"] for b in spx_bars},
                  {day_of(b["datetime"]): b["close"] for b in vix_bars}, rates,
                  {name: {day_of(b["datetime"]): b["close"] for b in bars} for name, bars in all_bars.items()})
    for index, market in enumerate(markets):
        bars = all_bars[market.name]
        f = base_features(bars)
        ctx.caches[market.name] = MarketCache(market.name, index, bars, [day_of(b["datetime"]) for b in bars], f,
                                              rsi(f["close"], 14), _sweeps(bars), _fvg_touch(bars),
                                              confirmed_pivots(bars), cost_fraction(market, median(f["close"])))
    return ctx


def feature_row(ctx: Context, name: str, i: int) -> dict | None:
    c = ctx.caches[name]
    f, closes = c.f, c.f["close"]
    if i < 200:
        return None
    a, mean, sd = f["atr"][i], f["bb"][0][i], f["bb"][1][i]
    if None in (a, f["adx"][i], f["er"][i], f["vol_ratio"][i], c.rsi14[i], f["rsi2"][i], mean) or not a or not sd:
        return None
    day = c.days[i]
    idx = bisect_left(ctx.spx_days, day) - 1                     # strictly before today
    s20 = ctx.spx[ctx.spx_days[idx]] / ctx.spx[ctx.spx_days[idx - 20]] - 1 if idx >= 20 else None
    idx = bisect_left(ctx.vix_days, day) - 1
    vix_level, vix_chg = ((ctx.vix[ctx.vix_days[idx]], ctx.vix[ctx.vix_days[idx]] / ctx.vix[ctx.vix_days[idx - 5]] - 1)
                          if idx >= 5 else (None, None))
    month = day[:7]
    prev_month = f"{int(month[:4]) - (month[5:] == '01')}-{(int(month[5:]) - 2) % 12 + 1:02d}"
    base, quote = CARRY[name]
    rq = fx_factors._rate(ctx.rates, quote, prev_month)
    rb = fx_factors._rate(ctx.rates, base, prev_month) if base else 0.0
    usd = []
    for pair, sign in USD_LEGS.items():
        series, keys = ctx.closes_by_day[pair], ctx.sorted_days[pair]
        j = bisect_right(keys, day) - 1                           # last day <= today
        if j >= 20:
            usd.append(sign * (series[keys[j]] / series[keys[j - 20]] - 1))
    if None in (s20, vix_level, rq, rb) or len(usd) < 5:
        return None

    def ret(k):
        return math.log(closes[i] / closes[i - k])

    vol20 = pstdev([math.log(closes[j] / closes[j - 1]) for j in range(i - 19, i + 1)])
    next_month = date(int(month[:4]) + (month[5:] == "12"), int(month[5:]) % 12 + 1, 1)
    to_month_end = busday_count(date.fromisoformat(day) + timedelta(days=1), next_month)
    swept_low, swept_high = c.sweeps
    fvg_bull, fvg_bear = c.fvg
    swing_high, swing_low, _ = c.swings
    feats = {
        "r1": ret(1), "r5": ret(5), "r20": ret(20), "r60": ret(60), "r120": ret(120),
        "vol20": vol20, "atr_ratio": f["vol_ratio"][i], "adx": f["adx"][i], "er": f["er"][i],
        "rsi14": c.rsi14[i], "rsi2": f["rsi2"][i], "bb_z": (closes[i] - mean) / sd,
        "d_ema20": (closes[i] - f["ema20"][i]) / a, "d_ema50": (closes[i] - f["ema50"][i]) / a,
        "d_ema200": (closes[i] - f["ema200"][i]) / a,
        "sweep_low": float(swept_low[i]), "sweep_high": float(swept_high[i]),
        "fvg_bull": float(fvg_bull[i]), "fvg_bear": float(fvg_bear[i]),
        "d_swing_high": (swing_high[i] - closes[i]) / a if swing_high[i] else 0.0,
        "d_swing_low": (closes[i] - swing_low[i]) / a if swing_low[i] else 0.0,
        "dow": date.fromisoformat(day).weekday(), "month_end_soon": float(to_month_end < 5),
        "carry": rb - rq, "spx_r20": s20, "vix": vix_level, "vix_chg5": vix_chg, "usd_mom20": sum(usd) / len(usd),
    }
    for k, other in enumerate(ctx.markets):
        feats[f"mkt_{other.name}"] = float(k == c.index)
    return feats


def load_context(markets: list[Market]) -> Context:
    from forex_ai_analyst.forex.data import load_yahoo
    return build_context(markets, {m.name: load_yahoo(m, "1d") for m in markets}, load_yahoo(SPX, "1d"),
                         load_yahoo(VIX, "1d"), fx_factors.load_rates())
