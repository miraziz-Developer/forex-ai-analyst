"""Live crypto capitulation rebound, 1h (docs/CRYPTO_ENGINE2_STUDY.md rule B; PF 1.33 on ten unseen coins).

Signals come from the exact function the lab tested (``forex_ai_analyst.lab.engine2_study.capitulation_rebound``):
a 1h close at least 12% below the close 24 hours earlier, on a green hour, buys. The stop (0.5 ATR(14) under
the 24-hour low) and the target (half the fall back) sit on the exchange; after 24 hours the position is
closed at market. Long only, deterministic, no LLM in the trade path.
"""
from __future__ import annotations

import os

from forex_ai_analyst.lab.engine2_study import HOLD, capitulation_rebound

STRATEGY = "crypto_rebound_1h"
TIMEFRAME = "1h"
HOLD_HOURS = HOLD
HISTORY_BARS = 200


# docs/CRYPTO_ENGINE2_STUDY.md (expansion): passed on 32 unseen coins; traded by this engine only, not by Donchian
EXTRA_PAIRS = ("XTZ-USDT,THETA-USDT,VET-USDT,ICP-USDT,MANA-USDT,GALA-USDT,CHZ-USDT,ENJ-USDT,KSM-USDT,COMP-USDT,"
               "SNX-USDT,1INCH-USDT,SUSHI-USDT,ZIL-USDT,IOTA-USDT,NEO-USDT,QTUM-USDT,ONT-USDT,BAT-USDT,ZRX-USDT,"
               "KAVA-USDT,RUNE-USDT,EGLD-USDT,HBAR-USDT,GRT-USDT,CELO-USDT,SKL-USDT,ANKR-USDT,CTSI-USDT,DYDX-USDT,"
               "ENS-USDT,APE-USDT")


def extra_pairs() -> tuple[str, ...]:
    raw = os.environ.get("REBOUND_EXTRA_PAIRS", EXTRA_PAIRS)
    return tuple(dict.fromkeys(p.strip().upper() for p in raw.split(",") if p.strip()))


def max_open() -> int:
    """Crashes hit many coins at once; this caps the engine's open positions inside the bot's overall cap."""
    try:
        return int(os.environ.get("REBOUND_MAX_OPEN", "5"))
    except ValueError:
        return 5


def leverage() -> int:
    try:
        return int(os.environ.get("REBOUND_LEVERAGE", "3"))
    except ValueError:
        return 3


def enabled() -> bool:
    return os.environ.get("REBOUND_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}


def entry_signal(bars: list[dict]) -> dict | None:
    """A rebound buy on the newest closed 1h bar, with its stop and target distances."""
    if len(bars) < 100:
        return None
    sig = capitulation_rebound(bars)
    last = len(bars) - 1
    if not sig.long_entry[last] or not sig.stop_distance[last] or not sig.target_distance[last]:
        return None
    close = float(bars[-1]["close"])
    return {"candle_time_ms": int(bars[-1]["datetime"]), "entry": close, "close": close,
            "stop": close - sig.stop_distance[last], "stop_distance": sig.stop_distance[last],
            "target": close + sig.target_distance[last], "target_distance": sig.target_distance[last],
            "fall": 1 - close / float(bars[-25]["close"])}
