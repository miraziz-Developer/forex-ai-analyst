"""Shared, deterministic domain objects for the paper-only multi-strategy bot."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import json


class Direction(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class MarketRegime(StrEnum):
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGING = "RANGING"
    BREAKOUT_READY = "BREAKOUT_READY"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    UNCERTAIN = "UNCERTAIN"


class CandidateStatus(StrEnum):
    CANDIDATE = "CANDIDATE"
    REJECTED = "REJECTED"
    BLOCKED_BY_RISK = "BLOCKED_BY_RISK"
    BLOCKED_BY_CONFLICT = "BLOCKED_BY_CONFLICT"
    ACCEPTED_PAPER = "ACCEPTED_PAPER"
    WIN = "WIN"
    LOSS = "LOSS"
    TIME_EXIT = "TIME_EXIT"


@dataclass(frozen=True)
class CandidateSignal:
    strategy: str
    pair: str
    direction: Direction
    regime: MarketRegime
    entry_price: float
    stop_price: float
    target_price: float
    expires_at: datetime
    signal_timeframe: str
    trend_timeframe: str
    candle_time_ms: int
    score: int
    confirmations: tuple[str, ...]
    invalidation_reason: str
    features: dict[str, float | str] = field(default_factory=dict)

    @property
    def reward_risk(self) -> float:
        risk = abs(self.entry_price - self.stop_price)
        return abs(self.target_price - self.entry_price) / risk if risk else 0.0

    @property
    def fingerprint(self) -> str:
        payload = json.dumps({"strategy": self.strategy, "pair": self.pair,
                              "direction": self.direction, "candle": self.candle_time_ms},
                             sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:24]

    def validation_error(self, now: datetime | None = None, min_reward_risk: float = 1.3) -> str | None:
        now = now or datetime.now(timezone.utc)
        if not self.pair or self.score < 0 or self.score > 100:
            return "invalid identity or score"
        if self.expires_at.tzinfo is None or self.expires_at <= now:
            return "expired candidate"
        if min(self.entry_price, self.stop_price, self.target_price) <= 0:
            return "non-positive price"
        valid_levels = (self.stop_price < self.entry_price < self.target_price
                        if self.direction is Direction.BUY else
                        self.target_price < self.entry_price < self.stop_price)
        if not valid_levels:
            return "invalid direction levels"
        if self.reward_risk < min_reward_risk:
            return "reward/risk below minimum"
        return None


@dataclass(frozen=True)
class Decision:
    candidate: CandidateSignal
    status: CandidateStatus
    reason: str | None = None

    @property
    def accepted(self) -> bool:
        return self.status is CandidateStatus.ACCEPTED_PAPER


@dataclass(frozen=True)
class RiskDecision:
    accepted: bool
    reason: str | None = None
    risk_usdt: float = 0.0
    quantity: float = 0.0