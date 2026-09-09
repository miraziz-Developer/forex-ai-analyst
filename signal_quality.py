"""Pure, shared signal-quality gates used by both live trading and backtests."""

CONFIDENCE_LEVELS = {"PAST": 1, "ORTA": 2, "YUQORI": 3}


def confidence_meets_minimum(confidence: str | None, minimum: str = "ORTA") -> bool:
    """Fail closed when confidence is absent/unknown or below the configured floor."""
    normalized_minimum = minimum.strip().upper()
    if normalized_minimum not in CONFIDENCE_LEVELS:
        raise ValueError(
            f"MIN_SIGNAL_CONFIDENCE must be one of {', '.join(CONFIDENCE_LEVELS)}, "
            f"got {minimum!r}"
        )
    normalized_confidence = confidence.strip().upper() if confidence else ""
    return CONFIDENCE_LEVELS.get(normalized_confidence, 0) >= CONFIDENCE_LEVELS[normalized_minimum]


def reward_risk_ratio(entry: float, target: float, stop: float) -> float:
    """Return the actual reward/risk implied by executable prices."""
    risk = abs(entry - stop)
    return abs(target - entry) / risk if risk > 0 else 0.0


def performance_metrics(rows: list[dict]) -> dict:
    """Risk-normalized metrics for decided trades, ordered chronologically.

    A target hit earns its actual R:R and a stop hit loses 1R. EXPIRED trades
    are deliberately excluded because they are neither comparable target nor
    stop outcomes. This avoids a high win rate hiding poor payoff asymmetry.
    """
    decided = [row for row in rows if row.get("outcome") in {"WIN", "LOSS"}]
    decided.sort(key=lambda row: row.get("checkpoint_ms", row.get("signal_time", 0)))
    wins = [row for row in decided if row["outcome"] == "WIN"]
    losses = [row for row in decided if row["outcome"] == "LOSS"]
    returns_r = [float(row.get("actual_rr", 0.0)) if row["outcome"] == "WIN" else -1.0
                 for row in decided]
    gross_profit_r = sum(value for value in returns_r if value > 0)
    gross_loss_r = abs(sum(value for value in returns_r if value < 0))

    equity_r = peak_r = max_drawdown_r = 0.0
    for value in returns_r:
        equity_r += value
        peak_r = max(peak_r, equity_r)
        max_drawdown_r = max(max_drawdown_r, peak_r - equity_r)

    count = len(decided)
    return {
        "trades": count,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(len(wins) / count * 100, 1) if count else None,
        "net_r": round(sum(returns_r), 3),
        "profit_factor": round(gross_profit_r / gross_loss_r, 3) if gross_loss_r else None,
        "expectancy_r": round(sum(returns_r) / count, 3) if count else None,
        "max_drawdown_r": round(max_drawdown_r, 3),
    }


def rows_for_policy(rows: list[dict], minimum_confidence: str, minimum_rr: float) -> list[dict]:
    """Return decided AI trade candidates accepted by a hypothetical policy."""
    return [
        row for row in rows
        if row.get("outcome") in {"WIN", "LOSS"}
        and confidence_meets_minimum(row.get("confidence"), minimum_confidence)
        and float(row.get("actual_rr", 0.0)) >= minimum_rr
    ]


def optimize_policy_holdout(rows: list[dict], holdout_ratio: float = 0.30,
                            min_train_trades: int = 20,
                            confidence_grid: tuple[str, ...] = ("ORTA", "YUQORI"),
                            rr_grid: tuple[float, ...] = (1.5, 2.0, 2.5)) -> dict:
    """Select a policy on older data, then evaluate once on unseen newer data.

    The small, predefined grid limits multiple-testing risk. Selection is by
    train expectancy, then profit factor, net R, lower drawdown, and finally
    more observations. Holdout metrics never participate in policy selection.
    """
    if not 0 < holdout_ratio < 1:
        raise ValueError("holdout_ratio must be between 0 and 1")
    if min_train_trades < 1:
        raise ValueError("min_train_trades must be at least 1")

    candidates = [row for row in rows
                  if row.get("outcome") in {"WIN", "LOSS"}
                  and row.get("actual_rr") is not None]
    candidates.sort(key=lambda row: row.get("checkpoint_ms", row.get("signal_time", 0)))
    if len(candidates) < 2:
        return {"status": "insufficient_data", "candidate_trades": len(candidates)}

    split_index = max(1, min(len(candidates) - 1,
                             int(len(candidates) * (1 - holdout_ratio))))
    train, holdout = candidates[:split_index], candidates[split_index:]
    evaluations = []
    for confidence in confidence_grid:
        for minimum_rr in rr_grid:
            train_metrics = performance_metrics(rows_for_policy(train, confidence, minimum_rr))
            evaluations.append({
                "minimum_confidence": confidence,
                "minimum_rr": minimum_rr,
                "train": train_metrics,
            })

    eligible = [item for item in evaluations if item["train"]["trades"] >= min_train_trades]
    if not eligible:
        return {
            "status": "insufficient_data",
            "candidate_trades": len(candidates),
            "train_candidates": len(train),
            "holdout_candidates": len(holdout),
            "min_train_trades": min_train_trades,
            "evaluations": evaluations,
        }

    def _selection_key(item: dict) -> tuple:
        metrics = item["train"]
        profit_factor = metrics["profit_factor"]
        return (
            metrics["expectancy_r"],
            profit_factor if profit_factor is not None else float("inf"),
            metrics["net_r"],
            -metrics["max_drawdown_r"],
            metrics["trades"],
        )

    selected = max(eligible, key=_selection_key)
    holdout_rows = rows_for_policy(
        holdout, selected["minimum_confidence"], selected["minimum_rr"])
    return {
        "status": "ok",
        "candidate_trades": len(candidates),
        "train_candidates": len(train),
        "holdout_candidates": len(holdout),
        "holdout_ratio": holdout_ratio,
        "min_train_trades": min_train_trades,
        "selected_policy": {
            "minimum_confidence": selected["minimum_confidence"],
            "minimum_rr": selected["minimum_rr"],
        },
        "train_metrics": selected["train"],
        "holdout_metrics": performance_metrics(holdout_rows),
        "evaluations": evaluations,
    }