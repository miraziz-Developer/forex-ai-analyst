"""Closed-candle support/resistance rejection entries for range and trend pullbacks."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from market_regime import RegimeSnapshot
from scalping_core import CandidateSignal, Direction, MarketRegime
from scalping_indicators import atr, candle_confirmation, relative_volume, support_resistance_zones


def _inside_zone(price: float, zones: list[tuple[float, float]]) -> tuple[float, float] | None:
    return next((zone for zone in reversed(zones) if zone[0] <= price <= zone[1]), None)


def evaluate(pair: str, bars_15m: list[dict], bars_5m: list[dict], regime: RegimeSnapshot,
             now: datetime | None = None) -> CandidateSignal | None:
    now = now or datetime.now(timezone.utc)
    if regime.regime not in {MarketRegime.RANGING, MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN} or len(bars_5m) < 55:
        return None
    supports, resistances = support_resistance_zones(bars_5m)
    current, volatility = bars_5m[-1], atr(bars_5m)
    pattern, volume = candle_confirmation(bars_5m), relative_volume(bars_5m)
    if volatility is None or pattern is None:
        return None
    support = _inside_zone(float(current["low"]), supports)
    resistance = _inside_zone(float(current["high"]), resistances)
    bullish = support is not None and pattern in {"BULLISH_ENGULFING", "BULLISH_REJECTION", "BULLISH_RECLAIM"}
    bearish = resistance is not None and pattern in {"BEARISH_ENGULFING", "BEARISH_REJECTION", "BEARISH_RECLAIM"}
    if bullish and regime.regime is MarketRegime.TRENDING_DOWN:
        return None
    if bearish and regime.regime is MarketRegime.TRENDING_UP:
        return None
    if bullish == bearish or volume is None or volume < 1.0:
        return None
    direction, zone = (Direction.BUY, support) if bullish else (Direction.SELL, resistance)
    entry = float(current["close"])
    stop = zone[0] - volatility * .2 if bullish else zone[1] + volatility * .2
    opposing = resistances[-1][0] if bullish and resistances else supports[-1][1] if not bullish and supports else None
    target = opposing if opposing is not None else entry + (entry - stop) * 1.5 if bullish else entry - (stop - entry) * 1.5
    candidate = CandidateSignal("support_resistance_rejection", pair.upper(), direction, regime.regime, entry, stop, target,
                                now + timedelta(minutes=60), "5m", "15m", int(current["datetime"]), 75,
                                ("validated 5m support/resistance zone", f"closed {pattern.lower()}",
                                 f"relative volume {volume:.2f}x"),
                                "5m candle closes beyond the rejection zone",
                                {**regime.features, "pattern": pattern, "relative_volume": volume,
                                 "zone_low": zone[0], "zone_high": zone[1], "atr_5m": volatility})
    return candidate if candidate.validation_error(now) is None else None