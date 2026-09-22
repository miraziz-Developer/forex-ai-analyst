"""Fail-closed, configurable quality gates — research/backtest replays only.

Not used by the live contextual-AI scan path (see interfaces/http.py:scan_pair);
QUALITY_MIN_SCORE/QUALITY_MIN_STOP_ATR_MULTIPLE and QUALITY_BLOCKED_PAIRS only
take effect inside research/backtest.py and research/production_backtest.py.
"""
from __future__ import annotations

from dataclasses import dataclass
import os

from forex_ai_analyst.trading.domain.models import CandidateSignal, Direction
from forex_ai_analyst.trading.domain.indicators import ema, values


@dataclass(frozen=True)
class QualityPolicy:
    """Independent filters that prevent low-quality/cost-inefficient entries.

    A zero minimum stop ATR disables only that one optional check.  All other
    enabled checks fail closed when their required history is unavailable.
    """
    min_score: int = 75
    require_1h_alignment: bool = True
    min_stop_atr_multiple: float = 0.8
    blocked_pairs: frozenset[str] = frozenset({"XRP-USDT"})
    allow_buy: bool = True
    allow_sell: bool = False


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    if value.strip().lower() not in {"true", "false"}:
        raise ValueError(f"{name} must be true or false")
    return value.strip().lower() == "true"


def quality_policy_from_environment() -> QualityPolicy:
    try:
        blocked = frozenset(item.strip().upper() for item in os.environ.get(
            "QUALITY_BLOCKED_PAIRS", "XRP-USDT").split(",") if item.strip())
        policy = QualityPolicy(
            min_score=int(os.environ.get("QUALITY_MIN_SCORE", "75")),
            require_1h_alignment=_env_bool("QUALITY_REQUIRE_1H_ALIGNMENT", True),
            min_stop_atr_multiple=float(os.environ.get("QUALITY_MIN_STOP_ATR_MULTIPLE", "0.8")),
            blocked_pairs=blocked,
            allow_buy=_env_bool("QUALITY_ALLOW_BUY", True),
            allow_sell=_env_bool("QUALITY_ALLOW_SELL", False),
        )
    except ValueError as exc:
        raise ValueError("invalid signal quality configuration") from exc
    if not 0 <= policy.min_score <= 100 or policy.min_stop_atr_multiple < 0:
        raise ValueError("unsafe signal quality configuration")
    if not policy.allow_buy and not policy.allow_sell:
        raise ValueError("signal quality configuration blocks every direction")
    return policy


def quality_rejection_reason(candidate: CandidateSignal, bars_1h: list[dict],
                             policy: QualityPolicy = QualityPolicy()) -> str | None:
    """Return a reason for rejection, otherwise ``None``.

    1h alignment is deliberately based only on fully closed bars supplied by
    the caller.  A 50/200 EMA trend is used rather than a single price cross.
    """
    if candidate.pair.upper() in policy.blocked_pairs:
        return "pair blocked by quality policy"
    if candidate.score < policy.min_score:
        return "quality score below policy threshold"
    if candidate.direction is Direction.BUY and not policy.allow_buy:
        return "buy direction blocked by quality policy"
    if candidate.direction is Direction.SELL and not policy.allow_sell:
        return "sell direction blocked by quality policy"
    atr_5m = candidate.features.get("atr_5m")
    try:
        stop_distance = abs(candidate.entry_price - candidate.stop_price)
        if policy.min_stop_atr_multiple and (atr_5m is None or float(atr_5m) <= 0 or
                                             stop_distance < float(atr_5m) * policy.min_stop_atr_multiple):
            return "stop distance below ATR quality minimum"
    except (TypeError, ValueError):
        return "invalid ATR quality feature"
    if not policy.require_1h_alignment:
        return None
    if len(bars_1h) < 200:
        return "insufficient closed 1h history"
    closes = values(bars_1h)
    ema50, ema200 = ema(closes, 50), ema(closes, 200)
    if ema50 is None or ema200 is None:
        return "1h trend indicators unavailable"
    bullish = ema50 > ema200 and closes[-1] > ema200
    bearish = ema50 < ema200 and closes[-1] < ema200
    if candidate.direction is Direction.BUY and not bullish:
        return "buy conflicts with 1h trend"
    if candidate.direction is Direction.SELL and not bearish:
        return "sell conflicts with 1h trend"
    return None