"""Single-pair, single-position, bar-by-bar backtest with realistic costs.

Rules that prevent look-ahead:
- signals are computed from bars closed through index i and acted on at the
  open of bar i+1;
- stops/targets are checked intrabar with the stop assumed first when both
  touch in the same candle; a gap through the stop fills at the open;
- funding is charged from the actual historical funding events that occur
  while the position is open.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Costs:
    fee_pct_per_side: float = 0.05      # taker; maker is ~0.02
    slippage_pct_per_side: float = 0.02
    charge_funding: bool = True


@dataclass(frozen=True)
class Sizing:
    risk_per_trade: float = 0.01        # fraction of equity lost if the initial stop is hit
    max_leverage: float = 3.0           # notional cap as a multiple of equity


@dataclass
class Signals:
    long_entry: list[bool]
    short_entry: list[bool]
    long_exit: list[bool]
    short_exit: list[bool]
    stop_distance: list[float | None]
    trail_distance: list[float | None] | None = None
    target_distance: list[float | None] | None = None
    max_bars: int | None = None


@dataclass
class _Position:
    direction: int
    entry_index: int
    entry_time: int
    entry: float
    qty: float
    stop: float
    initial_risk: float
    target: float | None
    extreme: float
    fees: float = 0.0
    funding: float = 0.0


@dataclass
class Result:
    trades: list[dict] = field(default_factory=list)
    equity: list[tuple[int, float]] = field(default_factory=list)


def run(bars: list[dict], signals: Signals, funding: list[tuple[int, float]] | None = None,
        costs: Costs = Costs(), sizing: Sizing = Sizing(), start_equity: float = 10_000.0) -> Result:
    n = len(bars)
    fee, slip = costs.fee_pct_per_side / 100, costs.slippage_pct_per_side / 100
    events = funding if (funding and costs.charge_funding) else []
    f_index = 0
    cash = start_equity
    pos: _Position | None = None
    pending_exit = False
    pending_entry = 0
    result = Result()

    def close(i: int, raw_price: float, reason: str) -> None:
        nonlocal cash, pos
        assert pos is not None
        price = raw_price * (1 - slip * pos.direction)
        exit_fee = price * pos.qty * fee
        gross = (price - pos.entry) * pos.qty * pos.direction
        net = gross - pos.fees - exit_fee - pos.funding
        cash += gross - exit_fee - pos.funding
        result.trades.append({
            "entry_time": pos.entry_time, "exit_time": bars[i]["datetime"], "direction": pos.direction,
            "entry": pos.entry, "exit": price, "qty": pos.qty, "net": net, "fees": pos.fees + exit_fee,
            "funding": pos.funding, "r": net / pos.initial_risk if pos.initial_risk else 0.0,
            "bars": i - pos.entry_index + 1, "reason": reason})
        pos = None

    for i in range(n):
        bar = bars[i]
        o, h, l, c = bar["open"], bar["high"], bar["low"], bar["close"]

        if pending_exit and pos is not None:
            close(i, o, "signal")
        pending_exit = False

        if pending_entry and pos is None:
            direction = pending_entry
            distance = signals.stop_distance[i - 1]
            price = o * (1 + slip * direction)
            if distance and distance > 0 and cash > 0:
                qty = min(cash * sizing.risk_per_trade / distance, cash * sizing.max_leverage / price)
                entry_fee = price * qty * fee
                cash -= entry_fee
                target_dist = signals.target_distance[i - 1] if signals.target_distance else None
                pos = _Position(direction, i, bar["datetime"], price, qty, price - direction * distance,
                                qty * distance, price + direction * target_dist if target_dist else None,
                                price, fees=entry_fee)
        pending_entry = 0

        if pos is not None:
            stop_hit = (l <= pos.stop) if pos.direction > 0 else (h >= pos.stop)
            if stop_hit:
                gap = (o <= pos.stop) if pos.direction > 0 else (o >= pos.stop)
                close(i, o if gap else pos.stop, "stop")
            elif pos.target is not None and ((h >= pos.target) if pos.direction > 0 else (l <= pos.target)):
                close(i, pos.target, "target")

        # Funding events up to the next bar's open, charged only while holding.
        bar_end = bars[i + 1]["datetime"] if i + 1 < n else bar["datetime"] + 1
        while f_index < len(events) and events[f_index][0] < bar_end:
            stamp, rate = events[f_index]
            if pos is not None and stamp > pos.entry_time:
                pos.funding += pos.qty * c * rate * pos.direction
            f_index += 1

        if pos is not None:
            pos.extreme = max(pos.extreme, h) if pos.direction > 0 else min(pos.extreme, l)
            trail = signals.trail_distance[i] if signals.trail_distance else None
            if trail:
                candidate = pos.extreme - pos.direction * trail
                pos.stop = max(pos.stop, candidate) if pos.direction > 0 else min(pos.stop, candidate)
            held = i - pos.entry_index + 1
            exit_signal = signals.long_exit[i] if pos.direction > 0 else signals.short_exit[i]
            if i + 1 < n and (exit_signal or (signals.max_bars and held >= signals.max_bars)):
                pending_exit = True
        elif i + 1 < n:
            if signals.long_entry[i]:
                pending_entry = 1
            elif signals.short_entry[i]:
                pending_entry = -1

        unrealized = (c - pos.entry) * pos.qty * pos.direction - pos.funding if pos else 0.0
        result.equity.append((bar["datetime"], cash + unrealized))

    if pos is not None:
        close(n - 1, bars[-1]["close"], "end")
        result.equity[-1] = (bars[-1]["datetime"], cash)
    return result
