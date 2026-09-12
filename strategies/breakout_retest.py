"""Volume-confirmed breakout followed by a closed-candle retest confirmation."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from market_regime import RegimeSnapshot
from scalping_core import CandidateSignal, Direction, MarketRegime
from scalping_indicators import atr, candle_confirmation, relative_volume, support_resistance_zones


def evaluate(pair: str, bars_15m: list[dict], bars_5m: list[dict], regime: RegimeSnapshot,
             now: datetime | None = None) -> CandidateSignal | None:
    now = now or datetime.now(timezone.utc)
    if regime.regime is not MarketRegime.BREAKOUT_READY or len(bars_5m) < 55:
        return None
    supports, resistances = support_resistance_zones(bars_5m[:-2])
    breakout, retest = bars_5m[-2], bars_5m[-1]
    volume, pattern, volatility = relative_volume(bars_5m[:-1]), candle_confirmation(bars_5m), atr(bars_5m)
    if volatility is None or volume is None or volume < 1.3 or pattern is None:
        return None
    resistance = resistances[-1] if resistances else None
    support = supports[-1] if supports else None
    bullish = resistance and float(breakout["close"]) > resistance[1] and float(retest["low"]) <= resistance[1] and \
        float(retest["close"]) > resistance[1] and pattern in {"BULLISH_ENGULFING", "BULLISH_REJECTION", "BULLISH_RECLAIM"}
    bearish = support and float(breakout["close"]) < support[0] and float(retest["high"]) >= support[0] and \
        float(retest["close"]) < support[0] and pattern in {"BEARISH_ENGULFING", "BEARISH_REJECTION", "BEARISH_RECLAIM"}
    if not bullish and not bearish:
        return None
    direction, zone = (Direction.BUY, resistance) if bullish else (Direction.SELL, support)
    entry = float(retest["close"])
    stop = zone[0] - volatility * .2 if bullish else zone[1] + volatility * .2
    target = entry + (entry - stop) * 1.5 if bullish else entry - (stop - entry) * 1.5
    candidate = CandidateSignal("breakout_retest", pair.upper(), direction, regime.regime, entry, stop, target,
                                now + timedelta(minutes=60), "5m", "15m", int(retest["datetime"]), 80,
                                ("compressed 15m breakout-ready regime", "5m close beyond validated structure",
                                 "closed retest held as new support/resistance", f"relative volume {volume:.2f}x"),
                                "5m candle closes back through the broken zone",
                                {**regime.features, "pattern": pattern, "relative_volume": volume,
                                 "zone_low": zone[0], "zone_high": zone[1], "atr_5m": volatility})
    return candidate if candidate.validation_error(now) is None else None