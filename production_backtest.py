"""Replay deployed 15m-regime/5m-entry logic with public perpetual OHLCV archives.

Live data remains BingX. Binance's public USDT-M perpetual archive is used only
for reproducible long-horizon research because BingX REST history is too short.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import zipfile
from bisect import bisect_right
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from market_regime import classify_market_regime
from quality_policy import QualityPolicy, quality_rejection_reason
from risk_manager import RiskConfig, assess_risk
from strategy_coordinator import resolve_candidates
from strategies import breakout_retest, support_resistance_rejection, trend_pullback

FIVE_MINUTES_MS = 5 * 60 * 1000
ARCHIVE_URL = "https://data.binance.vision/data/futures/um/monthly/klines/{symbol}/5m/{symbol}-5m-{month}.zip"
CACHE_DIR = Path(".backtest_cache")


def month_starts(start: datetime, end: datetime) -> list[datetime]:
    cursor = start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    result = []
    while cursor < end:
        result.append(cursor)
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
    return result


def walk_forward_periods(start: datetime, days: int, windows: int) -> list[tuple[datetime, datetime]]:
    """Return equal, contiguous out-of-sample replay periods.

    Each period is intentionally independent: callers must reset account state
    between periods instead of allowing a prior period's equity curve to affect
    a later result.  Requiring exact division prevents an implicit partial
    final window from receiving less evidence than the others.
    """
    if days <= 0 or windows <= 0 or days % windows:
        raise ValueError("days must be positive and evenly divisible by windows")
    width = timedelta(days=days // windows)
    return [(start + index * width, start + (index + 1) * width) for index in range(windows)]


def build_period_report(pairs: list[str], start: datetime, end: datetime, config: RiskConfig, fee_pct: float,
                        slippage_pct: float, policy: QualityPolicy, starting_equity: float) -> dict:
    """Replay one independent account period and return its complete audit report."""
    warmup = start - timedelta(days=10)
    all_trades, rejections = [], defaultdict(int)
    for pair in pairs:
        bars = archive_bars(pair, warmup, end)
        trades, blocked = replay_pair(pair, bars, int(start.timestamp() * 1000), config, fee_pct, slippage_pct, policy)
        all_trades.extend(trades)
        for reason, count in blocked.items():
            rejections[reason] += count
        print(pair, metrics(trades, starting_equity), "candidates rejected", blocked)
    all_trades, account_blocked = apply_account_limits(all_trades, config, starting_equity)
    for reason, count in account_blocked.items():
        rejections[reason] += count
    selectors = {"pair": lambda trade: trade["pair"], "strategy": lambda trade: trade["strategy"],
                 "direction": lambda trade: trade["direction"],
                 "month": lambda trade: datetime.fromtimestamp(trade["time"] / 1000, timezone.utc).strftime("%Y-%m")}
    breakdowns = {key: {value: metrics([trade for trade in all_trades if selector(trade) == value], starting_equity)
                        for value in sorted({selector(trade) for trade in all_trades})}
                  for key, selector in selectors.items()}
    return {"period_utc": [start.isoformat(), end.isoformat()], "all": metrics(all_trades, starting_equity),
            "rejections": dict(rejections), "breakdowns": breakdowns, "trades": all_trades}


def archive_bars(pair: str, start: datetime, end: datetime) -> list[dict]:
    """Download/cache complete monthly 5m USDT-M perpetual candles for [start, end)."""
    symbol, rows = pair.replace("-", ""), {}
    CACHE_DIR.mkdir(exist_ok=True)
    for month in month_starts(start, end):
        name = f"{symbol}-5m-{month:%Y-%m}.zip"
        path = CACHE_DIR / name
        if not path.exists():
            response = requests.get(ARCHIVE_URL.format(symbol=symbol, month=f"{month:%Y-%m}"), timeout=90)
            if response.status_code == 404:
                raise RuntimeError(f"Archive unavailable: {name}. Select a completed historical month.")
            response.raise_for_status()
            path.write_bytes(response.content)
        with zipfile.ZipFile(path) as archive:
            csv_name = next(item for item in archive.namelist() if item.endswith(".csv"))
            reader = csv.reader(io.TextIOWrapper(archive.open(csv_name), encoding="utf-8"))
            for row in reader:
                if row and row[0].isdigit():
                    stamp = int(row[0])
                    if int(start.timestamp() * 1000) <= stamp < int(end.timestamp() * 1000):
                        rows[stamp] = {"datetime": stamp, "open": float(row[1]), "high": float(row[2]),
                                       "low": float(row[3]), "close": float(row[4]), "volume": float(row[5])}
    result = sorted(rows.values(), key=lambda item: item["datetime"])
    expected = (int(end.timestamp() * 1000) - int(start.timestamp() * 1000)) // FIVE_MINUTES_MS
    if len(result) != expected or any(b["datetime"] != int(start.timestamp() * 1000) + i * FIVE_MINUTES_MS
                                      for i, b in enumerate(result)):
        raise RuntimeError(f"Archive coverage gap for {pair}: expected {expected} continuous 5m candles, got {len(result)}")
    return result


def aggregate_15m(bars_5m: list[dict]) -> list[dict]:
    result = []
    for index in range(0, len(bars_5m), 3):
        group = bars_5m[index:index + 3]
        if len(group) == 3 and group[0]["datetime"] % (3 * FIVE_MINUTES_MS) == 0:
            result.append({"datetime": group[0]["datetime"], "open": group[0]["open"],
                           "high": max(item["high"] for item in group), "low": min(item["low"] for item in group),
                           "close": group[-1]["close"], "volume": sum(item["volume"] for item in group)})
    return result


def aggregate_1h(bars_5m: list[dict]) -> list[dict]:
    """Build only complete UTC-aligned 1h candles from chronological 5m data."""
    result = []
    for index in range(0, len(bars_5m), 12):
        group = bars_5m[index:index + 12]
        if len(group) == 12 and group[0]["datetime"] % (12 * FIVE_MINUTES_MS) == 0:
            result.append({"datetime": group[0]["datetime"], "open": group[0]["open"],
                           "high": max(item["high"] for item in group), "low": min(item["low"] for item in group),
                           "close": group[-1]["close"], "volume": sum(item["volume"] for item in group)})
    return result


def evaluate_production_signal(pair: str, bars_15m: list[dict], bars_5m: list[dict], now: datetime, regime=None):
    if len(bars_15m) < 260 or len(bars_5m) < 80:
        return None
    regime = regime or classify_market_regime(bars_15m[-260:])
    candidates = [candidate for candidate in (
        trend_pullback.evaluate(pair, bars_15m[-260:], bars_5m[-80:], regime, now),
        support_resistance_rejection.evaluate(pair, bars_15m[-260:], bars_5m[-80:], regime, now),
        breakout_retest.evaluate(pair, bars_15m[-260:], bars_5m[-80:], regime, now),
    ) if candidate is not None]
    accepted = [item.candidate for item in resolve_candidates(candidates, set(), now) if item.accepted]
    return accepted[0] if accepted else None


def replay_pair(pair: str, bars: list[dict], start_ms: int, config: RiskConfig, fee_pct: float,
                slippage_pct: float, policy: QualityPolicy) -> tuple[list[dict], dict[str, int]]:
    bars_15m = aggregate_15m(bars)
    bars_1h = aggregate_1h(bars)
    close_stamps = [bar["datetime"] + 3 * FIVE_MINUTES_MS for bar in bars_15m]
    one_hour_closes = [bar["datetime"] + 12 * FIVE_MINUTES_MS for bar in bars_1h]
    trades, rejected = [], defaultdict(int)
    regimes: dict[int, object] = {}
    for index in range(1, len(bars) - 1):
        signal_close = bars[index]["datetime"] + FIVE_MINUTES_MS
        if signal_close < start_ms:
            continue
        completed = bisect_right(close_stamps, signal_close)
        if completed < 260:
            continue
        if completed not in regimes:
            regimes[completed] = classify_market_regime(bars_15m[completed - 260:completed])
        candidate = evaluate_production_signal(pair, bars_15m[:completed], bars[:index + 1],
                                               datetime.fromtimestamp(signal_close / 1000, timezone.utc), regimes[completed])
        if candidate is None:
            continue
        completed_1h = bisect_right(one_hour_closes, signal_close)
        quality_reason = quality_rejection_reason(candidate, bars_1h[:completed_1h], policy)
        if quality_reason:
            rejected[quality_reason] += 1
            continue
        risk = assess_risk(candidate, open_positions=0, daily_trades=0, daily_realized_pnl=0, config=config)
        if not risk.accepted:
            rejected[risk.reason] += 1
            continue
        buy, entry_bar, slip = candidate.direction.value == "BUY", bars[index + 1], slippage_pct / 200
        entry = entry_bar["open"] * (1 + slip if buy else 1 - slip)
        outcome, exit_price, held = "EXPIRED", None, 0
        for held, bar in enumerate(bars[index + 1:index + 13], 1):
            hit_stop = bar["low"] <= candidate.stop_price if buy else bar["high"] >= candidate.stop_price
            hit_target = bar["high"] >= candidate.target_price if buy else bar["low"] <= candidate.target_price
            if hit_stop:  # conservative when both levels occur within one OHLC bar
                outcome, exit_price = "LOSS", candidate.stop_price * (1 - slip if buy else 1 + slip)
                break
            if hit_target:
                outcome, exit_price = "WIN", candidate.target_price * (1 - slip if buy else 1 + slip)
                break
        if exit_price is None:
            exit_price = bars[min(index + 12, len(bars) - 1)]["close"]
        gross = (exit_price - entry) * risk.quantity if buy else (entry - exit_price) * risk.quantity
        fees = (entry + exit_price) * risk.quantity * fee_pct / 200
        trades.append({"pair": pair, "strategy": candidate.strategy, "direction": candidate.direction.value,
                       "outcome": outcome, "time": candidate.candle_time_ms, "net_pnl_usdt": gross - fees,
                       "gross_pnl_usdt": gross, "fees_usdt": fees, "held_bars": held,
                       "entry_time": entry_bar["datetime"], "exit_time": bars[min(index + held, len(bars) - 1)]["datetime"]})
    return trades, dict(rejected)


def apply_account_limits(candidates: list[dict], config: RiskConfig, starting_equity: float) -> tuple[list[dict], dict[str, int]]:
    """Accept chronological candidates with production daily/concurrency limits.

    PnL becomes available only after the recorded exit time. This deliberately
    rejects overlapping entries when the account has no free position slot.
    """
    accepted, rejected, open_trades = [], defaultdict(int), []
    daily_count, daily_pnl = defaultdict(int), defaultdict(float)
    equity = starting_equity
    for trade in sorted(candidates, key=lambda item: (item["entry_time"], item["pair"], item["strategy"])):
        for closed in [item for item in open_trades if item["exit_time"] <= trade["entry_time"]]:
            open_trades.remove(closed)
            equity += closed["net_pnl_usdt"]
            daily_pnl[datetime.fromtimestamp(closed["exit_time"] / 1000, timezone.utc).date().isoformat()] += closed["net_pnl_usdt"]
        day = datetime.fromtimestamp(trade["entry_time"] / 1000, timezone.utc).date().isoformat()
        if config.max_open_positions and len(open_trades) >= config.max_open_positions:
            rejected["maximum open positions reached"] += 1
            continue
        if config.max_daily_trades and daily_count[day] >= config.max_daily_trades:
            rejected["daily trade limit reached"] += 1
            continue
        if daily_pnl[day] <= -config.max_daily_loss_usdt:
            rejected["daily loss limit reached"] += 1
            continue
        daily_count[day] += 1
        trade["equity_before_usdt"] = round(equity, 4)
        accepted.append(trade)
        open_trades.append(trade)
    for closed in sorted(open_trades, key=lambda item: item["exit_time"]):
        equity += closed["net_pnl_usdt"]
    return accepted, dict(rejected)


def metrics(trades: list[dict], starting_equity: float = 0.0) -> dict:
    decided = [item for item in trades if item["outcome"] != "EXPIRED"]
    net = sum(item["net_pnl_usdt"] for item in trades)
    gains = sum(max(item["net_pnl_usdt"], 0) for item in trades)
    losses = -sum(min(item["net_pnl_usdt"], 0) for item in trades)
    equity = peak = starting_equity
    drawdown = 0.0
    for item in sorted(trades, key=lambda item: item.get("exit_time", item["time"])):
        equity += item["net_pnl_usdt"]
        peak, drawdown = max(peak, equity), min(drawdown, equity - peak)
    return {"trades": len(trades), "wins": sum(item["outcome"] == "WIN" for item in decided),
            "losses": sum(item["outcome"] == "LOSS" for item in decided), "expired": len(trades) - len(decided),
            "net_usdt": round(net, 4), "gross_usdt": round(sum(item["gross_pnl_usdt"] for item in trades), 4),
            "fees_usdt": round(sum(item["fees_usdt"] for item in trades), 4),
            "win_rate_pct": round(100 * sum(item["outcome"] == "WIN" for item in decided) / len(decided), 2) if decided else None,
            "expectancy_usdt": round(net / len(trades), 4) if trades else None,
            "profit_factor": round(gains / losses, 3) if losses else None, "max_drawdown_usdt": round(drawdown, 4),
            "ending_equity_usdt": round(starting_equity + net, 4) if starting_equity else None}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2026-06-01", help="UTC inclusive date; archive months must be complete")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--pairs", default="BTC-USDT,ETH-USDT,SOL-USDT,XRP-USDT,BNB-USDT")
    parser.add_argument("--risk-usdt", type=float, default=.50)
    parser.add_argument("--max-daily-loss-usdt", type=float, default=1.5)
    parser.add_argument("--max-daily-trades", type=int, default=4)
    parser.add_argument("--max-open-positions", type=int, default=1)
    parser.add_argument("--max-notional-usdt", type=float, default=75)
    parser.add_argument("--max-fee-to-risk-ratio", type=float, default=.15)
    parser.add_argument("--fee-pct-round-trip", type=float, default=.10)
    parser.add_argument("--slippage-pct-round-trip", type=float, default=.02)
    parser.add_argument("--starting-equity-usdt", type=float, default=100)
    parser.add_argument("--min-score", type=int, default=75)
    parser.add_argument("--min-stop-atr-multiple", type=float, default=.8)
    parser.add_argument("--allow-sell", action="store_true")
    parser.add_argument("--blocked-pairs", default="XRP-USDT")
    parser.add_argument("--walk-forward-windows", type=int, default=1,
                        help="Split the requested period into equal independent replay windows.")
    parser.add_argument("--output", default="backtest_results.json")
    args = parser.parse_args()
    if args.walk_forward_windows < 1 or args.days % args.walk_forward_windows:
        parser.error("--walk-forward-windows must be positive and divide --days exactly")
    start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    end = start + timedelta(days=args.days)
    config = RiskConfig(risk_usdt_per_trade=args.risk_usdt, max_daily_loss_usdt=args.max_daily_loss_usdt,
                        max_daily_trades=args.max_daily_trades, max_open_positions=args.max_open_positions,
                        max_position_notional_usdt=args.max_notional_usdt,
                        max_fee_to_risk_ratio=args.max_fee_to_risk_ratio,
                        estimated_fee_pct_round_trip=args.fee_pct_round_trip)
    policy = QualityPolicy(min_score=args.min_score, min_stop_atr_multiple=args.min_stop_atr_multiple,
                           blocked_pairs=frozenset(item.strip().upper() for item in args.blocked_pairs.split(",") if item.strip()),
                           allow_sell=args.allow_sell)
    pairs = [item.strip().upper() for item in args.pairs.split(",") if item.strip()]
    periods = walk_forward_periods(start, args.days, args.walk_forward_windows)
    window_reports = [build_period_report(pairs, window_start, window_end, config, args.fee_pct_round_trip,
                                          args.slippage_pct_round_trip, policy, args.starting_equity_usdt)
                      for window_start, window_end in periods]
    report = window_reports[0] if len(window_reports) == 1 else {
        "period_utc": [start.isoformat(), end.isoformat()],
        "walk_forward_windows": window_reports,
        "promotion_eligible": all(window["all"]["net_usdt"] > 0 and (window["all"]["profit_factor"] or 0) > 1
                                  for window in window_reports),
    }
    report["config"] = {**vars(args), "risk_limits": config.__dict__,
                        "quality_policy": {**policy.__dict__, "blocked_pairs": sorted(policy.blocked_pairs)}}
    Path(args.output).write_text(json.dumps(report, indent=2), encoding="utf-8")
    if len(window_reports) == 1:
        print("ALL", report["all"], "rejected", report["rejections"], "report", args.output)
    else:
        print("WALK FORWARD", [window["all"] for window in window_reports],
              "promotion eligible", report["promotion_eligible"], "report", args.output)


if __name__ == "__main__":
    main()