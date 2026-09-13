"""The one Render entrypoint: deterministic, Binance-data, paper-only multi-strategy service."""
from __future__ import annotations

import logging
import os
import secrets
from dataclasses import replace
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

from flask import Flask, abort, jsonify, request

from ai_trader import AITradeDecision, decide
from institutional_data import fetch_institutional_context
from learning import summarize as learning_summary
from market_intelligence import context_for_pair, status as intelligence_status
import knowledge
import execution_alerts
from market_regime import classify_market_regime
from multi_strategy_scheduler import start_scheduler
from notifier import send_telegram_message
from scalping_data import MarketDataProvider, provider_from_environment
import scalping_storage
import runtime_controls
from telegram_bot import configure_webhook, handle_update

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
app = Flask(__name__)

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


def format_paper_signal(candidate, decision: AITradeDecision, quantity: float, broker_order: dict | None = None) -> str:
    execution_note = (f"BingX VST demo order ochildi: #{broker_order['order_id']}; "
                      f"fill: {broker_order['fill_price']:.6g}."
                      if broker_order else "Bu paper signal. BingX order ochilmaydi.")
    return (f"🤖 AI VST SIGNAL — {candidate.pair} {candidate.direction}\n\n"
            f"Regime: {candidate.regime}\nAI ishonchi: {decision.confidence}/100\n\n"
            f"Entry: {candidate.entry_price:.6g}\nStop: {candidate.stop_price:.6g}\n"
            f"Target: {candidate.target_price:.6g}\nR:R: {candidate.reward_risk:.2f}\n\nTasdiqlar:\n• " +
            "\n• ".join(candidate.confirmations) + f"\n\nAI risk: ${decision.risk_usdt:.2f}; leverage: {decision.leverage}x; quantity: {quantity:.8g}\n"
            f"Cooldown: {decision.cooldown_minutes} daqiqa\nInvalidation: {decision.invalidation}\n"
            + execution_note)


def demo_execution_enabled() -> bool:
    """Only enable the hardcoded BingX VST (virtual-money) execution client with both credentials."""
    try:
        controls = runtime_controls.settings()
    except Exception:
        return False
    runtime_execution = controls["demo_execution"]
    return (not controls["kill_switch"] and runtime_execution is not False and
            os.environ.get("KILL_SWITCH", "false").strip().lower() != "true" and
            os.environ.get("AUTO_EXECUTE_TRADES", "false").strip().lower() == "true" and
            bool(os.environ.get("BINGX_API_KEY", "").strip()) and bool(os.environ.get("BINGX_SECRET", "").strip()))


def execute_bingx_vst_order(candidate, decision: AITradeDecision) -> dict | None:
    if not demo_execution_enabled():
        return None
    import broker
    distance = abs(candidate.entry_price - candidate.stop_price)
    quantity = broker.round_quantity(candidate.pair, decision.risk_usdt / distance)
    if quantity <= 0:
        raise ValueError(f"BingX VST quantity rounds to zero for {candidate.pair}; AI risk too small")
    order = broker.place_market_order(candidate.pair, str(candidate.direction), quantity,
                                       candidate.target_price, candidate.stop_price, leverage=decision.leverage)
    return {**order, "quantity": quantity}


def _constrain_ai_decision(decision: AITradeDecision, controls: dict, account_state: dict) -> AITradeDecision:
    """Constrain AI risk by current VST equity, daily loss budget and margin.

    There is deliberately no fixed-USDT risk cap, leverage cap below BingX's
    125x technical limit, or minimum cooldown.  The account's live available
    margin and the AI-selected stop/leverage determine what it can support.
    """
    risk_limit = runtime_controls.balance_risk_limit(account_state, controls)
    if risk_limit is None:
        raise ValueError("VST balance state is unavailable for risk sizing")
    try:
        available = float(account_state["available_usdt"])
        margin_pct = float(controls["max_margin_utilization_pct"])
        distance = abs(float(decision.entry_price) - float(decision.stop_price))
        entry = float(decision.entry_price)
    except (KeyError, TypeError, ValueError):
        raise ValueError("VST available margin state is invalid") from None
    if available <= 0 or not 0 < margin_pct <= 100 or distance <= 0 or entry <= 0:
        raise ValueError("VST account cannot support a new risk-sized position")
    # risk / stop_distance is quantity; quantity * entry / leverage is margin.
    margin_supported_risk = available * margin_pct / 100 * decision.leverage * distance / entry
    return replace(decision, risk_usdt=min(decision.risk_usdt, risk_limit, margin_supported_risk))


def _vst_account_context() -> dict:
    """Build a bounded, non-sensitive VST account snapshot for AI sizing context."""
    context = {"available": False, "open_strategy_positions": scalping_storage.open_paper_positions(),
               "daily_strategy_pnl_usdt": scalping_storage.risk_state()[1]}
    if not (os.environ.get("BINGX_API_KEY", "").strip() and os.environ.get("BINGX_SECRET", "").strip()):
        return {**context, "reason": "BingX VST credentials are not configured"}
    try:
        import broker
        return {**context, "available": True, **broker.get_vst_usdt_balance()}
    except Exception as exc:
        logger.warning("BingX VST account context unavailable: %s", type(exc).__name__)
        return {**context, "reason": f"VST account query unavailable: {type(exc).__name__}"}


def scan_pair(pair: str, provider: MarketDataProvider, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    bars_15m = provider.fetch_closed_bars(pair, "15m", 260, now)
    bars_5m = provider.fetch_closed_bars(pair, "5m", 120, now)
    bars_1h = provider.fetch_closed_bars(pair, "1h", 200, now)
    regime = classify_market_regime(bars_15m)
    if bars_15m:
        scalping_storage.log_market_snapshot(pair, "15m", int(bars_15m[-1]["datetime"]),
                                             regime.regime, regime.features)
    if not bars_5m or not bars_15m:
        return [{"status": "SKIP", "reason": "yetarli yopilgan sham yo‘q"}]
    snapshot = {"pair": pair.upper(), "time_utc": now.isoformat(), "regime": str(regime.regime),
                "regime_features": regime.features, "institutional": fetch_institutional_context(pair.upper()),
                "market_intelligence": context_for_pair(pair, now),
                "outcome_learning": learning_summary(scalping_storage.closed_paper_signals(500), pair),
                 "account_state": _vst_account_context(),
                "bars_5m": bars_5m[-80:], "bars_15m": bars_15m[-120:], "bars_1h": bars_1h[-120:]}
    excerpts = knowledge.search(f"{pair} {regime.regime} trend volatility risk")
    controls = runtime_controls.settings()
    account_state = snapshot["account_state"]
    ai = decide(snapshot, excerpts, scalping_storage.recent_ai_reviews(pair), controls)
    if not ai.proposes_trade:
        return [{"status": ai.action, "reason": ai.rationale}]
    from scalping_core import CandidateStatus, Decision
    try:
        # Journaled proposals and VST orders use identical balance-relative
        # sizing, so no proposal is accepted without a fresh account snapshot.
        if not account_state["available"]:
            return [{"status": "SKIP", "reason": "VST balance state unavailable; order yuborilmadi"}]
        ai = _constrain_ai_decision(ai, controls, account_state)
        candidate = ai.to_candidate(pair, regime.regime, int(bars_5m[-1]["datetime"]), now)
        runtime_rejection = runtime_controls.trade_permitted(pair, ai.risk_usdt, ai.leverage, ai.cooldown_minutes)
        if runtime_rejection:
            key = "kill-switch" if "kill switch" in runtime_rejection else f"risk-control:{pair}:{runtime_rejection}"
            execution_alerts.report(key, f"{pair} yangi VST order bloklandi: {runtime_rejection}.",
                                    severity="CRITICAL" if key == "kill-switch" else "WARNING",
                                    details={"pair": pair, "reason": runtime_rejection})
            return [{"status": "SKIP", "reason": runtime_rejection}]
        execution_alerts.resolve("kill-switch", note="runtime execution permission restored")
        if candidate.fingerprint in scalping_storage.existing_fingerprints():
            return [{"status": "SKIP", "reason": "duplicate AI candle decision"}]
        broker_order = execute_bingx_vst_order(candidate, ai)
        quantity = float((broker_order or {}).get("quantity", ai.risk_usdt / abs(candidate.entry_price - candidate.stop_price)))
        accepted = Decision(candidate, CandidateStatus.ACCEPTED_PAPER, "AI contextual decision")
        scalping_storage.mark_accepted(accepted, ai.risk_usdt, quantity, broker_order)
        if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
            send_telegram_message(format_paper_signal(candidate, ai, quantity, broker_order), TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
        return [{"status": "ACCEPTED_PAPER", "fingerprint": candidate.fingerprint, "ai": ai.raw}]
    except Exception as exc:
        logger.exception("AI VST execution failed for %s", pair)
        return [{"status": "SKIP", "reason": f"AI/VST execution failed: {type(exc).__name__}"}]


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
    demo_execution = demo_execution_enabled()
    try:
        alerts = execution_alerts.status()
    except Exception:
        alerts = {"unavailable": True}
    return jsonify(status="ok", service="multi-strategy-paper", paper_only=not demo_execution, demo_only=True,
                   provider=os.environ.get("MULTI_STRATEGY_PROVIDER", "").strip().lower() or "bingx",
                   auto_execute_trades=demo_execution,
                   auto_execute_trades_configured=AUTO_EXECUTE_TRADES_CONFIGURED,
                   execution_alerts=alerts), 200


@app.route("/api/signals")
def signals_api():
    _require_dashboard_access()
    return jsonify({"signals": scalping_storage.recent_candidates(request.args.get("limit", 100, type=int))})


@app.route("/api/intelligence")
def intelligence_api():
    _require_dashboard_access()
    return jsonify({"market_intelligence": intelligence_status(),
                    "reconciliation": scalping_storage.reconciliation_status()})


@app.route("/telegram/webhook", methods=["POST"])
def telegram_webhook():
    secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "").strip()
    if secret and not secrets.compare_digest(request.headers.get("X-Telegram-Bot-Api-Secret-Token", ""), secret):
        abort(401)
    handle_update(request.get_json(silent=True) or {})
    return jsonify({"ok": True})


if __name__ == "__main__":
    scalping_storage.init_db()
    execution_alerts.init_db()
    runtime_controls.init_db()
    knowledge.init_db()
    if os.environ.get("TELEGRAM_BOT_TOKEN", "").strip():
        if configure_webhook():
            logger.info("Telegram webhook and command menu configured")
        else:
            logger.warning("Telegram webhook not configured; set HTTPS PUBLIC_BASE_URL and TELEGRAM_WEBHOOK_SECRET for commands and buttons")
    provider = provider_from_environment()
    start_scheduler(scan=scan_configured_pairs, provider=provider,
                    interval_seconds=int(os.environ.get("MULTI_STRATEGY_SCAN_INTERVAL_SECONDS", "300")))
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))