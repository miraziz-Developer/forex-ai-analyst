"""Walk-forward optimizer for deterministic strategies (no paid LLM calls).

Selection: oldest train data only.  The following 30 days are validation and
the newest 90 days are a final untouched test.  A policy is never promoted
solely for win rate: it must also be profitable after estimated taker fees.
"""

import argparse
import itertools
import json
import os
import time
from dataclasses import asdict, replace

from market_data import fetch_bars
from strategy_engine import StrategyConfig, candidate_levels, ema, evaluate_candidate

FOUR_HOURS_MS = 4 * 3600 * 1000
DAY_MS = 24 * 3600 * 1000


def aggregate_daily(closed_4h: list[dict]) -> list[dict]:
    buckets = {}
    for bar in reversed(closed_4h):
        day = int(bar["datetime"]) // DAY_MS
        item = buckets.setdefault(day, {"datetime": day * DAY_MS, "open": float(bar["open"]),
                                        "high": float(bar["high"]), "low": float(bar["low"]),
                                        "close": float(bar["close"]), "volume": 0.0})
        item["high"] = max(item["high"], float(bar["high"]))
        item["low"] = min(item["low"], float(bar["low"]))
        item["close"] = float(bar["close"])
        item["volume"] += float(bar["volume"])
    return list(reversed(list(buckets.values())))


def _resolve(direction, entry, target, stop, forward):
    for index, bar in enumerate(forward):
        high, low = float(bar["high"]), float(bar["low"])
        target_hit = high >= target if direction == "BUY" else low <= target
        stop_hit = low <= stop if direction == "BUY" else high >= stop
        if stop_hit:
            return "LOSS", -1.0, index
        if target_hit:
            return "WIN", None, index
    if not forward:
        return "EXPIRED", 0.0, -1
    exit_price = float(forward[-1]["close"])
    risk = abs(entry - stop)
    signed_move = exit_price - entry if direction == "BUY" else entry - exit_price
    return "EXPIRED", signed_move / risk if risk else 0.0, len(forward) - 1


def _resolve_trend_exit(direction, entry, initial_stop, forward, chronological,
                        entry_index, config, signal_atr):
    """Resolve trend trades with a next-bar-safe trailing/structural exit."""
    risk = abs(entry - initial_stop)
    stop = initial_stop
    extreme = entry
    for offset, bar in enumerate(forward):
        high, low, close = (float(bar[key]) for key in ("high", "low", "close"))
        stop_hit = low <= stop if direction == "BUY" else high >= stop
        if stop_hit:
            signed = stop - entry if direction == "BUY" else entry - stop
            return ("WIN" if signed > 0 else "LOSS"), signed / risk if risk else 0.0, offset

        absolute_index = entry_index + offset
        history = chronological[:absolute_index + 1]
        closes = [float(item["close"]) for item in history]
        structural_exit = False
        fast, slow = ema(closes, config.fast_ema), ema(closes, config.slow_ema)
        structural_exit = (fast is not None and slow is not None
                           and (fast <= slow if direction == "BUY" else fast >= slow))
        if structural_exit:
            signed = close - entry if direction == "BUY" else entry - close
            return ("WIN" if signed > 0 else "LOSS"), signed / risk if risk else 0.0, offset

        # The newly observed extreme only changes the stop for the next bar,
        # avoiding optimistic same-bar ordering assumptions.
        if direction == "BUY":
            extreme = max(extreme, high)
            stop = max(stop, extreme - 3.0 * signal_atr)
        else:
            extreme = min(extreme, low)
            stop = min(stop, extreme + 3.0 * signal_atr)

    close = float(forward[-1]["close"])
    signed = close - entry if direction == "BUY" else entry - close
    return "EXPIRED", signed / risk if risk else 0.0, len(forward) - 1


def simulate(bars: list[dict], config: StrategyConfig, start_ms: int, end_ms: int,
             expiry_bars: int = 6, fee_pct_round_trip: float = 0.10,
             slippage_pct_round_trip: float = 0.0,
             funding_pct_per_8h: float = 0.0,
             ensemble_members: tuple[StrategyConfig, ...] = (),
             min_votes: int = 1) -> list[dict]:
    if expiry_bars < 1:
        raise ValueError("expiry_bars must be at least 1")
    if min(fee_pct_round_trip, slippage_pct_round_trip, funding_pct_per_8h) < 0:
        raise ValueError("trading costs cannot be negative")
    if start_ms >= end_ms:
        raise ValueError("start_ms must be earlier than end_ms")
    chronological = sorted(bars, key=lambda bar: int(bar["datetime"]))
    all_daily = aggregate_daily(list(reversed(chronological)))
    trades, busy_until = [], -1
    for index in range(80, len(chronological) - expiry_bars):
        signal_bar = chronological[index]
        signal_time = int(signal_bar["datetime"])
        if signal_time < start_ms - expiry_bars * FOUR_HOURS_MS or signal_time >= end_ms \
                or signal_time <= busy_until:
            continue
        closed = list(reversed(chronological[:index + 1]))
        current_day_ms = int(signal_bar["datetime"]) // DAY_MS * DAY_MS
        completed_daily = [bar for bar in all_daily if int(bar["datetime"]) < current_day_ms]
        if ensemble_members:
            candidates = [evaluate_candidate(closed[:180], completed_daily, member)
                          for member in ensemble_members]
            candidates = [item for item in candidates if item]
            buy_votes = sum(item["direction"] == "BUY" for item in candidates)
            sell_votes = sum(item["direction"] == "SELL" for item in candidates)
            direction = "BUY" if buy_votes >= min_votes and buy_votes > sell_votes else (
                "SELL" if sell_votes >= min_votes and sell_votes > buy_votes else None)
            matching = [item for item in candidates if item["direction"] == direction]
            candidate = ({"direction": direction,
                          "atr": sum(float(item["atr"]) for item in matching) / len(matching),
                          "strategy": "+".join(member.name for member in ensemble_members)}
                         if direction else None)
        else:
            candidate = evaluate_candidate(closed[:180], completed_daily, config)
        if not candidate:
            continue
        entry_bar = chronological[index + 1]
        if int(entry_bar["datetime"]) >= end_ms:
            continue
        entry = float(entry_bar["open"])
        target, stop = candidate_levels(entry, candidate, config)
        full_forward = chronological[index + 1:index + 1 + expiry_bars]
        forward = [bar for bar in full_forward if int(bar["datetime"]) < end_ms]
        if not forward:
            continue
        if config.name == "snr_trend_following":
            outcome, result_r, exit_index = _resolve_trend_exit(
                candidate["direction"], entry, stop, forward, chronological,
                index + 1, config, float(candidate["atr"]),
            )
        else:
            outcome, result_r, exit_index = _resolve(
                candidate["direction"], entry, target, stop, forward)
        if outcome == "EXPIRED" and len(forward) < len(full_forward):
            continue
        if outcome == "WIN" and config.name != "snr_trend_following":
            result_r = config.reward_risk
        risk_pct = abs(entry - stop) / entry * 100
        holding_8h_periods = (exit_index + 1) / 2
        cost_pct = (fee_pct_round_trip + slippage_pct_round_trip
                    + funding_pct_per_8h * holding_8h_periods)
        result_r -= cost_pct / risk_pct if risk_pct else 0.0
        busy_until = forward[exit_index]["datetime"]
        if signal_time < start_ms:
            continue
        trades.append({"time": signal_bar["datetime"], "outcome": outcome, "net_r": result_r,
                       "direction": candidate["direction"],
                       "strategy": candidate.get("strategy", config.name)})
    return trades


def metrics(trades: list[dict]) -> dict:
    decided = [trade for trade in trades if trade["outcome"] in {"WIN", "LOSS"}]
    wins = sum(trade["outcome"] == "WIN" for trade in decided)
    net = sum(trade["net_r"] for trade in trades)
    gross_profit = sum(max(trade["net_r"], 0) for trade in trades)
    gross_loss = abs(sum(min(trade["net_r"], 0) for trade in trades))
    equity = peak = drawdown = 0.0
    for trade in trades:
        equity += trade["net_r"]
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    resolved_win_rate = round(wins / len(decided) * 100, 1) if decided else None
    return {"trades": len(trades), "wins": wins, "losses": len(decided) - wins,
            "expired": len(trades) - len(decided),
            "win_rate_pct": resolved_win_rate,
            "resolved_win_rate_pct": resolved_win_rate,
            "net_r": round(net, 3), "expectancy_r": round(net / len(trades), 3) if trades else None,
            "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss else None,
            "max_drawdown_r": round(drawdown, 3)}


def _profit_factor_score(result: dict) -> float:
    profit_factor = result["profit_factor"]
    if profit_factor is not None:
        return profit_factor
    return float("inf") if result["net_r"] > 0 else 0.0


def _resolved_trades(result: dict) -> int:
    return result["wins"] + result["losses"]


def _validate_research_thresholds(min_train_trades: int, min_win_rate_pct: float) -> None:
    if type(min_train_trades) is not int or min_train_trades < 1:
        raise ValueError("min_train_trades must be a positive integer")
    if isinstance(min_win_rate_pct, bool) or not isinstance(min_win_rate_pct, (int, float)) \
            or not 0 <= min_win_rate_pct <= 100:
        raise ValueError("min_win_rate_pct must be between 0 and 100")


def _meets_edge_requirements(result: dict, min_resolved_trades: int,
                             min_win_rate_pct: float) -> bool:
    win_rate = result["resolved_win_rate_pct"]
    return all((_resolved_trades(result) >= min_resolved_trades,
                win_rate is not None and win_rate >= min_win_rate_pct,
                result["net_r"] > 0,
                _profit_factor_score(result) > 1))


def parameter_grid():
    """Two restrained, pre-declared variants for ten independent hypotheses."""
    base = StrategyConfig()
    families = (
        "snr_trend_following", "ema_trend_pullback", "donchian_breakout",
        "macd_continuation", "supertrend_continuation", "bollinger_trend_pullback",
        "bollinger_reversion", "rsi_reversion", "volatility_breakout", "range_breakout",
    )
    for name in families:
        for variant in (0, 1):
            yield replace(
                base, name=name, fast_ema=(12, 20)[variant], slow_ema=(40, 55)[variant],
                adx_min=(18.0, 24.0)[variant], channel_lookback=(20, 40)[variant],
                sr_lookback=(30, 50)[variant], sr_tolerance_atr=(0.35, 0.55)[variant],
                bollinger_std=(2.0, 2.5)[variant], rsi_long_min=(35.0, 40.0)[variant],
                rsi_long_max=(65.0, 60.0)[variant], atr_expansion_min=(1.05, 1.20)[variant],
                volume_ratio_min=(1.0, 1.2)[variant], atr_stop=(1.5, 2.0)[variant],
                reward_risk=(2.0, 2.5)[variant], min_confirmations=2,
            )


def fetch_history_paginated(symbol: str, pages: int = 3) -> list[dict]:
    """Fetch deep 4H history, de-duplicated by candle timestamp."""
    if pages < 1:
        raise ValueError("pages must be at least 1")
    by_time = {}
    end_time_ms = None
    for page in range(pages):
        batch = fetch_bars(symbol, "4h", outputsize=1000, end_time_ms=end_time_ms)
        if not batch:
            break
        by_time.update({int(bar["datetime"]): bar for bar in batch})
        oldest = min(int(bar["datetime"]) for bar in batch)
        print(f"  {symbol}: page {page + 1}/{pages}, oldest={oldest}")
        if len(batch) < 1000:
            break
        end_time_ms = oldest - 1
    return sorted(by_time.values(), key=lambda bar: bar["datetime"], reverse=True)


def load_or_fetch_history(symbol: str, pages: int, cache_dir: str,
                          max_cache_age_seconds: int = 4 * 3600) -> list[dict]:
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, f"{symbol.replace('-', '_')}_4h_{pages}p.json")
    cache_is_fresh = (os.path.exists(path) and
                      time.time() - os.path.getmtime(path) < max_cache_age_seconds)
    if cache_is_fresh:
        with open(path, encoding="utf-8") as cached:
            bars = json.load(cached)
        print(f"  {symbol}: loaded {len(bars)} cached bars", flush=True)
        return bars
    bars = fetch_history_paginated(symbol, pages)
    with open(path, "w", encoding="utf-8") as cached:
        json.dump(bars, cached)
    return bars


def _combined(histories, config, start_ms, end_ms):
    return sorted((trade for bars in histories.values()
                   for trade in simulate(
                       bars, config, start_ms, end_ms, expiry_bars=42,
                       fee_pct_round_trip=0.10, slippage_pct_round_trip=0.04,
                       funding_pct_per_8h=0.01,
                   )),
                  key=lambda trade: int(trade["time"]))


def _pair_metrics(histories, config, start_ms, end_ms):
    return {pair: metrics(simulate(
        bars, config, start_ms, end_ms, expiry_bars=42,
        fee_pct_round_trip=0.10, slippage_pct_round_trip=0.04,
        funding_pct_per_8h=0.01,
    )) for pair, bars in histories.items()}


def _ensemble_trades(histories, members, start_ms, end_ms, min_votes):
    execution = replace(members[0], name="range_breakout")
    return sorted((trade for bars in histories.values() for trade in simulate(
        bars, execution, start_ms, end_ms, expiry_bars=42,
        fee_pct_round_trip=0.10, slippage_pct_round_trip=0.04,
        funding_pct_per_8h=0.01, ensemble_members=tuple(members), min_votes=min_votes,
    )), key=lambda trade: int(trade["time"]))


def _ensemble_pair_metrics(histories, members, start_ms, end_ms, min_votes):
    execution = replace(members[0], name="range_breakout")
    return {pair: metrics(simulate(
        bars, execution, start_ms, end_ms, expiry_bars=42,
        fee_pct_round_trip=0.10, slippage_pct_round_trip=0.04,
        funding_pct_per_8h=0.01, ensemble_members=tuple(members), min_votes=min_votes,
    )) for pair, bars in histories.items()}


def _economically_positive(result: dict, min_trades: int) -> bool:
    return (result["trades"] >= min_trades and result["net_r"] > 0
            and result["expectancy_r"] is not None and result["expectancy_r"] > 0
            and _profit_factor_score(result) > 1)


def run_three_month_research(histories: dict[str, list[dict]],
                             min_train_trades: int = 20) -> dict:
    """Select on pre-holdout rolling months; evaluate once on the final 90 days."""
    if type(min_train_trades) is not int or min_train_trades < 1:
        raise ValueError("min_train_trades must be a positive integer")
    populated = {pair: bars for pair, bars in histories.items() if bars}
    if not populated:
        return {"status": "insufficient_data", "reason": "no historical bars"}
    latest = min(max(int(bar["datetime"]) for bar in bars) for bars in populated.values())
    test_end = latest + FOUR_HOURS_MS
    test_start = test_end - 90 * DAY_MS
    earliest = max(min(int(bar["datetime"]) for bar in bars) for bars in populated.values())
    development_start = max(earliest, test_start - 270 * DAY_MS)
    if test_start - development_start < 90 * DAY_MS:
        return {"status": "insufficient_data", "required_development_days": 90,
                "available_start_ms": earliest}

    families = []
    family_names = tuple(dict.fromkeys(config.name for config in parameter_grid()))
    for family in family_names:
        variants = []
        for config in (item for item in parameter_grid() if item.name == family):
            development_trades = _combined(populated, config, development_start, test_start)
            development = metrics(development_trades)
            months = []
            for start, end in _month_windows(development_start, test_start):
                month_trades = [item for item in development_trades
                                if start <= int(item["time"]) < end]
                months.append({"start_ms": start, "end_ms": end,
                               **metrics(month_trades)})
            active = [month for month in months if month["trades"]]
            profitable_months = sum(month["net_r"] > 0 for month in active)
            variants.append({"policy": asdict(config), "development": development,
                             "monthly": months, "profitable_months": profitable_months,
                             "active_months": len(active)})
        eligible = [item for item in variants
                    if (_economically_positive(item["development"], min_train_trades)
                        and item["active_months"] >= 3
                        and item["profitable_months"] * 2 >= item["active_months"])]
        selectable = eligible or variants
        selected_item = max(selectable, key=lambda item: (
            item["profitable_months"] / max(item["active_months"], 1),
            item["development"]["expectancy_r"],
            _profit_factor_score(item["development"]),
            -item["development"]["max_drawdown_r"],
        ))
        selected = StrategyConfig(**selected_item["policy"])
        holdout = metrics(_combined(populated, selected, test_start, test_end))
        pairs = _pair_metrics(populated, selected, test_start, test_end)
        positive_pairs = sum(item["net_r"] > 0 for item in pairs.values())
        train_eligible = bool(eligible)
        passed = (train_eligible and _economically_positive(holdout, 10)
                  and positive_pairs >= 3)
        families.append({
            "family": family, "status": ("passed" if passed else
                                           "rejected" if train_eligible else "no_train_edge"),
            "eligible_for_promotion": train_eligible,
            "policy": asdict(selected), "development": selected_item["development"],
            "development_monthly": selected_item["monthly"],
            "holdout_90d": holdout, "holdout_by_pair": pairs,
            "positive_holdout_pairs": positive_pairs, "variants": variants,
        })

    development_ranked = sorted(
        (item for item in families if item.get("policy")),
        key=lambda item: (item["development"]["expectancy_r"],
                          _profit_factor_score(item["development"]),
                          -item["development"]["max_drawdown_r"]), reverse=True)
    finalists = development_ranked[:4]
    combinations = []
    for size in (2, 3):
        for selected_items in itertools.combinations(finalists, size):
            members = [StrategyConfig(**item["policy"]) for item in selected_items]
            min_votes = 2
            development = metrics(_ensemble_trades(
                populated, members, development_start, test_start, min_votes))
            train_eligible = _economically_positive(development, min_train_trades)
            holdout = metrics(_ensemble_trades(
                populated, members, test_start, test_end, min_votes))
            pairs = _ensemble_pair_metrics(populated, members, test_start, test_end, min_votes)
            positive_pairs = sum(item["net_r"] > 0 for item in pairs.values())
            combination_passed = (train_eligible and _economically_positive(holdout, 10)
                                  and positive_pairs >= 3)
            combinations.append({"members": [item.name for item in members],
                                 "min_votes": min_votes,
                                 "status": ("passed" if combination_passed else
                                            "rejected" if train_eligible else "no_train_edge"),
                                 "eligible_for_promotion": train_eligible,
                                 "development": development, "holdout_90d": holdout,
                                 "holdout_by_pair": pairs,
                                 "positive_holdout_pairs": positive_pairs})

    best_observed = max(families, key=lambda item: (
        item["holdout_90d"]["expectancy_r"],
        _profit_factor_score(item["holdout_90d"]),
        -item["holdout_90d"]["max_drawdown_r"],
    )) if families else None
    promotion_candidates = [item for item in development_ranked
                            if item["eligible_for_promotion"]]
    promotion_candidate = promotion_candidates[0] if promotion_candidates else None
    winner = (promotion_candidate if promotion_candidate
              and promotion_candidate["status"] == "passed" else None)
    return {
        "status": "passed" if winner else "rejected",
        "policy": winner["policy"] if winner else None,
        "winner_family": winner["family"] if winner else None,
        "promotion_candidate_family": (promotion_candidate["family"]
                                       if promotion_candidate else None),
        "best_observed_holdout_family": (best_observed["family"]
                                          if best_observed else None),
        "development_start_ms": development_start, "holdout_start_ms": test_start,
        "test_end_ms": test_end,
        "cost_model": {"taker_fee_round_trip_pct": 0.10,
                       "slippage_round_trip_pct": 0.04,
                       "funding_drag_pct_per_8h": 0.01,
                       "same_bar_stop_first": True},
        "promotion_rules": {"development_profitable_months_min_fraction": 0.5,
                            "holdout_min_trades": 10, "positive_pairs": 3,
                            "net_r_gt": 0, "profit_factor_gt": 1},
        "families": families,
        "development_leaderboard": [item["family"] for item in development_ranked],
        "holdout_leaderboard": [item["family"] for item in sorted(
            families, key=lambda item: (item["holdout_90d"]["expectancy_r"],
                                        _profit_factor_score(item["holdout_90d"]),
                                        -item["holdout_90d"]["max_drawdown_r"]), reverse=True)],
        "combinations": combinations,
        "combination_note": "Ensembles are research-only until the live policy schema supports voting.",
    }


def _month_windows(start_ms: int, end_ms: int):
    cursor = start_ms
    while cursor < end_ms:
        following = min(cursor + 30 * DAY_MS, end_ms)
        yield cursor, following
        cursor = following


def run_monthly_research(histories: dict[str, list[dict]], min_train_trades: int = 30,
                         min_win_rate_pct: float = 60.0) -> dict:
    """Pick one config per family on old data, then compare rolling 30-day results."""
    _validate_research_thresholds(min_train_trades, min_win_rate_pct)
    populated = [bars for bars in histories.values() if bars]
    if not populated:
        return {"status": "insufficient_data", "reason": "no historical bars"}
    latest = min(max(int(bar["datetime"]) for bar in bars) for bars in populated)
    earliest = max(min(int(bar["datetime"]) for bar in bars) for bars in populated)
    available = latest - earliest
    evaluation_start = latest - min(180 * DAY_MS, int(available * 0.4))
    grouped = {}
    for config in parameter_grid():
        train_metrics = metrics(_combined(histories, config, earliest, evaluation_start))
        if not _meets_edge_requirements(train_metrics, min_train_trades, min_win_rate_pct):
            continue
        grouped.setdefault(config.name, []).append((train_metrics, config))

    families = []
    for name, candidates in sorted(grouped.items()):
        train, selected = max(candidates, key=lambda item: (
            item[0]["expectancy_r"], _profit_factor_score(item[0]),
            -item[0]["max_drawdown_r"], item[0]["trades"]))
        monthly = []
        all_evaluation_trades = []
        for start, end in _month_windows(evaluation_start, latest + FOUR_HOURS_MS):
            trades = _combined(histories, selected, start, end)
            all_evaluation_trades.extend(trades)
            monthly.append({"start_ms": start, "end_ms": end, **metrics(trades)})
        active_months = [month for month in monthly if month["trades"]]
        profitable_months = sum(month["net_r"] > 0 for month in active_months)
        expectancies = sorted(month["expectancy_r"] for month in active_months
                              if month["expectancy_r"] is not None)
        median_expectancy = (expectancies[len(expectancies) // 2] if expectancies else None)
        aggregate = metrics(sorted(all_evaluation_trades, key=lambda trade: trade["time"]))
        stability_score = ((profitable_months / len(active_months)) if active_months else 0,
                           median_expectancy if median_expectancy is not None else -999,
                           aggregate["expectancy_r"] if aggregate["expectancy_r"] is not None else -999)
        families.append({"strategy": name, "policy": asdict(selected), "train": train,
                         "rolling_30d": monthly, "evaluation": aggregate,
                         "active_months": len(active_months),
                         "profitable_months": profitable_months,
                         "profitable_month_pct": round(100 * profitable_months / len(active_months), 1)
                         if active_months else None,
                         "median_month_expectancy_r": median_expectancy,
                         "_score": stability_score})
    families.sort(key=lambda item: item["_score"], reverse=True)
    for family in families:
        family.pop("_score")
    return {"status": "ok" if families else "no_train_policy_meets_target",
            "target_resolved_win_rate_pct": min_win_rate_pct,
            "min_train_resolved_trades": min_train_trades, "train_start_ms": earliest,
            "evaluation_start_ms": evaluation_start, "latest_ms": latest,
            "families": families, "best_by_monthly_stability": families[0] if families else None}


def run_walk_forward(histories: dict[str, list[dict]], min_train_trades: int = 20,
                     min_win_rate_pct: float = 60.0) -> dict:
    _validate_research_thresholds(min_train_trades, min_win_rate_pct)
    populated = [bars for bars in histories.values() if bars]
    if not populated:
        return {"status": "insufficient_data", "reason": "no historical bars"}
    latest = min(max(int(bar["datetime"]) for bar in bars) for bars in populated)
    final_start, validation_start = latest - 90 * DAY_MS, latest - 120 * DAY_MS
    earliest = max(min(int(bar["datetime"]) for bar in bars) for bars in populated)
    evaluations = []
    for config in parameter_grid():
        train = [trade for bars in histories.values()
                 for trade in simulate(bars, config, earliest, validation_start)]
        score = metrics(sorted(train, key=lambda trade: trade["time"]))
        if _resolved_trades(score) >= min_train_trades:
            validation = [trade for bars in histories.values()
                          for trade in simulate(bars, config, validation_start, final_start)]
            evaluations.append((score, metrics(sorted(validation, key=lambda trade: trade["time"])), config))
    if not evaluations:
        return {"status": "insufficient_data", "train_start_ms": earliest,
                "validation_start_ms": validation_start, "final_start_ms": final_start}
    # Train removes policies with no evidence of an edge. The requested 30-day
    # validation then chooses among that shortlist; the newest 90 days remain
    # completely untouched until one policy has been selected.
    shortlist = [item for item in evaluations
                 if _meets_edge_requirements(item[0], min_train_trades, min_win_rate_pct)]
    if not shortlist:
        return {"status": "no_train_policy_meets_target",
                "target_resolved_win_rate_pct": min_win_rate_pct,
                "min_train_resolved_trades": min_train_trades,
                "evaluated_policies": len(evaluations)}
    validation_shortlist = [item for item in shortlist
                            if _meets_edge_requirements(item[1], 10, min_win_rate_pct)]
    if not validation_shortlist:
        return {"status": "no_validation_policy_meets_target",
                "target_resolved_win_rate_pct": min_win_rate_pct,
                "min_train_resolved_trades": min_train_trades,
                "evaluated_policies": len(evaluations),
                "train_target_shortlist": len(shortlist)}
    score, validation_metrics, selected = max(validation_shortlist, key=lambda item: (
        item[1]["expectancy_r"] if item[1]["expectancy_r"] is not None else -999,
        _profit_factor_score(item[1]), -item[1]["max_drawdown_r"], item[1]["trades"]))
    final = [trade for bars in histories.values()
             for trade in simulate(bars, selected, final_start, latest + FOUR_HOURS_MS)]
    final_metrics = metrics(sorted(final, key=lambda trade: trade["time"]))
    passed = _meets_edge_requirements(final_metrics, 20, min_win_rate_pct)
    return {"status": "passed" if passed else "rejected", "policy": asdict(selected),
            "train": score, "validation_30d": validation_metrics, "final_90d": final_metrics,
            "target_resolved_win_rate_pct": min_win_rate_pct,
            "min_train_resolved_trades": min_train_trades,
            "train_start_ms": earliest, "validation_start_ms": validation_start,
            "final_start_ms": final_start, "latest_ms": latest,
            "evaluated_policies": len(evaluations), "train_target_shortlist": len(shortlist),
            "validation_target_shortlist": len(validation_shortlist)}


def persist_promoted_policy(result: dict, path: str = "strategy_policy.json") -> bool:
    """Write only a passed policy; remove stale live state after a rejected run."""
    if result.get("status") == "passed" and result.get("policy"):
        with open(path, "w", encoding="utf-8") as output:
            json.dump(result["policy"], output, indent=2)
        return True
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pairs", default="BTC-USDT,ETH-USDT,SOL-USDT,XRP-USDT,BNB-USDT")
    parser.add_argument("--min-train-trades", type=int, default=20)
    parser.add_argument("--min-win-rate", type=float, default=60.0,
                        help="minimum resolved win rate required in train, validation, and final")
    parser.add_argument("--output", default="strategy_three_month_research.json")
    parser.add_argument("--pages", type=int, default=3,
                        help="1000-bar 4H pages per pair (3 is roughly 500 days)")
    parser.add_argument("--mode", choices=("three-month", "monthly", "walk-forward"),
                        default="three-month")
    parser.add_argument("--cache-dir", default=".strategy_cache")
    args = parser.parse_args()
    try:
        _validate_research_thresholds(args.min_train_trades, args.min_win_rate)
    except ValueError as error:
        parser.error(str(error))
    histories = {}
    for pair in args.pairs.split(","):
        pair = pair.strip()
        if pair:
            print(f"Fetching {pair} 4h history...", flush=True)
            histories[pair] = load_or_fetch_history(pair, args.pages, args.cache_dir)
    if args.mode == "three-month":
        result = run_three_month_research(histories, args.min_train_trades)
    elif args.mode == "monthly":
        result = run_monthly_research(histories, args.min_train_trades, args.min_win_rate)
    else:
        result = run_walk_forward(histories, args.min_train_trades, args.min_win_rate)
    with open(args.output, "w", encoding="utf-8") as output:
        json.dump(result, output, indent=2)
    print(json.dumps(result, indent=2))
    if args.mode in {"three-month", "walk-forward"} and persist_promoted_policy(result):
        print("PASS: strategy_policy.json promoted for live demo use.")
    elif args.mode in {"three-month", "walk-forward"}:
        print("NOT PROMOTED: validation/final profitability requirements were not met.")
    else:
        print("RESEARCH ONLY: rolling months are diagnostic and do not promote a live policy.")


if __name__ == "__main__":
    main()