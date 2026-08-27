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
