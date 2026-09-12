"""The one Render entrypoint: deterministic, Binance-data, paper-only multi-strategy service."""
from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

from flask import Flask, abort, jsonify, request

from market_regime import classify_market_regime
from multi_strategy_scheduler import start_scheduler
from notifier import send_telegram_message
from risk_manager import assess_risk, config_from_environment
from scalping_data import MarketDataProvider, provider_from_environment
import scalping_storage
from strategy_coordinator import resolve_candidates
from strategies import breakout_retest, support_resistance_rejection, trend_pullback

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
app = Flask(__name__)

PAPER_ONLY = True
AUTO_EXECUTE_TRADES_CONFIGURED = os.environ.get("AUTO_EXECUTE_TRADES", "false").strip().lower() == "true"
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
DASHBOARD_TOKEN = os.environ.get("DASHBOARD_TOKEN", "").strip()


def configured_pairs() -> tuple[str, ...]:
    pairs = tuple(item.strip().upper() for item in os.environ.get(
        "MULTI_STRATEGY_PAIRS", "BTC-USDT,ETH-USDT,SOL-USDT,XRP-USDT,BNB-USDT").split(",") if item.strip())
    if not pairs:
        raise ValueError("MULTI_STRATEGY_PAIRS must contain at least one pair")
    return pairs


def format_paper_signal(candidate, risk) -> str:
    return (f"📈 PAPER SIGNAL — {candidate.pair} {candidate.direction}\n\n"
            f"Strategy: {candidate.strategy}\nRegime: {candidate.regime}\nQuality score: {candidate.score}/100\n\n"
            f"Entry: {candidate.entry_price:.6g}\nStop: {candidate.stop_price:.6g}\n"
            f"Target: {candidate.target_price:.6g}\nR:R: {candidate.reward_risk:.2f}\n\nTasdiqlar:\n• " +
            "\n• ".join(candidate.confirmations) + f"\n\nRisk: ${risk.risk_usdt:.2f}; quantity: {risk.quantity:.8g}\n"
            "Bu paper signal. BingX order ochilmaydi.")


def scan_pair(pair: str, provider: MarketDataProvider, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    bars_15m = provider.fetch_closed_bars(pair, "15m", 260, now)
    bars_5m = provider.fetch_closed_bars(pair, "5m", 80, now)
    regime = classify_market_regime(bars_15m)
    if bars_15m:
        scalping_storage.log_market_snapshot(pair, "15m", int(bars_15m[-1]["datetime"]),
                                             regime.regime, regime.features)
    candidates = [candidate for candidate in (
        trend_pullback.evaluate(pair, bars_15m, bars_5m, regime, now),
        support_resistance_rejection.evaluate(pair, bars_15m, bars_5m, regime, now),
        breakout_retest.evaluate(pair, bars_15m, bars_5m, regime, now),
    ) if candidate is not None]
    if not candidates:
        return []
    decisions = resolve_candidates(candidates, scalping_storage.existing_fingerprints(), now)
    results = []
    for decision in decisions:
        candidate = decision.candidate
        if decision.accepted:
            trades, pnl = scalping_storage.risk_state()
            risk = assess_risk(candidate, open_positions=scalping_storage.open_paper_positions(), daily_trades=trades,
                               daily_realized_pnl=pnl, config=config_from_environment())
            if not risk.accepted:
                from scalping_core import CandidateStatus, Decision
                decision = Decision(candidate, CandidateStatus.BLOCKED_BY_RISK, risk.reason)
            else:
                scalping_storage.mark_accepted(decision, risk.risk_usdt, risk.quantity)
                if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
                    send_telegram_message(format_paper_signal(candidate, risk), TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
        if not decision.accepted:
            scalping_storage.log_decision(decision)
        results.append({"status": decision.status, "reason": decision.reason, "fingerprint": candidate.fingerprint})
    return results


def scan_configured_pairs(provider: MarketDataProvider) -> None:
    for pair in configured_pairs():
        try:
            scan_pair(pair, provider)
        except Exception:
            logger.exception("multi-strategy scan failed for %s", pair)


def _require_dashboard_access() -> None:
    if not DASHBOARD_TOKEN:
        return
    supplied = request.args.get("token") or request.headers.get("X-Dashboard-Token", "")
    if not secrets.compare_digest(supplied, DASHBOARD_TOKEN):
        abort(401)


@app.route("/health")
def health():
    return jsonify(status="ok", service="multi-strategy-paper", paper_only=True,
                   provider=os.environ.get("MULTI_STRATEGY_PROVIDER", "").strip().lower() or "binance_futures",
                   auto_execute_trades=False,
                   auto_execute_trades_configured=AUTO_EXECUTE_TRADES_CONFIGURED), 200


@app.route("/api/signals")
def signals_api():
    _require_dashboard_access()
    return jsonify({"signals": scalping_storage.recent_candidates(request.args.get("limit", 100, type=int))})


if __name__ == "__main__":
    scalping_storage.init_db()
    provider = provider_from_environment()
    start_scheduler(scan=scan_configured_pairs, provider=provider,
                    interval_seconds=int(os.environ.get("MULTI_STRATEGY_SCAN_INTERVAL_SECONDS", "300")))
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))