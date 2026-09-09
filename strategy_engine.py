"""Deterministic, look-ahead-safe trade candidates for live and research use.

The LLM remains a second opinion, but it may only confirm the direction produced
here.  All functions are dependency-free so the exact same rules can be replayed.
Bars passed to this module must be CLOSED and ordered most-recent-first.
"""

from dataclasses import dataclass
import json
import math
import os


@dataclass(frozen=True)
class StrategyConfig:
    name: str = "snr_trend_following"
    fast_ema: int = 20
    slow_ema: int = 50
    daily_ema: int = 20
    adx_min: float = 20.0
    volume_ratio_min: float = 1.2
    rsi_long_min: float = 42.0
    rsi_long_max: float = 64.0
    atr_stop: float = 1.5
    reward_risk: float = 2.0
    bollinger_period: int = 20
    bollinger_std: float = 2.0
    atr_expansion_min: float = 1.0
    supertrend_multiplier: float = 3.0
    min_confirmations: int = 3
    pivot_span: int = 2
    sr_lookback: int = 40
    sr_tolerance_atr: float = 0.45
    channel_lookback: int = 20


def _values(bars: list[dict], key: str) -> list[float]:
    return [float(bar[key]) for bar in reversed(bars)]


def ema(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    seed = sum(values[:period]) / period
    multiplier = 2 / (period + 1)
    result = seed
    for value in values[period:]:
        result = value * multiplier + result * (1 - multiplier)
    return result


def macd_histogram(values: list[float], fast: int = 12, slow: int = 26,
                   signal: int = 9) -> float | None:
    if len(values) < slow + signal:
        return None
    macd_series = []
    for end in range(slow, len(values) + 1):
        window = values[:end]
        fast_value, slow_value = ema(window, fast), ema(window, slow)
        macd_series.append(fast_value - slow_value)
    signal_value = ema(macd_series, signal)
    return macd_series[-1] - signal_value if signal_value is not None else None


def bollinger_bands(values: list[float], period: int = 20,
                    deviations: float = 2.0) -> tuple[float, float, float] | None:
    if len(values) < period:
        return None
    window = values[-period:]
    middle = sum(window) / period
    standard_deviation = (sum((value - middle) ** 2 for value in window) / period) ** 0.5
    return middle + deviations * standard_deviation, middle, middle - deviations * standard_deviation


def atr_expansion_ratio(bars: list[dict], short_period: int = 7,
                        long_period: int = 28) -> float | None:
    short, long = atr(bars, short_period), atr(bars, long_period)
    return short / long if short is not None and long else None


def supertrend_direction(bars: list[dict], period: int = 10,
                         multiplier: float = 3.0) -> str | None:
    """Return the current Supertrend direction using only closed bars."""
    chronological = list(reversed(bars))
    if len(chronological) < period + 2:
        return None
    true_ranges = []
    for index, current in enumerate(chronological):
        high, low = float(current["high"]), float(current["low"])
        previous_close = float(chronological[index - 1]["close"]) if index else float(current["close"])
        true_ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    rolling_true_range = sum(true_ranges[1:period + 1])
    final_upper = final_lower = supertrend = None
    direction = None
    for index in range(period, len(chronological)):
        if index > period:
            rolling_true_range += true_ranges[index] - true_ranges[index - period]
        current_atr = rolling_true_range / period
        current, previous = chronological[index], chronological[index - 1]
        midpoint = (float(current["high"]) + float(current["low"])) / 2
        basic_upper, basic_lower = midpoint + multiplier * current_atr, midpoint - multiplier * current_atr
        if final_upper is None:
            final_upper, final_lower = basic_upper, basic_lower
        else:
            final_upper = (basic_upper if basic_upper < final_upper or
                           float(previous["close"]) > final_upper else final_upper)
            final_lower = (basic_lower if basic_lower > final_lower or
                           float(previous["close"]) < final_lower else final_lower)
        close = float(current["close"])
        if supertrend is None:
            direction = "BUY" if close >= final_upper else "SELL"
        elif direction == "SELL" and close > final_upper:
            direction = "BUY"
        elif direction == "BUY" and close < final_lower:
            direction = "SELL"
        supertrend = final_lower if direction == "BUY" else final_upper
    return direction


def rsi(values: list[float], period: int = 14) -> float | None:
    if len(values) < period + 1:
        return None
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(change, 0) for change in changes[-period:]]
    losses = [max(-change, 0) for change in changes[-period:]]
    average_gain, average_loss = sum(gains) / period, sum(losses) / period
    if average_loss == 0:
        return 100.0
    relative_strength = average_gain / average_loss
    return 100 - 100 / (1 + relative_strength)


def atr(bars: list[dict], period: int = 14) -> float | None:
    chronological = list(reversed(bars))
    if len(chronological) < period + 1:
        return None
    ranges = []
    for previous, current in zip(chronological, chronological[1:]):
        high, low, previous_close = (float(current["high"]), float(current["low"]),
                                     float(previous["close"]))
        ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    return sum(ranges[-period:]) / period


def adx(bars: list[dict], period: int = 14) -> float | None:
    """Wilder-style directional strength; enough for a stable regime gate."""
    chronological = list(reversed(bars))
    if len(chronological) < period * 2 + 1:
        return None
    tr_values, plus_dm, minus_dm = [], [], []
    for previous, current in zip(chronological, chronological[1:]):
        high, low = float(current["high"]), float(current["low"])
        previous_high, previous_low = float(previous["high"]), float(previous["low"])
        up, down = high - previous_high, previous_low - low
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
        tr_values.append(max(high - low, abs(high - float(previous["close"])),
                             abs(low - float(previous["close"]))))
    dx = []
    for end in range(period, len(tr_values) + 1):
        tr_sum = sum(tr_values[end - period:end])
        if not tr_sum:
            continue
        plus = 100 * sum(plus_dm[end - period:end]) / tr_sum
        minus = 100 * sum(minus_dm[end - period:end]) / tr_sum
        dx.append(100 * abs(plus - minus) / (plus + minus) if plus + minus else 0.0)
    return sum(dx[-period:]) / period if len(dx) >= period else None


def _volume_ratio(bars: list[dict], period: int = 20) -> float | None:
    if len(bars) < period + 1:
        return None
    baseline = sum(float(bar["volume"]) for bar in bars[1:period + 1]) / period
    return float(bars[0]["volume"]) / baseline if baseline else None


def _daily_bias(daily_bars: list[dict], period: int) -> str | None:
    closes = _values(daily_bars, "close")
    average = ema(closes, period)
    if average is None:
        return None
    if closes[-1] > average:
        return "BUY"
    if closes[-1] < average:
        return "SELL"
    return None


def support_resistance_levels(bars: list[dict], lookback: int = 40,
                              pivot_span: int = 2) -> tuple[list[float], list[float]]:
    """Return confirmed, look-ahead-safe swing supports and resistances."""
    if pivot_span < 1 or lookback < pivot_span * 2 + 1:
        raise ValueError("S/R lookback must contain both sides of a pivot")
    historical = list(reversed(bars[1:lookback + 1]))
    if len(historical) < pivot_span * 2 + 1:
        return [], []
    supports, resistances = [], []
    for index in range(pivot_span, len(historical) - pivot_span):
        current = historical[index]
        neighbours = (historical[index - pivot_span:index]
                      + historical[index + 1:index + pivot_span + 1])
        low, high = float(current["low"]), float(current["high"])
        if low < min(float(item["low"]) for item in neighbours):
            supports.append(low)
        if high > max(float(item["high"]) for item in neighbours):
            resistances.append(high)
    return supports, resistances


def _indicator_confirmations(direction: str, close: float, open_price: float, momentum: float,
                             volume_ratio: float, histogram: float | None,
                             previous_histogram: float | None, supertrend: str | None,
                             expansion: float | None, bollinger_middle: float | None,
                             config: StrategyConfig) -> list[str]:
    confirmations = []
    if histogram is not None and (histogram > 0 if direction == "BUY" else histogram < 0):
        confirmations.append("MACD direction")
    if supertrend == direction:
        confirmations.append("Supertrend direction")
    if (momentum >= 50 if direction == "BUY" else momentum <= 50):
        confirmations.append("RSI momentum direction")
    if volume_ratio >= config.volume_ratio_min:
        confirmations.append("volume participation")
    if expansion is not None and expansion >= config.atr_expansion_min:
        confirmations.append("ATR expansion")
    if bollinger_middle is not None and (close > bollinger_middle if direction == "BUY"
                                         else close < bollinger_middle):
        confirmations.append("Bollinger midline direction")
    return confirmations

def _evaluate_snr_candidate(primary_bars: list[dict], daily_bars: list[dict],
                            config: StrategyConfig) -> dict | None:
    """Return one S/R rejection in the higher-timeframe trend, or ``None``."""
    needed = max(config.slow_ema + 5, config.sr_lookback + 2, 35)
    if len(primary_bars) < needed or len(daily_bars) < config.daily_ema + 2:
        return None
    closes = _values(primary_bars, "close")
    fast, slow = ema(closes, config.fast_ema), ema(closes, config.slow_ema)
    strength, momentum = adx(primary_bars), rsi(closes)
    volatility, volume_ratio = atr(primary_bars), _volume_ratio(primary_bars)
    bias = _daily_bias(daily_bars, config.daily_ema)
    if None in (fast, slow, strength, momentum, volatility, volume_ratio) or not bias:
        return None

    current = primary_bars[0]
    close, open_price = float(current["close"]), float(current["open"])
    high, low = float(current["high"]), float(current["low"])
    trend = "BUY" if fast > slow and close > fast else "SELL" if fast < slow and close < fast else None
    if strength < config.adx_min or trend != bias:
        return None

    supports, resistances = support_resistance_levels(
        primary_bars, config.sr_lookback, config.pivot_span)
    tolerance = volatility * config.sr_tolerance_atr
    support = max((level for level in supports if level <= close + tolerance), default=None)
    resistance = min((level for level in resistances if level >= close - tolerance), default=None)
    if (trend == "BUY" and support is not None and low <= support + tolerance
            and close > support and close > open_price
            and config.rsi_long_min <= momentum <= config.rsi_long_max):
        direction, structure_level = "BUY", support
    elif (trend == "SELL" and resistance is not None and high >= resistance - tolerance
          and close < resistance and close < open_price
          and 100 - config.rsi_long_max <= momentum <= 100 - config.rsi_long_min):
        direction, structure_level = "SELL", resistance
    else:
        return None
    evidence = [f"confirmed pivot S/R rejection at {structure_level:g}"]
    histogram = macd_histogram(closes)
    previous_histogram = macd_histogram(closes[:-1])
    current_supertrend = supertrend_direction(primary_bars,
                                               multiplier=config.supertrend_multiplier)
    expansion = atr_expansion_ratio(primary_bars)
    bands = bollinger_bands(closes, config.bollinger_period, config.bollinger_std)
    confirmations = _indicator_confirmations(
        direction, close, open_price, momentum, volume_ratio, histogram, previous_histogram,
        current_supertrend, expansion, bands[1] if bands else None, config,
    )
    if len(confirmations) < config.min_confirmations:
        return None
    return {
        "direction": direction,
        "strategy": config.name,
        "atr": volatility,
        "adx": round(strength, 2),
        "rsi": round(momentum, 2),
        "volume_ratio": round(volume_ratio, 2),
        "structure_level": structure_level,
        "confirmation_count": len(confirmations),
        "confirmations": confirmations,
        "evidence": evidence + ["daily/primary trend alignment", f"ADX {strength:.1f}",
                                f"{len(confirmations)} supplementary confirmations"],
    }


def evaluate_candidate(primary_bars: list[dict], daily_bars: list[dict],
                       config: StrategyConfig) -> dict | None:
    """Evaluate one pre-declared family from closed bars without look-ahead."""
    if config.name == "snr_trend_following":
        return _evaluate_snr_candidate(primary_bars, daily_bars, config)
    valid = {"ema_trend_pullback", "donchian_breakout", "macd_continuation",
             "supertrend_continuation", "bollinger_trend_pullback", "bollinger_reversion",
             "rsi_reversion", "volatility_breakout", "range_breakout"}
    if config.name not in valid:
        raise ValueError(f"Unknown strategy: {config.name}")
    needed = max(config.slow_ema + 5, config.channel_lookback + 2,
                 config.bollinger_period + 2, 40)
    if len(primary_bars) < needed or len(daily_bars) < config.daily_ema + 2:
        return None
    closes = _values(primary_bars, "close")
    fast, slow = ema(closes, config.fast_ema), ema(closes, config.slow_ema)
    strength, momentum = adx(primary_bars), rsi(closes)
    volatility, volume_ratio = atr(primary_bars), _volume_ratio(primary_bars)
    bias = _daily_bias(daily_bars, config.daily_ema)
    if None in (fast, slow, strength, momentum, volatility, volume_ratio) or not bias:
        return None
    current = primary_bars[0]
    close, open_price = float(current["close"]), float(current["open"])
    high, low = float(current["high"]), float(current["low"])
    previous_close = float(primary_bars[1]["close"])
    bullish = close > open_price
    trend = "BUY" if fast > slow and close > slow else "SELL" if fast < slow and close < slow else None
    aligned = trend == bias and strength >= config.adx_min
    channel = primary_bars[1:config.channel_lookback + 1]
    channel_high = max(float(bar["high"]) for bar in channel)
    channel_low = min(float(bar["low"]) for bar in channel)
    needs_bands = config.name in {"bollinger_trend_pullback", "bollinger_reversion"}
    bands = (bollinger_bands(closes, config.bollinger_period, config.bollinger_std)
             if needs_bands else None)
    upper, middle, lower = bands if bands else (None, None, None)
    histogram = macd_histogram(closes) if config.name == "macd_continuation" else None
    previous_histogram = (macd_histogram(closes[:-1])
                          if config.name == "macd_continuation" else None)
    expansion = (atr_expansion_ratio(primary_bars)
                 if config.name == "volatility_breakout" else None)
    current_supertrend = (supertrend_direction(
        primary_bars, multiplier=config.supertrend_multiplier)
        if config.name == "supertrend_continuation" else None)

    if config.name == "ema_trend_pullback":
        signal = aligned and ((trend == "BUY" and low <= fast < close and bullish) or
                              (trend == "SELL" and high >= fast > close and not bullish))
    elif config.name == "donchian_breakout":
        signal = aligned and ((trend == "BUY" and close > channel_high) or
                              (trend == "SELL" and close < channel_low))
    elif config.name == "macd_continuation":
        signal = aligned and histogram is not None and previous_histogram is not None and (
            (trend == "BUY" and histogram > 0 >= previous_histogram) or
            (trend == "SELL" and histogram < 0 <= previous_histogram))
    elif config.name == "supertrend_continuation":
        signal = aligned and current_supertrend == trend and (
            (trend == "BUY" and previous_close <= fast < close) or
            (trend == "SELL" and previous_close >= fast > close))
    elif config.name == "bollinger_trend_pullback":
        signal = aligned and middle is not None and (
            (trend == "BUY" and low <= middle < close and bullish) or
            (trend == "SELL" and high >= middle > close and not bullish))
    elif config.name == "volatility_breakout":
        signal = aligned and expansion is not None and expansion >= config.atr_expansion_min \
            and volume_ratio >= config.volume_ratio_min and (
                (trend == "BUY" and close > channel_high) or
                (trend == "SELL" and close < channel_low))
    elif config.name == "range_breakout":
        signal = strength >= config.adx_min and (close > channel_high or close < channel_low)
        trend = "BUY" if close > channel_high else "SELL"
    elif config.name == "bollinger_reversion":
        signal = strength < config.adx_min and lower is not None and upper is not None and (
            (previous_close < lower and close > lower and bullish) or
            (previous_close > upper and close < upper and not bullish))
        trend = "BUY" if bullish else "SELL"
    else:  # rsi_reversion
        signal = strength < config.adx_min and (
            (momentum < config.rsi_long_min and bullish) or
            (momentum > config.rsi_long_max and not bullish))
        trend = "BUY" if bullish else "SELL"
    if not signal or trend is None:
        return None
    return {"direction": trend, "strategy": config.name, "atr": volatility,
            "adx": round(strength, 2), "rsi": round(momentum, 2),
            "volume_ratio": round(volume_ratio, 2),
            "evidence": [config.name.replace("_", " ")]}


def candidate_levels(entry: float, candidate: dict, config: StrategyConfig) -> tuple[float, float]:
    risk = float(candidate["atr"]) * config.atr_stop
    if candidate["direction"] == "BUY":
        stop = min(entry - risk, float(candidate.get("structure_level", entry))
                   - float(candidate["atr"]) * 0.2)
        return entry + (entry - stop) * config.reward_risk, stop
    stop = max(entry + risk, float(candidate.get("structure_level", entry))
               + float(candidate["atr"]) * 0.2)
    return entry - (stop - entry) * config.reward_risk, stop


def config_from_dict(values: dict) -> StrategyConfig:
    if not isinstance(values, dict):
        raise ValueError("Strategy policy must be a JSON object")
    allowed = StrategyConfig.__dataclass_fields__
    unknown = set(values) - set(allowed)
    if unknown:
        raise ValueError(f"Unknown strategy policy fields: {', '.join(sorted(unknown))}")
    config = StrategyConfig(**{key: value for key, value in values.items() if key in allowed})
    valid_names = {"snr_trend_following", "ema_trend_pullback", "donchian_breakout",
                   "macd_continuation", "supertrend_continuation", "bollinger_trend_pullback",
                   "bollinger_reversion", "rsi_reversion", "volatility_breakout",
                   "range_breakout"}
    if config.name not in valid_names:
        raise ValueError(f"Unknown strategy: {config.name}")
    periods = ("fast_ema", "slow_ema", "daily_ema", "bollinger_period",
               "min_confirmations", "pivot_span", "sr_lookback", "channel_lookback")
    if any(type(getattr(config, key)) is not int or getattr(config, key) <= 0 for key in periods):
        raise ValueError("Strategy periods must be positive integers")
    positive = ("atr_stop", "reward_risk", "bollinger_std", "supertrend_multiplier",
                "volume_ratio_min", "atr_expansion_min", "sr_tolerance_atr")
    if any(isinstance(getattr(config, key), bool)
           or not isinstance(getattr(config, key), (int, float))
           or not math.isfinite(getattr(config, key)) or getattr(config, key) <= 0
           for key in positive):
        raise ValueError("Strategy periods, multipliers, stop, and reward/risk must be positive")
    if config.fast_ema >= config.slow_ema:
        raise ValueError("fast_ema must be smaller than slow_ema")
    if config.sr_lookback < config.pivot_span * 2 + 1:
        raise ValueError("sr_lookback must contain both sides of a pivot")
    if config.min_confirmations > 6:
        raise ValueError("min_confirmations cannot exceed available indicators")
    bounded = (config.adx_min, config.rsi_long_min, config.rsi_long_max)
    if any(isinstance(value, bool) or not isinstance(value, (int, float))
           or not math.isfinite(value) for value in bounded):
        raise ValueError("Strategy thresholds must be finite numbers")
    if config.adx_min < 0:
        raise ValueError("ADX thresholds cannot be negative")
    if not 0 <= config.rsi_long_min < config.rsi_long_max <= 100:
        raise ValueError("RSI thresholds must be ordered within 0..100")
    return config


def load_promoted_policy(path: str = "strategy_policy.json") -> StrategyConfig | None:
    """Fail closed: no deterministic gate until a walk-forward run promotes one."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as policy_file:
            return config_from_dict(json.load(policy_file))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None