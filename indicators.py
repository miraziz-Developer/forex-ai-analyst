def compute_atr(bars: list[dict], period: int = 14) -> float | None:
    """Standard Average True Range. bars must be most-recent-first (our usual
    convention); returns the ATR in absolute price units, or None if there
    isn't enough data. Uses a simple moving average of True Range — the
    textbook Wilder smoothing differs only slightly and isn't worth the
    extra complexity for this use case."""
    if len(bars) < period + 1:
        return None

    chrono = list(reversed(bars))  # oldest first, needed for prev-close comparisons
    true_ranges = []
    for i in range(1, len(chrono)):
        high, low = float(chrono[i]["high"]), float(chrono[i]["low"])
        prev_close = float(chrono[i - 1]["close"])
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)

    if len(true_ranges) < period:
        return None
    return sum(true_ranges[-period:]) / period


def atr_percentile(bars: list[dict], period: int = 14, lookback: int = 100) -> float | None:
    """Where current volatility sits within its own recent history, 0-100.

    Rolls the ATR over the last `lookback` bars and returns the percentile rank
    of the most recent value among them. The point is a regime read, not an
    absolute level: crypto ATR in raw terms is meaningless across pairs and
    across months, but "quieter than 80% of the last 100 bars" means the same
    thing everywhere. Both extremes are bad places to trade — a dead market
    (low percentile) produces chop and fakeouts, and a violent one (high
    percentile) is news-driven and doesn't respect structure.
    """
    if len(bars) < period + lookback + 1:
        return None

    chrono = list(reversed(bars))  # oldest first, needed for prev-close comparisons
    true_ranges = []
    for i in range(1, len(chrono)):
        high, low = float(chrono[i]["high"]), float(chrono[i]["low"])
        prev_close = float(chrono[i - 1]["close"])
        true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))

    if len(true_ranges) < period + lookback:
        return None

    # Rolling ATR series: one value per window position, most recent last.
    atr_series = [
        sum(true_ranges[i - period:i]) / period
        for i in range(period, len(true_ranges) + 1)
    ]
    window = atr_series[-lookback:]
    current = window[-1]
    below = sum(1 for value in window if value < current)
    return below / len(window) * 100


def atr_pct(bars: list[dict], period: int = 14) -> float | None:
    """ATR expressed as a percentage of the most recent close — the unit the
    rest of the app works in (TARGET_PCT/STOP_PCT), so it's directly comparable
    across pairs at wildly different price scales (BTC vs. a small-cap coin)."""
    atr = compute_atr(bars, period)
    if atr is None or not bars:
        return None
    latest_close = float(bars[0]["close"])
    if not latest_close:
        return None
    return atr / latest_close * 100
