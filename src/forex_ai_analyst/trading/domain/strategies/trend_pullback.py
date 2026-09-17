"""15m trend alignment with a closed 5m pullback/reclaim entry."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from forex_ai_analyst.trading.domain.regime import RegimeSnapshot
from forex_ai_analyst.trading.domain.models import CandidateSignal, Direction, MarketRegime
from forex_ai_analyst.trading.domain.indicators import (atr, ema, macd_histogram_direction, relative_volume, rsi,
                                 stochastic_rsi, values)


def _bullish_reclaim(bar: dict, ema20: float) -> bool:
    body = abs(bar["close"] - bar["open"])
    lower_wick = min(bar["open"], bar["close"]) - bar["low"]
    return bar["close"] > bar["open"] and bar["close"] > ema20 and (lower_wick >= body * 0.5 or body > 0)


def _bearish_reclaim(bar: dict, ema20: float) -> bool:
    body = abs(bar["close"] - bar["open"])
    upper_wick = bar["high"] - max(bar["open"], bar["close"])
    return bar["close"] < bar["open"] and bar["close"] < ema20 and (upper_wick >= body * 0.5 or body > 0)


def evaluate(pair: str, bars_15m: list[dict], bars_5m: list[dict], regime: RegimeSnapshot,
             now: datetime | None = None) -> CandidateSignal | None:
    """Returns no signal unless all mandatory closed-bar conditions are met."""
    now = now or datetime.now(timezone.utc)
    if regime.regime not in {MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN} or len(bars_5m) < 40:
        return None
    closes = values(bars_5m)
    ema20, current_atr, current_rsi = ema(closes, 20), atr(bars_5m), rsi(closes)
    if None in (ema20, current_atr, current_rsi) or current_atr <= 0:
        return None
    bar, previous = bars_5m[-1], bars_5m[-2]
    volume = relative_volume(bars_5m)
    macd, stoch = macd_histogram_direction(bars_5m), stochastic_rsi(bars_5m)
    bullish = regime.regime is MarketRegime.TRENDING_UP
    pullback_touch = ((previous["low"] <= ema20 * 1.003 and bar["close"] >= ema20) if bullish
                      else (previous["high"] >= ema20 * .997 and bar["close"] <= ema20))
    candle_ok = _bullish_reclaim(bar, ema20) if bullish else _bearish_reclaim(bar, ema20)
    rsi_ok = current_rsi >= 50 if bullish else current_rsi <= 50
    if not (pullback_touch and candle_ok and rsi_ok):
        return None
    direction = Direction.BUY if bullish else Direction.SELL
    stop = min(previous["low"], bar["low"]) - current_atr * .2 if bullish else \
        max(previous["high"], bar["high"]) + current_atr * .2
    entry = bar["close"]
    target = entry + (entry - stop) * 1.5 if bullish else entry - (stop - entry) * 1.5
    confirmations = ["15m EMA50/EMA200, ADX/DMI, and structure aligned", "5m EMA20 pullback reclaimed",
                     "closed bullish rejection candle" if bullish else "closed bearish rejection candle",
                     "5m RSI reclaimed 50" if bullish else "5m RSI lost 50"]
    score = 20 + 20 + 20 + 15
    momentum = (macd == "UP" and bullish) or (macd == "DOWN" and not bullish) or \
        (stoch is not None and ((stoch > 50 and bullish) or (stoch < 50 and not bullish)))
    if momentum:
        score += 10
        confirmations.append("5m momentum turned with the trend")
    if volume is not None and volume >= 1.0:
        score += 10
        confirmations.append(f"relative volume {volume:.2f}x")
    score += 5  # constructed target is exactly 1.5R
    return CandidateSignal(
        strategy="trend_pullback", pair=pair.upper(), direction=direction, regime=regime.regime,
        entry_price=entry, stop_price=stop, target_price=target,
        expires_at=now + timedelta(minutes=60), signal_timeframe="5m", trend_timeframe="15m",
        candle_time_ms=int(bar["datetime"]), score=score, confirmations=tuple(confirmations),
        invalidation_reason="5m candle closes beyond pullback swing and EMA20",
        features={**regime.features, "ema20": ema20, "rsi_5m": current_rsi,
                  "atr_5m": current_atr, "relative_volume": volume or 0.0,
                  "macd_direction": macd or "UNAVAILABLE", "stoch_rsi": stoch or 0.0},
    )