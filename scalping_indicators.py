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