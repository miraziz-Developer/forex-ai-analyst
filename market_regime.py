"""Fail-closed 15m market regime classification."""
from __future__ import annotations

from dataclasses import dataclass

from scalping_core import MarketRegime
from scalping_indicators import atr_percentile, bollinger, dmi_adx, ema, swing_bias, values


@dataclass(frozen=True)
class RegimeSnapshot:
    regime: MarketRegime
    features: dict[str, float | str]


def classify_market_regime(bars: list[dict]) -> RegimeSnapshot:
    if len(bars) < 255:
        return RegimeSnapshot(MarketRegime.UNCERTAIN, {"reason": "insufficient closed history"})
    closes = values(bars)
    ema50, ema200 = ema(closes, 50), ema(closes, 200)
    dmi = dmi_adx(bars)
    percentile, bands, structure = atr_percentile(bars), bollinger(bars), swing_bias(bars)
    if None in (ema50, ema200, percentile) or dmi is None or bands is None:
        return RegimeSnapshot(MarketRegime.UNCERTAIN, {"reason": "indicator unavailable"})
    adx, plus_di, minus_di = dmi
    features = {"ema50": ema50, "ema200": ema200, "adx": adx, "+di": plus_di,
                "-di": minus_di, "atr_percentile": percentile, "bb_width": bands[3],
                "structure": structure or "MIXED"}
    if percentile > 90:
        return RegimeSnapshot(MarketRegime.HIGH_VOLATILITY, features)
    if (ema50 > ema200 and closes[-1] > ema200 and adx >= 22 and plus_di > minus_di
            and structure == "BULLISH" and percentile >= 20):
        return RegimeSnapshot(MarketRegime.TRENDING_UP, features)
    if (ema50 < ema200 and closes[-1] < ema200 and adx >= 22 and minus_di > plus_di
            and structure == "BEARISH" and percentile >= 20):
        return RegimeSnapshot(MarketRegime.TRENDING_DOWN, features)
    if adx < 20 and percentile >= 20:
        return RegimeSnapshot(MarketRegime.RANGING, features)
    if percentile < 20 and bands[3] < 0.03:
        return RegimeSnapshot(MarketRegime.BREAKOUT_READY, features)
    return RegimeSnapshot(MarketRegime.UNCERTAIN, features)