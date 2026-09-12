"""Candidate validation and deterministic same-pair conflict resolution."""
from __future__ import annotations

from datetime import datetime

from scalping_core import CandidateStatus, CandidateSignal, Decision


def resolve_candidates(candidates: list[CandidateSignal], seen_fingerprints: set[str], now: datetime,
                       min_score: int = 65, min_reward_risk: float = 1.3) -> list[Decision]:
    decisions: list[Decision] = []
    eligible: list[CandidateSignal] = []
    for candidate in candidates:
        error = candidate.validation_error(now, min_reward_risk)
        if error:
            decisions.append(Decision(candidate, CandidateStatus.REJECTED, error))
        elif candidate.score < min_score:
            decisions.append(Decision(candidate, CandidateStatus.REJECTED, "quality score below threshold"))
        elif candidate.fingerprint in seen_fingerprints:
            decisions.append(Decision(candidate, CandidateStatus.BLOCKED_BY_CONFLICT, "duplicate candle fingerprint"))
        else:
            eligible.append(candidate)
    winners: dict[str, CandidateSignal] = {}
    for candidate in eligible:
        current = winners.get(candidate.pair)
        if current is None or (candidate.score, candidate.reward_risk, candidate.strategy) > \
                (current.score, current.reward_risk, current.strategy):
            winners[candidate.pair] = candidate
    for candidate in eligible:
        if winners[candidate.pair] is candidate:
            decisions.append(Decision(candidate, CandidateStatus.ACCEPTED_PAPER))
        else:
            decisions.append(Decision(candidate, CandidateStatus.BLOCKED_BY_CONFLICT,
                                      "higher-quality candidate selected for pair"))
    return decisions