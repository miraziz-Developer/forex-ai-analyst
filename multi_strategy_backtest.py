"""Deterministic, closed-bar paper replay for multi-strategy candidates.

The evaluator receives only bars closed through `index`, preventing look-ahead.
Entries are always the following bar's open; an ambiguous stop/target candle is a loss.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from scalping_core import CandidateSignal, Direction
from risk_manager import RiskConfig, assess_risk


@dataclass(frozen=True)
class BacktestCosts:
    fee_pct_round_trip: float = 0.10
    slippage_pct_round_trip: float = 0.02
    funding_pct_per_8h: float = 0.0

    def validate(self) -> None:
        if min(self.fee_pct_round_trip, self.slippage_pct_round_trip, self.funding_pct_per_8h) < 0:
            raise ValueError("backtest costs cannot be negative")


def _exit(direction: Direction, entry: float, stop: float, target: float, bars: list[dict], expiry_bars: int) -> tuple[str, float, int]:
    for offset, bar in enumerate(bars[:expiry_bars], start=1):
        stop_hit = float(bar["low"]) <= stop if direction is Direction.BUY else float(bar["high"]) >= stop
        target_hit = float(bar["high"]) >= target if direction is Direction.BUY else float(bar["low"]) <= target
        if stop_hit:  # conservative when both levels touch in this OHLC bar
            return "LOSS", stop, offset
        if target_hit:
            return "WIN", target, offset
    if not bars:
        return "EXPIRED", entry, 0
    return "EXPIRED", float(bars[min(len(bars), expiry_bars) - 1]["close"]), min(len(bars), expiry_bars)


def simulate(bars: list[dict], evaluator: Callable[[list[dict], datetime], list[CandidateSignal]], *,
             warmup_bars: int = 260, expiry_bars: int = 12, risk_config: RiskConfig = RiskConfig(),
             costs: BacktestCosts = BacktestCosts()) -> list[dict]:
    """Replay chronological bars with central limits, no overlapping paper positions."""
    if warmup_bars < 1 or expiry_bars < 1:
        raise ValueError("warmup_bars and expiry_bars must be positive")
    costs.validate()
    chronological = sorted(bars, key=lambda item: int(item["datetime"]))
    trades, daily_count, daily_pnl = [], defaultdict(int), defaultdict(float)
    busy_until = -1
    for index in range(warmup_bars, len(chronological) - 1):
        if index <= busy_until:
            continue
        now = datetime.fromtimestamp(int(chronological[index]["datetime"]) / 1000, timezone.utc)
        candidates = evaluator(chronological[:index + 1], now)
        if not candidates:
            continue
        # Evaluator must return validated/candidate-ranked signals; replay still validates risk.
        candidate = max(candidates, key=lambda item: (item.score, item.reward_risk, item.strategy))
        day = now.date().isoformat()
        risk = assess_risk(candidate, open_positions=0, daily_trades=daily_count[day],
                           daily_realized_pnl=daily_pnl[day], config=risk_config)
        if not risk.accepted:
            continue
        next_bar = chronological[index + 1]
        raw_entry = float(next_bar["open"])
        entry = raw_entry * (1 + costs.slippage_pct_round_trip / 200) if candidate.direction is Direction.BUY else \
            raw_entry * (1 - costs.slippage_pct_round_trip / 200)
        risk_distance = abs(candidate.entry_price - candidate.stop_price)
        if risk_distance <= 0:
            continue
        # Keep candidate's stop distance and target R ratio while applying executable entry slippage.
        stop = entry - risk_distance if candidate.direction is Direction.BUY else entry + risk_distance
        target = entry + risk_distance * candidate.reward_risk if candidate.direction is Direction.BUY else entry - risk_distance * candidate.reward_risk
        outcome, exit_price, held_bars = _exit(candidate.direction, entry, stop, target, chronological[index + 1:], expiry_bars)
        quantity = risk.risk_usdt / risk_distance
        gross = (exit_price - entry) * quantity if candidate.direction is Direction.BUY else (entry - exit_price) * quantity
        notional = entry * quantity
        fees = notional * costs.fee_pct_round_trip / 100
        funding = notional * costs.funding_pct_per_8h / 100 * (held_bars * 5 / 60 / 8)
        net = gross - fees - funding
        daily_count[day] += 1
        daily_pnl[day] += net
        trades.append({"time": int(chronological[index]["datetime"]), "pair": candidate.pair,
                       "strategy": candidate.strategy, "regime": candidate.regime, "direction": candidate.direction,
                       "outcome": outcome, "entry": entry, "exit": exit_price, "gross_pnl_usdt": gross,
                       "net_pnl_usdt": net, "fees_usdt": fees, "funding_usdt": funding, "held_bars": held_bars})
        busy_until = index + held_bars
    return trades


def metrics(trades: list[dict]) -> dict:
    decided = [trade for trade in trades if trade["outcome"] in {"WIN", "LOSS"}]
    wins, losses = sum(item["outcome"] == "WIN" for item in decided), sum(item["outcome"] == "LOSS" for item in decided)
    net = sum(float(item["net_pnl_usdt"]) for item in trades)
    gross_win = sum(max(float(item["net_pnl_usdt"]), 0) for item in trades)
    gross_loss = -sum(min(float(item["net_pnl_usdt"]), 0) for item in trades)
    equity, peak, drawdown = 0.0, 0.0, 0.0
    for trade in trades:
        equity += float(trade["net_pnl_usdt"])
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    return {"trades": len(trades), "wins": wins, "losses": losses,
            "win_rate_pct": round(100 * wins / (wins + losses), 2) if wins + losses else None,
            "net_pnl_usdt": round(net, 6), "expectancy_usdt": round(net / len(trades), 6) if trades else 0.0,
            "profit_factor": round(gross_win / gross_loss, 6) if gross_loss else None,
            "max_drawdown_usdt": round(drawdown, 6),
            "average_holding_bars": round(sum(item["held_bars"] for item in trades) / len(trades), 2) if trades else 0.0}