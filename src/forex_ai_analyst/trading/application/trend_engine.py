"""Live 4h Donchian breakout (long only): the research lab's best walk-forward family.

Signals come from the exact function the lab backtested
(``forex_ai_analyst.lab.strategies.donchian``), so live and research logic
cannot drift apart. Defaults are the latest walk-forward window's selection in
docs/LAB_REPORT.md. The strategy has no fixed profit target: a trade leaves on
its initial ATR stop (exchange-side) or when a 4h close breaks the exit
channel.

There is no LLM in the trade path: every entry, stop, size and exit is a
deterministic rule, so live behaviour is exactly what was backtested.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from forex_ai_analyst.lab.strategies import donchian

STRATEGY = "donchian_4h"
TIMEFRAME = "4h"


@dataclass(frozen=True)
class TrendParams:
    entry_n: int = 100
    exit_n: int = 20
    stop_atr: float = 3.0
    leverage: int = 3

    @property
    def history_bars(self) -> int:
        return self.entry_n + 60


def params_from_environment() -> TrendParams:
    def number(name, default, cast):
        try:
            return cast(os.environ.get(name, str(default)))
        except ValueError:
            return default
    return TrendParams(entry_n=number("DONCHIAN_ENTRY_N", 100, int), exit_n=number("DONCHIAN_EXIT_N", 20, int),
                       stop_atr=number("DONCHIAN_STOP_ATR", 3.0, float), leverage=number("DONCHIAN_LEVERAGE", 3, int))


def entry_signal(bars: list[dict], params: TrendParams) -> dict | None:
    """Breakout on the newest closed 4h bar, with its ATR stop distance."""
    if len(bars) <= params.entry_n + 1:
        return None
    signals = donchian(bars, entry_n=params.entry_n, exit_n=params.exit_n, stop_atr=params.stop_atr, sides="long")
    distance = signals.stop_distance[-1]
    if not signals.long_entry[-1] or not distance:
        return None
    close = float(bars[-1]["close"])
    return {"candle_time_ms": int(bars[-1]["datetime"]), "entry": close, "stop": close - distance,
            "stop_distance": distance, "channel_high": max(float(b["high"]) for b in bars[-params.entry_n - 1:-1])}


def exit_signal(bars: list[dict], params: TrendParams, entry_candle_ms: int) -> bool:
    """True when a 4h bar closed after entry breaks below the exit channel."""
    if len(bars) <= params.exit_n + 1 or int(bars[-1]["datetime"]) <= int(entry_candle_ms):
        return False
    signals = donchian(bars, entry_n=params.entry_n, exit_n=params.exit_n, stop_atr=params.stop_atr, sides="long")
    return bool(signals.long_exit[-1])

