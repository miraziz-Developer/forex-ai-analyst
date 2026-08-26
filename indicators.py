def ema(values: list[float], length: int) -> list[float]:
    if not values:
        return []
    k = 2 / (length + 1)
    result = [values[0]]
    for price in values[1:]:
        result.append(price * k + result[-1] * (1 - k))
    return result


def detect_crossover(bars: list[dict], length: int) -> str | None:
    """bars must be most-recent-first (as returned by Twelve Data). Returns 'up', 'down', or None."""
    closes = [float(b["close"]) for b in reversed(bars)]
    if len(closes) < length + 2:
        return None

    ema_values = ema(closes, length)
    prev_diff = closes[-2] - ema_values[-2]
    curr_diff = closes[-1] - ema_values[-1]

    if prev_diff <= 0 < curr_diff:
        return "up"
    if prev_diff >= 0 > curr_diff:
        return "down"
    return None
