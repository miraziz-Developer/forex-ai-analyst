"""Historical backtest: replays the exact same AI analyst against real past
price data to get a much faster (hours, not weeks) directional read on whether
the strategy has any edge. Not a substitute for the live forward-test track
record (/stats) — see the caveats printed at the end of the report.

Usage:
    python backtest.py [--pairs BTC-USDT,ETH-USDT] [--checkpoint-hours 4] [--workers 6]
"""

import argparse
import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from dotenv import load_dotenv
from openai import OpenAI

from indicators import atr_pct, atr_percentile
from market_data import fetch_bars
from signal_quality import (
    confidence_meets_minimum,
    optimize_policy_holdout,
    performance_metrics,
    reward_risk_ratio,
)

load_dotenv()

logging.basicConfig(level=logging.WARNING)  # keep backtest output focused on the report
logger = logging.getLogger(__name__)

AZURE_OPENAI_API_KEY = os.environ["AZURE_OPENAI_API_KEY"]
AZURE_OPENAI_ENDPOINT = os.environ["AZURE_OPENAI_ENDPOINT"]
AZURE_OPENAI_DEPLOYMENT = os.environ["AZURE_OPENAI_DEPLOYMENT"]

FALLBACK_TARGET_PCT, FALLBACK_STOP_PCT = (
    float(x) for x in os.environ.get("TARGET_PCT_STOP_PCT", "1.5/0.6").split("/")
)
ATR_MULTIPLIER = float(os.environ.get("ATR_MULTIPLIER", "2.0"))
REWARD_RISK_RATIO = float(os.environ.get("REWARD_RISK_RATIO", "2.5"))
ATR_PERCENTILE_MIN = float(os.environ.get("ATR_PERCENTILE_MIN", "20"))
ATR_PERCENTILE_MAX = float(os.environ.get("ATR_PERCENTILE_MAX", "90"))
MIN_REWARD_RISK_RATIO = float(os.environ.get("MIN_REWARD_RISK_RATIO", "1.5"))
MIN_SIGNAL_CONFIDENCE = os.environ.get("MIN_SIGNAL_CONFIDENCE", "ORTA").strip().upper()
SIGNAL_EXPIRY_HOURS = float(os.environ.get("SIGNAL_EXPIRY_HOURS", "24"))
confidence_meets_minimum(MIN_SIGNAL_CONFIDENCE, MIN_SIGNAL_CONFIDENCE)  # validate at startup


def compute_stop_target_pct(bars_1h: list[dict]) -> tuple[float, float]:
    """Same volatility-adjusted logic as app.py — kept in sync so backtests
    actually reflect live behavior."""
    atr = atr_pct(bars_1h)
    if atr is None:
        return FALLBACK_TARGET_PCT, FALLBACK_STOP_PCT
    stop_pct = atr * ATR_MULTIPLIER
    return stop_pct * REWARD_RISK_RATIO, stop_pct

with open("system_prompt.txt", encoding="utf-8") as f:
    SYSTEM_PROMPT = f.read()

BACKTEST_NOTE = (
    "IMPORTANT: this is a historical backtest run. You do NOT have web search "
    "or live institutional data (funding rate / open interest / order book) "
    "for this check — say so plainly in the FUNDAMENTAL and INSTITUTSIONAL "
    "POZITSIYA sections rather than guessing. Base your verdict purely on the "
    "technical price data provided below.\n\n"
)

openai_client = OpenAI(base_url=AZURE_OPENAI_ENDPOINT, api_key=AZURE_OPENAI_API_KEY)


def extract_recommendation(analysis: str) -> str:
    for line in analysis.splitlines():
        normalized = re.sub(r"[’‘'ʻʼ`´]", "", line).upper()
        if "TAVSIYA" in normalized:
            if "SAVDONI KUZATISH" in normalized:
                return "TRADE_WATCH"
            if "OTKAZIB YUBORISH" in normalized:
                return "SKIP"
    return "UNKNOWN"


def extract_price(analysis: str, label: str) -> float | None:
    for line in analysis.splitlines():
        if label in line.upper():
            match = re.search(r"[\d][\d,]*\.?\d*", line)
            if match:
                try:
                    return float(match.group().replace(",", ""))
                except ValueError:
                    return None
    return None


def resolve_target_stop(entry_price: float, direction: str, ai_target: float | None, ai_stop: float | None,
                         fallback_target_pct: float, fallback_stop_pct: float) -> tuple[float, float]:
    """Same logic as app.py's _resolve_target_stop, kept in sync so the backtest
    actually reflects live behavior — trust the model's structural levels unless
    they fail a basic sanity check, then fall back to the ATR-based percentage."""
    if ai_stop is not None and ai_target is not None:
        stop_pct_actual = abs(entry_price - ai_stop) / entry_price * 100
        on_correct_side = (direction == "BUY" and ai_stop < entry_price < ai_target) or \
                           (direction == "SELL" and ai_target < entry_price < ai_stop)
        sane_distance = 0.05 <= stop_pct_actual <= max(fallback_stop_pct * 6, 3.0)
        if on_correct_side and sane_distance:
            return ai_target, ai_stop

    if direction == "BUY":
        return entry_price * (1 + fallback_target_pct / 100), entry_price * (1 - fallback_stop_pct / 100)
    return entry_price * (1 - fallback_target_pct / 100), entry_price * (1 + fallback_stop_pct / 100)


def extract_direction(analysis: str) -> str | None:
    for line in analysis.splitlines():
        normalized = re.sub(r"[’‘'ʻʼ`´]", "", line).upper()
        if "YONALISH" in normalized:
            if "SOTIB OLISH" in normalized:
                return "BUY"
            if "SOTISH" in normalized:
                return "SELL"
    return None


def extract_confidence(analysis: str) -> str | None:
    for line in analysis.splitlines():
        normalized = re.sub(r"[’‘'ʻʼ`´]", "", line).upper()
        if "ISHONCH DARAJASI" in normalized:
            for level in ("YUQORI", "ORTA", "PAST"):
                if level in normalized:
                    return level
    return None


def fetch_history_paginated(symbol: str, interval: str, pages: int) -> list[dict]:
    """A single kline call caps at 1000 bars. Walk endTime backward for more
    depth on the finer timeframes — this is one-off data prep, not a recurring
    live cost, so it goes through market_data's existing rate-limit-aware
    retry rather than a bespoke spacing scheme."""
    all_bars: list[dict] = []
    end_time_ms = None
    for page in range(pages):
        try:
            batch = fetch_bars(symbol, interval, outputsize=1000, end_time_ms=end_time_ms)
        except Exception as exc:
            print(f"  {symbol} {interval} page {page + 1}/{pages}: fetch failed ({exc}), stopping pagination here")
            break
        if not batch:
            break
        all_bars.extend(b for b in batch if b not in all_bars)
        end_time_ms = min(b["datetime"] for b in batch) - 1
    all_bars.sort(key=lambda b: b["datetime"], reverse=True)
    return all_bars


def fetch_history(symbol: str, pages: int = 2) -> dict[str, list[dict]]:
    """4H (1000 bars = ~166 days) and 1H (1000 bars = ~41 days) already comfortably
    cover a useful backtest window in one call. 15min (1000 bars = ~10.4 days/page) is
    the binding constraint on window length, so it gets paginated for more depth —
    `pages` controls how far back (2 = ~20.8 days, 4 = ~41.6 days, etc.)."""
    history = {}
    try:
        history["1d"] = fetch_bars(symbol, "1d", outputsize=200)
    except Exception as exc:
        print(f"  {symbol} 1d: fetch failed ({exc}), skipping this timeframe")
        history["1d"] = []
    try:
        history["4h"] = fetch_bars(symbol, "4h", outputsize=1000)
    except Exception as exc:
        print(f"  {symbol} 4h: fetch failed ({exc}), skipping this timeframe")
        history["4h"] = []
    try:
        history["1h"] = fetch_bars(symbol, "1h", outputsize=1000)
    except Exception as exc:
        print(f"  {symbol} 1h: fetch failed ({exc}), skipping this timeframe")
        history["1h"] = []
    history["15min"] = fetch_history_paginated(symbol, "15m", pages=pages)
    return history


def _bars_up_to(bars: list[dict], checkpoint_ms: int, count: int) -> list[dict]:
    """bars are most-recent-first; returns up to `count` bars whose time <= checkpoint,
    still most-recent-first."""
    return [b for b in bars if b["datetime"] <= checkpoint_ms][:count]


def _build_checkpoint_prompt(symbol: str, checkpoint_ms: int, bars_1d, bars_4h, bars_1h, bars_15m,
                              target_pct: float, stop_pct: float) -> str:
    checkpoint_iso = datetime.fromtimestamp(checkpoint_ms / 1000, tz=timezone.utc).isoformat()
    return (
        BACKTEST_NOTE
        + f"Pair: {symbol} (perpetual futures, USDT-margined)\n"
        f"Current UTC time (backtest checkpoint): {checkpoint_iso}\n"
        f"Configured target: {target_pct:.3f}% , max stop: {stop_pct:.3f}% "
        f"(volatility-adjusted: {ATR_MULTIPLIER}x 1H ATR, {REWARD_RISK_RATIO}:1 reward:risk)\n\n"
        f"1D bars (macro trend context, most recent first):\n{json.dumps(bars_1d[:30])}\n\n"
        f"4H bars (most recent first):\n{json.dumps(bars_4h[:20])}\n\n"
        f"1H bars:\n{json.dumps(bars_1h[:24])}\n\n"
        f"15min bars:\n{json.dumps(bars_15m[:20])}\n"
    )


def _resolve_forward(direction: str, entry: float, target: float, stop: float,
                      forward_1h_bars_chronological: list[dict]) -> tuple[str, float]:
    for bar in forward_1h_bars_chronological:
        high, low = float(bar["high"]), float(bar["low"])
        if direction == "BUY":
            hit_target, hit_stop = high >= target, low <= stop
        else:
            hit_target, hit_stop = low <= target, high >= stop
        if hit_stop:
            return "LOSS", stop
        if hit_target:
            return "WIN", target
    return "EXPIRED", (forward_1h_bars_chronological[-1]["close"] if forward_1h_bars_chronological else entry)


def run_checkpoint(symbol: str, checkpoint_ms: int, history: dict) -> dict | None:
    bars_1d = _bars_up_to(history["1d"], checkpoint_ms, 30)
    bars_4h = _bars_up_to(history["4h"], checkpoint_ms, 50)
    # Live fetches 150 1H bars so its 14-period ATR can be ranked over 100
    # historical values. Keep the same depth and gate here for honest parity.
    bars_1h = _bars_up_to(history["1h"], checkpoint_ms, 150)
    bars_15m = _bars_up_to(history["15min"], checkpoint_ms, 50)
    # ATR percentile needs 14 true ranges plus a 100-value ranking window.
    # Do not silently bypass the live volatility gate during warm-up.
    if len(bars_1h) < 115 or len(bars_15m) < 20:
        return None  # not enough history yet at this checkpoint

    regime_pct = atr_percentile(bars_1h)
    if regime_pct is not None and not (ATR_PERCENTILE_MIN <= regime_pct <= ATR_PERCENTILE_MAX):
        return {
            "symbol": symbol,
            "checkpoint_ms": checkpoint_ms,
            "recommendation": "FILTERED_VOLATILITY",
            "atr_percentile": regime_pct,
        }

    target_pct, stop_pct = compute_stop_target_pct(bars_1h)
    prompt = _build_checkpoint_prompt(symbol, checkpoint_ms, bars_1d, bars_4h, bars_1h, bars_15m,
                                       target_pct, stop_pct)
    try:
        response = openai_client.responses.create(
            model=AZURE_OPENAI_DEPLOYMENT,
            instructions=SYSTEM_PROMPT,
            input=prompt,
        )
        analysis = response.output_text
    except Exception as exc:
        logger.warning("Checkpoint call failed for %s @ %s: %s", symbol, checkpoint_ms, exc)
        return None

    recommendation = extract_recommendation(analysis)
    result = {"symbol": symbol, "checkpoint_ms": checkpoint_ms, "recommendation": recommendation,
              "analysis": analysis}
    if recommendation != "TRADE_WATCH":
        return result

    confidence = extract_confidence(analysis)
    result["confidence"] = confidence
    filter_reason = None
    if not confidence_meets_minimum(confidence, MIN_SIGNAL_CONFIDENCE):
        result["recommendation"] = "FILTERED_CONFIDENCE"
        filter_reason = "confidence"

    direction = extract_direction(analysis)
    if not direction:
        result["recommendation"] = "UNPARSEABLE"
        return result

    entry = bars_1h[0]["close"] if bars_1h else None
    entry = float(entry)
    ai_stop = extract_price(analysis, "STOP-LOSS NARXI")
    ai_target = extract_price(analysis, "TAKE-PROFIT NARXI")
    target, stop = resolve_target_stop(entry, direction, ai_target, ai_stop, target_pct, stop_pct)
    actual_rr = reward_risk_ratio(entry, target, stop)
    result["actual_rr"] = actual_rr
    if actual_rr < MIN_REWARD_RISK_RATIO:
        result["recommendation"] = "FILTERED_RR"
        filter_reason = "reward_risk" if filter_reason is None else f"{filter_reason}+reward_risk"

    expiry_ms = checkpoint_ms + int(SIGNAL_EXPIRY_HOURS * 3600 * 1000)
    forward = sorted(
        (b for b in history["1h"] if checkpoint_ms < b["datetime"] <= expiry_ms),
        key=lambda b: b["datetime"],
    )
    outcome, outcome_price = _resolve_forward(direction, entry, target, stop, forward)

    result.update(direction=direction, entry=entry, target=target, stop=stop,
                   accepted=filter_reason is None, filter_reason=filter_reason,
                   outcome=outcome, outcome_price=outcome_price)
    return result


def run_backtest(pairs: list[str], checkpoint_hours: int, workers: int,
                  min_age_days: float = 0, max_age_days: float | None = None,
                  history_pages: int = 2, holdout_ratio: float = 0.30,
                  min_train_trades: int = 20) -> None:
    print(f"Fetching bulk history for {pairs}...")
    histories = {symbol: fetch_history(symbol, pages=history_pages) for symbol in pairs}

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    window_start_ms = now_ms - int(max_age_days * 86400 * 1000) if max_age_days else None
    window_end_ms = now_ms - int(min_age_days * 86400 * 1000)

    tasks = []
    for symbol, history in histories.items():
        bar_1h_times = sorted(b["datetime"] for b in history["1h"])
        bar_15m_times = sorted(b["datetime"] for b in history["15min"])
        if not bar_1h_times or not bar_15m_times:
            print(f"  {symbol}: missing 1h or 15min history, skipping")
            continue
        # bounded by whichever timeframe's window is shorter (usually 15min,
        # since it's the finest granularity and caps out fastest per API call)
        start_ms = max(bar_1h_times[0], bar_15m_times[0])
        end_ms = min(bar_1h_times[-1], bar_15m_times[-1], window_end_ms)
        if window_start_ms:
            start_ms = max(start_ms, window_start_ms)
        step_ms = checkpoint_hours * 3600 * 1000
        checkpoint = start_ms + step_ms * 20  # skip the first ~20 checkpoints so bars_up_to has enough history
        while checkpoint < end_ms - step_ms:  # leave room for a forward window to resolve against
            tasks.append((symbol, checkpoint, history))
            checkpoint += step_ms

    print(f"Running {len(tasks)} checkpoints across {workers} workers "
          f"(each is a real AI call — this costs real tokens)...")

    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run_checkpoint, symbol, cp, history): (symbol, cp) for symbol, cp, history in tasks}
        done = 0
        for future in as_completed(futures):
            done += 1
            r = future.result()
            if r:
                results.append(r)
            if done % 10 == 0 or done == len(tasks):
                print(f"  {done}/{len(tasks)} checkpoints done")

    with open("backtest_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"\nFull results (including reasoning text for loss analysis) saved to backtest_results.json")

    _print_report(results)
    policy_analysis = optimize_policy_holdout(
        results, holdout_ratio=holdout_ratio, min_train_trades=min_train_trades)
    with open("backtest_policy_analysis.json", "w", encoding="utf-8") as f:
        json.dump(policy_analysis, f, indent=2)
    _print_policy_analysis(policy_analysis)


def _format_metrics(metrics: dict) -> str:
    return (
        f"{metrics['trades']} trades | win {metrics['win_rate_pct']}% | "
        f"net {metrics['net_r']:+.3f}R | PF {metrics['profit_factor']} | "
        f"expectancy {metrics['expectancy_r']}R | DD {metrics['max_drawdown_r']}R"
    )


def _print_policy_analysis(analysis: dict) -> None:
    print("\n" + "=" * 60)
    print("OUT-OF-SAMPLE POLICY ANALYSIS")
    print("=" * 60)
    if analysis["status"] != "ok":
        print(
            f"Insufficient data: {analysis.get('train_candidates', 0)} train candidates; "
            f"each policy needs at least {analysis.get('min_train_trades', 20)} decided trades."
        )
        print("No threshold recommendation was made — collecting more data is safer than overfitting.")
        return

    policy = analysis["selected_policy"]
    print(f"Selected using OLD train data only: confidence >= {policy['minimum_confidence']}, "
          f"R:R >= {policy['minimum_rr']}:1")
    print(f"  TRAIN:   {_format_metrics(analysis['train_metrics'])}")
    print(f"  HOLDOUT: {_format_metrics(analysis['holdout_metrics'])}")
    print("Holdout was not used for selection. Do not deploy this policy if holdout expectancy/PF deteriorates.")
    print("Full grid audit saved to backtest_policy_analysis.json")


def _print_report(results: list[dict]) -> None:
    print("\n" + "=" * 60)
    print("BACKTEST REPORT")
    print("=" * 60)

    by_pair: dict[str, list[dict]] = {}
    for r in results:
        by_pair.setdefault(r["symbol"], []).append(r)

    total_wins = total_losses = total_expired = total_signals = 0
    for symbol, rows in sorted(by_pair.items()):
        checks = len(rows)
        signals = [r for r in rows if r.get("outcome") and r.get("accepted", True)]
        wins = sum(1 for r in signals if r["outcome"] == "WIN")
        losses = sum(1 for r in signals if r["outcome"] == "LOSS")
        expired = sum(1 for r in signals if r["outcome"] == "EXPIRED")
        decided = wins + losses
        win_rate = round(wins / decided * 100, 1) if decided else None

        total_wins += wins
        total_losses += losses
        total_expired += expired
        total_signals += len(signals)

        print(f"\n{symbol}: {checks} checkpoints checked, {len(signals)} TRADE WATCH signals")
        print(f"  WIN: {wins}  LOSS: {losses}  EXPIRED: {expired}  "
              f"Win rate (decided only): {win_rate}%")

    decided_total = total_wins + total_losses
    overall_win_rate = round(total_wins / decided_total * 100, 1) if decided_total else None
    print(f"\n{'-' * 60}")
    print(f"OVERALL: {total_signals} signals — WIN {total_wins} / LOSS {total_losses} / "
          f"EXPIRED {total_expired} — win rate {overall_win_rate}%")

    for direction in ("BUY", "SELL"):
        dir_rows = [r for r in results
                    if r.get("direction") == direction and r.get("outcome") and r.get("accepted", True)]
        d_wins = sum(1 for r in dir_rows if r["outcome"] == "WIN")
        d_losses = sum(1 for r in dir_rows if r["outcome"] == "LOSS")
        d_decided = d_wins + d_losses
        d_rate = round(d_wins / d_decided * 100, 1) if d_decided else None
        print(f"  {direction}: {len(dir_rows)} signals — WIN {d_wins} / LOSS {d_losses} — win rate {d_rate}%")
    print(f"{'-' * 60}")

    accepted = [r for r in results if r.get("outcome") and r.get("accepted", True)]
    metrics = performance_metrics(accepted)
    print("RISK-NORMALIZED PERFORMANCE (decided accepted trades)")
    print(f"  Net: {metrics['net_r']:+.3f}R | Profit factor: {metrics['profit_factor']} | "
          f"Expectancy: {metrics['expectancy_r']}R/trade | Max drawdown: {metrics['max_drawdown_r']}R")

    filtered = [r for r in results if r.get("outcome") and r.get("accepted") is False]
    if filtered:
        filtered_metrics = performance_metrics(filtered)
        print("FILTERED CANDIDATES (counterfactual — not traded)")
        print(f"  {filtered_metrics['trades']} decided | WIN {filtered_metrics['wins']} / "
              f"LOSS {filtered_metrics['losses']} | win rate {filtered_metrics['win_rate_pct']}% | "
              f"net {filtered_metrics['net_r']:+.3f}R")
        reasons = sorted({r.get("filter_reason") for r in filtered if r.get("filter_reason")})
        for reason in reasons:
            subset = [r for r in filtered if r.get("filter_reason") == reason]
            m = performance_metrics(subset)
            print(f"    {reason}: {m['trades']} trades, {m['win_rate_pct']}% win, {m['net_r']:+.3f}R")

    for confidence in ("YUQORI", "ORTA", "PAST", None):
        subset = [r for r in results if r.get("outcome") and r.get("confidence") == confidence]
        if subset:
            m = performance_metrics(subset)
            print(f"  Confidence {confidence or 'UNPARSEABLE'}: {m['trades']} decided, "
                  f"{m['win_rate_pct']}% win, {m['net_r']:+.3f}R")
    print(f"{'-' * 60}")
    print("""
Caveats — read before trusting this number:
- No web search / fundamental context was available at each historical
  checkpoint (can't time-travel a news search), so this only tests the
  technical + structure judgment, not the full live pipeline.
- No institutional data (funding/OI/order book) either, for the same reason.
- No chart images were rendered for backtest checkpoints (text/numbers only).
- Outcome resolution uses the configured SIGNAL_EXPIRY_HOURS window and 1H
  bar highs/lows, with the same conservative same-bar-loss rule as live.
- This is one run against one historical window — re-running with a longer
  window or different checkpoint spacing will move the number around.
Treat this as a fast directional signal, not a final verdict. The live
/stats track record remains the more trustworthy long-run answer.
""")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pairs", default="BTC-USDT,ETH-USDT,SOL-USDT,XRP-USDT,BNB-USDT")
    parser.add_argument("--checkpoint-hours", type=int, default=2)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--min-age-days", type=float, default=0,
                         help="Only test checkpoints at least this many days old")
    parser.add_argument("--max-age-days", type=float, default=None,
                         help="Only test checkpoints at most this many days old (unset = no upper bound)")
    parser.add_argument("--history-pages", type=int, default=2,
                         help="15min kline pages to fetch (2 = ~20.8 days, 4 = ~41.6 days)")
    parser.add_argument("--holdout-ratio", type=float, default=0.30,
                        help="Newest fraction reserved from policy selection (default: 0.30)")
    parser.add_argument("--min-train-trades", type=int, default=20,
                        help="Minimum decided train trades required per threshold policy")
    args = parser.parse_args()

    run_backtest([p.strip() for p in args.pairs.split(",") if p.strip()],
                  args.checkpoint_hours, args.workers,
                  min_age_days=args.min_age_days, max_age_days=args.max_age_days,
                  history_pages=args.history_pages, holdout_ratio=args.holdout_ratio,
                  min_train_trades=args.min_train_trades)
