"""Closed-bar indicators. Input bars are chronological (oldest first)."""
from __future__ import annotations

from math import sqrt


def values(bars: list[dict], field: str = "close") -> list[float]:
    return [float(item[field]) for item in bars]


def ema(series: list[float], period: int) -> float | None:
    if len(series) < period or period < 1:
        return None
    result = sum(series[:period]) / period
    multiplier = 2 / (period + 1)
    for value in series[period:]:
        result = value * multiplier + result * (1 - multiplier)
    return result


def rsi(series: list[float], period: int = 14) -> float | None:
    if len(series) <= period:
        return None
    changes = [series[index] - series[index - 1] for index in range(1, len(series))]
    gains = [max(change, 0.0) for change in changes[-period:]]
    losses = [max(-change, 0.0) for change in changes[-period:]]
    average_loss = sum(losses) / period
    if average_loss == 0:
        return 100.0
    return 100 - 100 / (1 + (sum(gains) / period) / average_loss)


def atr(bars: list[dict], period: int = 14) -> float | None:
    if len(bars) < period + 1:
        return None
    ranges = []
    for index in range(1, len(bars)):
        bar, previous = bars[index], bars[index - 1]
        ranges.append(max(float(bar["high"]) - float(bar["low"]),
                          abs(float(bar["high"]) - float(previous["close"])),
                          abs(float(bar["low"]) - float(previous["close"]))))
    return sum(ranges[-period:]) / period


def atr_percentile(bars: list[dict], period: int = 14, lookback: int = 50) -> float | None:
    if len(bars) < period + lookback:
        return None
    samples = [atr(bars[:index], period) for index in range(period + 1, len(bars) + 1)]
    samples = [sample for sample in samples if sample is not None][-lookback:]
    if len(samples) < lookback:
        return None
    return 100 * sum(sample <= samples[-1] for sample in samples) / len(samples)


def dmi_adx(bars: list[dict], period: int = 14) -> tuple[float, float, float] | None:
    if len(bars) < period + 1:
        return None
    plus, minus, true_ranges = [], [], []
    for index in range(1, len(bars)):
        current, previous = bars[index], bars[index - 1]
        up = float(current["high"]) - float(previous["high"])
        down = float(previous["low"]) - float(current["low"])
        plus.append(up if up > down and up > 0 else 0.0)
        minus.append(down if down > up and down > 0 else 0.0)
        true_ranges.append(max(float(current["high"]) - float(current["low"]),
                               abs(float(current["high"]) - float(previous["close"])),
                               abs(float(current["low"]) - float(previous["close"]))))
    tr = sum(true_ranges[-period:])
    if not tr:
        return 0.0, 0.0, 0.0
    plus_di, minus_di = 100 * sum(plus[-period:]) / tr, 100 * sum(minus[-period:]) / tr
    denominator = plus_di + minus_di
    adx = 100 * abs(plus_di - minus_di) / denominator if denominator else 0.0
    return adx, plus_di, minus_di


def bollinger(bars: list[dict], period: int = 20) -> tuple[float, float, float, float] | None:
    closes = values(bars)
    if len(closes) < period:
        return None
    window = closes[-period:]
    middle = sum(window) / period
    deviation = sqrt(sum((value - middle) ** 2 for value in window) / period)
    upper, lower = middle + 2 * deviation, middle - 2 * deviation
    return upper, middle, lower, (upper - lower) / middle if middle else 0.0


def relative_volume(bars: list[dict], period: int = 20) -> float | None:
    if len(bars) < period + 1:
        return None
    baseline = sum(float(bar["volume"]) for bar in bars[-period - 1:-1]) / period
    return float(bars[-1]["volume"]) / baseline if baseline else None


def macd_histogram_direction(bars: list[dict]) -> str | None:
    closes = values(bars)
    if len(closes) < 36:
        return None
    histograms = []
    for end in range(35, len(closes) + 1):
        window = closes[:end]
        fast, slow = ema(window, 12), ema(window, 26)
        if fast is None or slow is None:
            continue
        macd_series = [(ema(closes[:point], 12) or 0) - (ema(closes[:point], 26) or 0)
                       for point in range(26, end + 1)]
        signal = ema(macd_series, 9)
        if signal is not None:
            histograms.append(fast - slow - signal)
    if len(histograms) < 2:
        return None
    return "UP" if histograms[-1] > histograms[-2] else "DOWN"


def stochastic_rsi(bars: list[dict], period: int = 14, window: int = 14) -> float | None:
    closes = values(bars)
    rsis = [rsi(closes[:end], period) for end in range(period + 1, len(closes) + 1)]
    rsis = [value for value in rsis if value is not None]
    if len(rsis) < window:
        return None
    low, high = min(rsis[-window:]), max(rsis[-window:])
    return 50.0 if high == low else 100 * (rsis[-1] - low) / (high - low)


def swing_bias(bars: list[dict], span: int = 2) -> str | None:
    if len(bars) < span * 2 + 5:
        return None
    highs, lows = [], []
    for index in range(span, len(bars) - span):
        window = bars[index - span:index + span + 1]
        if float(bars[index]["high"]) == max(float(item["high"]) for item in window):
            highs.append(float(bars[index]["high"]))
        if float(bars[index]["low"]) == min(float(item["low"]) for item in window):
            lows.append(float(bars[index]["low"]))
    if len(highs) >= 2 and len(lows) >= 2:
        if highs[-1] > highs[-2] and lows[-1] > lows[-2]:
            return "BULLISH"
        if highs[-1] < highs[-2] and lows[-1] < lows[-2]:
            return "BEARISH"
    return None


def supertrend_direction(bars: list[dict], period: int = 10, multiplier: float = 3.0) -> str | None:
    """Return BUY/SELL from closed chronological bars, or None without enough history."""
    if len(bars) < period + 2 or period < 1 or multiplier <= 0:
        return None
    direction, final_upper, final_lower = "BUY", None, None
    for index in range(period, len(bars)):
        window = bars[:index + 1]
        volatility = atr(window, period)
        if volatility is None:
            continue
        current, previous = window[-1], window[-2]
        midpoint = (float(current["high"]) + float(current["low"])) / 2
        basic_upper, basic_lower = midpoint + multiplier * volatility, midpoint - multiplier * volatility
        if final_upper is None:
            final_upper, final_lower = basic_upper, basic_lower
            continue
        final_upper = basic_upper if basic_upper < final_upper or float(previous["close"]) > final_upper else final_upper
        final_lower = basic_lower if basic_lower > final_lower or float(previous["close"]) < final_lower else final_lower
        if float(current["close"]) > final_upper:
            direction = "BUY"
        elif float(current["close"]) < final_lower:
            direction = "SELL"
    return direction


def support_resistance_zones(bars: list[dict], *, span: int = 2, lookback: int = 50,
                             tolerance_atr: float = 0.35) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """Return confirmed support/resistance zones without using an unconfirmed final pivot."""
    if span < 1 or lookback < span * 2 + 1 or len(bars) < span * 2 + 1:
        return [], []
    recent = bars[-lookback:]
    volatility = atr(recent)
    if volatility is None or volatility <= 0:
        return [], []
    supports, resistances = [], []
    # A pivot needs `span` subsequent *closed* bars, so the last span bars are excluded.
    for index in range(span, len(recent) - span):
        window = recent[index - span:index + span + 1]
        low, high = float(recent[index]["low"]), float(recent[index]["high"])
        if low == min(float(item["low"]) for item in window):
            supports.append(low)
        if high == max(float(item["high"]) for item in window):
            resistances.append(high)
    width = volatility * tolerance_atr
    return ([(level - width, level + width) for level in supports[-3:]],
            [(level - width, level + width) for level in resistances[-3:]])


def structure_event(bars: list[dict], span: int = 2) -> str | None:
    """Classify the newest closed-bar break as bullish/bearish BOS or CHoCH.

    This is deliberately conservative: it needs two confirmed swing highs and lows.
    """
    if len(bars) < span * 2 + 8:
        return None
    highs, lows = [], []
    for index in range(span, len(bars) - span):
        window = bars[index - span:index + span + 1]
        if float(bars[index]["high"]) == max(float(item["high"]) for item in window):
            highs.append(float(bars[index]["high"]))
        if float(bars[index]["low"]) == min(float(item["low"]) for item in window):
            lows.append(float(bars[index]["low"]))
    if len(highs) < 2 or len(lows) < 2:
        return None
    prior_bias = "BULLISH" if highs[-1] > highs[-2] and lows[-1] > lows[-2] else \
        "BEARISH" if highs[-1] < highs[-2] and lows[-1] < lows[-2] else None
    close = float(bars[-1]["close"])
    if close > highs[-1]:
        return "BULLISH_BOS" if prior_bias == "BULLISH" else "BULLISH_CHOCH"
    if close < lows[-1]:
        return "BEARISH_BOS" if prior_bias == "BEARISH" else "BEARISH_CHOCH"
    return None


def higher_timeframe_bias(bars: list[dict], fast: int = 20, slow: int = 50) -> str | None:
    """Lightweight EMA trend bias for a higher timeframe (BULLISH/BEARISH/None).

    Deliberately cheaper than classify_market_regime (which needs 255 bars for
    EMA200): a daily/4H alignment check only needs a fast/slow EMA read, not a
    full regime classification. None means "not enough history to judge" and
    must never be treated as agreement by a caller enforcing alignment.
    """
    if len(bars) < slow:
        return None
    closes = values(bars)
    ema_fast, ema_slow = ema(closes, fast), ema(closes, slow)
    if ema_fast is None or ema_slow is None:
        return None
    if ema_fast > ema_slow and closes[-1] > ema_slow:
        return "BULLISH"
    if ema_fast < ema_slow and closes[-1] < ema_slow:
        return "BEARISH"
    return None


def candle_confirmation(bars: list[dict]) -> str | None:
    """Classify the newest closed candle only when it has meaningful directional action."""
    if len(bars) < 2:
        return None
    previous, current = bars[-2], bars[-1]
    opening, close, high, low = (float(current[key]) for key in ("open", "close", "high", "low"))
    prior_open, prior_close = float(previous["open"]), float(previous["close"])
    body, candle_range = abs(close - opening), high - low
    if candle_range <= 0 or body <= 0:
        return None
    lower_wick, upper_wick = min(opening, close) - low, high - max(opening, close)
    if close > opening and opening <= prior_close and close >= prior_open and prior_close < prior_open:
        return "BULLISH_ENGULFING"
    if close < opening and opening >= prior_close and close <= prior_open and prior_close > prior_open:
        return "BEARISH_ENGULFING"
    if lower_wick >= body * 2 and close > opening and close >= low + candle_range * 0.6:
        return "BULLISH_REJECTION"
    if upper_wick >= body * 2 and close < opening and close <= low + candle_range * 0.4:
        return "BEARISH_REJECTION"
    if body / candle_range >= 0.65:
        return "BULLISH_RECLAIM" if close > opening else "BEARISH_RECLAIM"
    return None