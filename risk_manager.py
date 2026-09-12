"""Central paper-risk limits; leverage never changes stop-loss risk."""
from __future__ import annotations

from dataclasses import dataclass
import os

from scalping_core import CandidateSignal, RiskDecision


@dataclass(frozen=True)
class RiskConfig:
    risk_usdt_per_trade: float = 0.75
    max_daily_loss_usdt: float = 2.0
    max_daily_trades: int = 0  # zero means unlimited
    max_open_positions: int = 0  # zero means unlimited
    min_reward_risk: float = 1.3


def config_from_environment() -> RiskConfig:
    """Validate deployment configuration once per scan; invalid values fail closed."""
    try:
        config = RiskConfig(
            risk_usdt_per_trade=float(os.environ.get("RISK_USDT_PER_TRADE", "0.75")),
            max_daily_loss_usdt=float(os.environ.get("MAX_DAILY_LOSS_USDT", "2.00")),
            max_daily_trades=int(os.environ.get("MAX_DAILY_TRADES", "0")),
            max_open_positions=int(os.environ.get("MAX_OPEN_POSITIONS", "0")),
            min_reward_risk=float(os.environ.get("MIN_REWARD_RISK", "1.3")),
        )
    except ValueError as exc:
        raise ValueError("invalid multi-strategy risk configuration") from exc
    if (config.risk_usdt_per_trade <= 0 or config.max_daily_loss_usdt <= 0 or
            config.max_daily_trades < 0 or config.max_open_positions < 0 or config.min_reward_risk < 1.3):
        raise ValueError("unsafe multi-strategy risk configuration")
    return config


def assess_risk(candidate: CandidateSignal, *, open_positions: int, daily_trades: int,
                daily_realized_pnl: float, config: RiskConfig = RiskConfig()) -> RiskDecision:
    if config.max_open_positions and open_positions >= config.max_open_positions:
        return RiskDecision(False, "maximum open positions reached")
    if config.max_daily_trades and daily_trades >= config.max_daily_trades:
        return RiskDecision(False, "daily trade limit reached")
    if daily_realized_pnl <= -config.max_daily_loss_usdt:
        return RiskDecision(False, "daily loss limit reached")
    if candidate.reward_risk < config.min_reward_risk:
        return RiskDecision(False, "reward/risk below minimum")
    distance = abs(candidate.entry_price - candidate.stop_price)
    if distance <= 0:
        return RiskDecision(False, "invalid stop distance")
    quantity = config.risk_usdt_per_trade / distance
    return RiskDecision(True, risk_usdt=config.risk_usdt_per_trade, quantity=quantity)