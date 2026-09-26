"""Domain models: raw record contract, feature availability, events, verdicts."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum

SCHEMA_VERSION = 1
MARKETS = {"perpetual", "spot"}


class Availability(StrEnum):
    HISTORICAL_AND_LIVE = "HISTORICAL_AND_LIVE"
    LIVE_ONLY = "LIVE_ONLY"
    HISTORICAL_PROXY = "HISTORICAL_PROXY"
    UNAVAILABLE = "UNAVAILABLE"


class Verdict(StrEnum):
    """The only three outcomes a research run may report."""
    REJECTED = "REJECTED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    ELIGIBLE_FOR_SHADOW = "ELIGIBLE_FOR_SHADOW"


def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def content_hash(value) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


@dataclass(frozen=True)
class RawRecord:
    """One observation with its provenance.

    event_time_ms: when it happened in the market.
    available_time_ms: the earliest moment a decision system could have known it.
    received_time_ms: when this lab obtained it.
    A backtest may only use a record whose available_time_ms <= decision time.
    """
    exchange: str
    market: str
    pair: str
    event_time_ms: int
    available_time_ms: int
    received_time_ms: int
    source: str
    values: dict
    schema_version: int = SCHEMA_VERSION

    def validation_error(self) -> str | None:
        if self.market not in MARKETS:
            return f"unknown market {self.market!r}"
        if not self.exchange or not self.pair or not self.source:
            return "missing exchange, pair or source"
        for name in ("event_time_ms", "available_time_ms", "received_time_ms"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                return f"{name} must be a non-negative integer"
        if self.available_time_ms < self.event_time_ms:
            return "available_time_ms precedes event_time_ms"
        if not isinstance(self.values, dict):
            return "values must be a dict"
        if self.schema_version != SCHEMA_VERSION:
            return f"schema_version {self.schema_version} != {SCHEMA_VERSION}"
        return None

    def visible_at(self, decision_time_ms: int) -> bool:
        return self.available_time_ms <= decision_time_ms


def point_in_time(records: list[RawRecord], decision_time_ms: int) -> list[RawRecord]:
    """Only what was knowable at the decision time."""
    return [r for r in records if r.visible_at(decision_time_ms)]


@dataclass(frozen=True)
class MarketEvent:
    """A market state under study - not a trade."""
    event_id: str
    hypothesis_id: str
    pair: str
    direction: str
    event_time_ms: int
    decision_time_ms: int
    reference_price: float
    feature_version: str
    features: dict
    data_quality: dict

    @property
    def fingerprint(self) -> str:
        return content_hash([self.hypothesis_id, self.feature_version, self.pair, self.direction,
                             self.decision_time_ms])[:24]
