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

from forex_ai_analyst.trading.application.ai_trader import AITradeDecision, decide
from forex_ai_analyst.trading.domain.indicators import higher_timeframe_bias
from forex_ai_analyst.trading.domain.models import Direction, MarketRegime
from forex_ai_analyst.trading.infrastructure.institutional_data import fetch_institutional_context
from forex_ai_analyst.trading.application.learning import summarize as learning_summary
from forex_ai_analyst.trading.infrastructure.market_intelligence import context_for_pair, status as intelligence_status
from forex_ai_analyst.knowledge import service as knowledge
from forex_ai_analyst.operations import incidents as execution_alerts
from forex_ai_analyst.trading.domain.regime import classify_market_regime
from forex_ai_analyst.trading.application.scheduler import start_scheduler
from forex_ai_analyst.shared.notifier import send_telegram_message
from forex_ai_analyst.trading.infrastructure.market_data import MarketDataProvider, provider_from_environment
from forex_ai_analyst.trading.infrastructure import signal_repository as scalping_storage
from forex_ai_analyst.operations import runtime_controls as runtime_controls
from forex_ai_analyst.interfaces.telegram_bot import configure_webhook, handle_update

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
app = Flask(__name__)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
DASHBOARD_TOKEN = os.environ.get("DASHBOARD_TOKEN", "").strip()
_VST_ACCOUNT_DIAGNOSTIC: dict = {"available": None, "last_checked_at": None}
_MIN_POST_FILL_REWARD_RISK = 1.3
_MAX_POST_FILL_RISK_MULTIPLIER = 1.05


def configured_pairs() -> tuple[str, ...]:
    pairs = tuple(item.strip().upper() for item in os.environ.get(
        "MULTI_STRATEGY_PAIRS", "BTC-USDT,ETH-USDT,SOL-USDT,XRP-USDT,BNB-USDT").split(",") if item.strip())
    if not pairs:
        raise ValueError("MULTI_STRATEGY_PAIRS must contain at least one pair")
    return pairs


def resolve_retired_static_risk_alerts() -> None:
    """Close alerts emitted by versions with now-retired fixed execution caps."""
    retired_reasons = (
        "AI risk runtime yuqori limitidan katta",
        "AI leverage runtime yuqori limitidan katta",
        "AI cooldown runtime minimumidan kichik",
    )
    for pair in configured_pairs():
        for reason in retired_reasons:
            execution_alerts.resolve(f"risk-control:{pair}:{reason}", note="fixed runtime limit retired")


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


def trade_readiness() -> dict:
    """Return safe, actionable reasons why the VST trade pipeline is blocked."""
    blockers = []
    auto_execute_configured = os.environ.get("AUTO_EXECUTE_TRADES", "false").strip().lower() == "true"
    if not auto_execute_configured:
        blockers.append("auto_execute_trades_disabled")
    if os.environ.get("KILL_SWITCH", "false").strip().lower() == "true":
        blockers.append("environment_kill_switch")
    if not os.environ.get("BINGX_API_KEY", "").strip():
        blockers.append("bingx_api_key_missing")
    if not os.environ.get("BINGX_SECRET", "").strip():
        blockers.append("bingx_secret_missing")
    standard_ai = bool(os.environ.get("OPENAI_API_KEY", "").strip())
    azure_ai = all(os.environ.get(key, "").strip() for key in (
        "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT"
    ))
    if not standard_ai and not azure_ai:
        blockers.append("ai_provider_missing")
    try:
        controls = runtime_controls.settings()
    except Exception:
        controls = None
        blockers.append("runtime_controls_unavailable")
    if controls:
        if controls["kill_switch"]:
            blockers.append("runtime_kill_switch")
        if controls["demo_execution"] is False:
            blockers.append("runtime_demo_execution_disabled")
    return {
        "ready": not blockers,
        "auto_execute_trades_configured": auto_execute_configured,
        "blockers": blockers,
        "mode": "bingx_vst_demo_only",
    }


def demo_execution_enabled() -> bool:
    """Only enable the hardcoded BingX VST (virtual-money) execution client when ready."""
    return trade_readiness()["ready"]


def execute_bingx_vst_order(candidate, decision: AITradeDecision) -> dict | None:
    if not demo_execution_enabled():
        return None
    from forex_ai_analyst.trading.infrastructure import bingx_broker as broker
    distance = abs(candidate.entry_price - candidate.stop_price)
    quantity = broker.round_quantity(candidate.pair, decision.risk_usdt / distance)
    if quantity <= 0:
        raise ValueError(f"BingX VST quantity rounds to zero for {candidate.pair}; AI risk too small")
    order = broker.place_market_order(candidate.pair, str(candidate.direction), quantity,
                                       candidate.target_price, candidate.stop_price, leverage=decision.leverage)
    # BingX's own executed quantity, when reported, can differ from what was
    # requested (precision rounding on BingX's side); trusting the requested
    # amount instead is what causes a later broker-vs-journal quantity
    # mismatch alert in scheduler.recover_open_vst_orders.
    filled_quantity = order.get("filled_quantity") or quantity
    fill = float(order["fill_price"])
    if str(candidate.direction) == "BUY":
        actual_risk_per_unit, actual_reward_per_unit = fill - candidate.stop_price, candidate.target_price - fill
    else:
        actual_risk_per_unit, actual_reward_per_unit = candidate.stop_price - fill, fill - candidate.target_price
    actual_risk = filled_quantity * actual_risk_per_unit
    actual_reward_risk = actual_reward_per_unit / actual_risk_per_unit if actual_risk_per_unit > 0 else 0.0
    accepted_risk = float(decision.risk_usdt)
    unsafe_fill = (actual_risk_per_unit <= 0 or actual_reward_per_unit <= 0 or
                   actual_reward_risk < _MIN_POST_FILL_REWARD_RISK or
                   actual_risk > accepted_risk * _MAX_POST_FILL_RISK_MULTIPLIER)
    result = {**order, "quantity": filled_quantity}
    if not unsafe_fill:
        return result

    position_side = "LONG" if str(candidate.direction) == "BUY" else "SHORT"
    details = {"pair": candidate.pair, "order_id": str(order["order_id"]),
               "fill_price": fill, "planned_risk_usdt": accepted_risk,
               "actual_risk_usdt": actual_risk, "actual_reward_risk": actual_reward_risk}
    position = broker.get_position(candidate.pair, position_side)
    if not position:
        execution_alerts.report(f"unsafe-fill-missing-position:{candidate.fingerprint}",
                                f"{candidate.pair} unsafe market filldan keyin broker pozitsiyasi topilmadi; exit fill taxmin qilinmadi.",
                                severity="CRITICAL", details=details)
        return {**result, "unsafe_fill": True}
    close_order = broker.close_position(candidate.pair, str(candidate.direction), filled_quantity)
    execution_alerts.report(f"unsafe-fill-closed:{candidate.fingerprint}",
                            f"{candidate.pair} unsafe market fill sabab darhol yopildi (post-fill R:R {actual_reward_risk:.2f}).",
                            severity="CRITICAL", details=details)
    return {**result, "unsafe_fill": True, "close_order": close_order}


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
    checked_at = datetime.now(timezone.utc).isoformat()
    if not (os.environ.get("BINGX_API_KEY", "").strip() and os.environ.get("BINGX_SECRET", "").strip()):
        diagnostic = {"available": False, "last_checked_at": checked_at, "category": "credentials",
                      "reason": "BingX VST credentials are not configured"}
        _VST_ACCOUNT_DIAGNOSTIC.clear()
        _VST_ACCOUNT_DIAGNOSTIC.update(diagnostic)
        return {**context, **diagnostic}
    try:
        from forex_ai_analyst.trading.infrastructure import bingx_broker as broker
        account = {**context, "available": True, **broker.get_vst_usdt_balance()}
        _VST_ACCOUNT_DIAGNOSTIC.clear()
        _VST_ACCOUNT_DIAGNOSTIC.update({"available": True, "last_checked_at": checked_at})
        return account
    except Exception as exc:
        diagnostic = getattr(exc, "diagnostic", {})
        safe = {"available": False, "last_checked_at": checked_at,
                "category": diagnostic.get("category", type(exc).__name__),
                "http_status": diagnostic.get("http_status"), "bingx_code": diagnostic.get("bingx_code"),
                "bingx_msg": diagnostic.get("bingx_msg"),
                "reason": "VST account query unavailable"}
        if diagnostic.get("balance_schema"):
            safe["balance_schema"] = diagnostic["balance_schema"]
        _VST_ACCOUNT_DIAGNOSTIC.clear()
        _VST_ACCOUNT_DIAGNOSTIC.update(safe)
        logger.warning("BingX VST account context unavailable: category=%s http_status=%s code=%s msg=%s",
                       safe["category"], safe["http_status"], safe["bingx_code"], safe["bingx_msg"])
        return {**context, **safe}


_TREND_BIAS_FOR_DIRECTION = {Direction.BUY: "BULLISH", Direction.SELL: "BEARISH"}


def _min_ai_confidence() -> int:
    try:
        return int(os.environ.get("AI_MIN_TRADE_CONFIDENCE", "50"))
    except ValueError:
        return 50


def mechanical_gate_rejection(direction: Direction, regime: MarketRegime,
                              higher_tf_bias: dict[str, str | None], confidence: int) -> str | None:
    """Mechanical, code-level pre-trade filters the AI's own reasoning cannot
    override. See RESEARCH_FINDINGS.md #1 (volatility regime) and #2
    (multi-timeframe alignment): both are evidence-backed, not LLM judgment.

    A bias of None ("not enough history to judge") never blocks a trade — only
    a determined, contradicting bias does.

    confidence is otherwise purely informational (never gated anywhere else):
    without this floor, a PROPOSE_TRADE at confidence 1 executes identically
    to one at confidence 99. The default (50, tunable via
    AI_MIN_TRADE_CONFIDENCE) only blocks the AI's own stated coin-flip-or-worse
    calls; it is not itself a claim about what confidence is "safe."
    """
    if confidence < _min_ai_confidence():
        return f"AI ishonchi {confidence}/100 minimal {_min_ai_confidence()} dan past - ishonch mexanik filtri"
    if regime in {MarketRegime.HIGH_VOLATILITY, MarketRegime.UNCERTAIN}:
        return f"15m rejim {regime} - volatillik mexanik filtri"
    expected = _TREND_BIAS_FOR_DIRECTION[direction]
    against = sorted(label for label, bias in higher_tf_bias.items() if bias is not None and bias != expected)
    if against:
        return f"{'/'.join(against)} trend AI yo'nalishiga zid - multi-timeframe mexanik filtri"
    return None


def scan_pair(pair: str, provider: MarketDataProvider, now: datetime | None = None,
              account_state: dict | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    bars_15m = provider.fetch_closed_bars(pair, "15m", 260, now)
    bars_5m = provider.fetch_closed_bars(pair, "5m", 120, now)
    bars_1h = provider.fetch_closed_bars(pair, "1h", 200, now)
    bars_4h = provider.fetch_closed_bars(pair, "4h", 80, now)
    bars_1d = provider.fetch_closed_bars(pair, "1d", 80, now)
    regime = classify_market_regime(bars_15m)
    higher_tf_bias = {"1h": higher_timeframe_bias(bars_1h), "4h": higher_timeframe_bias(bars_4h),
                      "1d": higher_timeframe_bias(bars_1d)}
    if bars_15m:
        scalping_storage.log_market_snapshot(pair, "15m", int(bars_15m[-1]["datetime"]),
                                             regime.regime, regime.features)
    if not bars_5m or not bars_15m:
        return [{"status": "SKIP", "reason": "yetarli yopilgan sham yo‘q"}]
    account_snapshot = account_state if account_state is not None else _vst_account_context()
    snapshot = {"pair": pair.upper(), "time_utc": now.isoformat(), "regime": str(regime.regime),
                "regime_features": regime.features, "higher_timeframe_bias": higher_tf_bias,
                "institutional": fetch_institutional_context(pair.upper()),
                "market_intelligence": context_for_pair(pair, now),
                "outcome_learning": learning_summary(scalping_storage.closed_paper_signals(500), pair),
                "account_state": account_snapshot,
                "bars_5m": bars_5m[-80:], "bars_15m": bars_15m[-120:], "bars_1h": bars_1h[-120:]}
    excerpts = knowledge.search(f"{pair} {regime.regime} trend volatility risk")
    controls = runtime_controls.settings()
    account_state = account_snapshot
    ai = decide(snapshot, excerpts, scalping_storage.recent_ai_reviews(pair), controls)
    if not ai.proposes_trade:
        return [{"status": ai.action, "reason": ai.rationale}]
    from forex_ai_analyst.trading.domain.models import CandidateStatus, Decision
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
        execution_alerts.resolve_prefix(f"risk-control:{pair}:", note="runtime execution permission restored")
        gate_rejection = mechanical_gate_rejection(ai.direction, regime.regime, higher_tf_bias, ai.confidence)
        if gate_rejection:
            key = f"mechanical-gate:{pair}:{gate_rejection}"
            execution_alerts.report(key, f"{pair} yangi VST order rad etildi (mexanik filtr): {gate_rejection}.",
                                    severity="WARNING", details={"pair": pair, "reason": gate_rejection})
            return [{"status": "SKIP", "reason": gate_rejection}]
        execution_alerts.resolve_prefix(f"mechanical-gate:{pair}:", note="mechanical gate conditions cleared")
        if candidate.fingerprint in scalping_storage.existing_fingerprints():
            return [{"status": "SKIP", "reason": "duplicate AI candle decision"}]
        broker_order = execute_bingx_vst_order(candidate, ai)
        quantity = float((broker_order or {}).get("quantity", ai.risk_usdt / abs(candidate.entry_price - candidate.stop_price)))
        accepted = Decision(candidate, CandidateStatus.ACCEPTED_PAPER, "AI contextual decision")
        scalping_storage.mark_accepted(accepted, ai.risk_usdt, quantity, broker_order)
        if broker_order and broker_order.get("unsafe_fill"):
            close_order = broker_order.get("close_order")
            if close_order:
                scalping_storage.resolve_paper_signal(candidate.fingerprint, CandidateStatus.TIME_EXIT,
                                                      float(close_order["fill_price"]), close_order)
                return [{"status": "SKIP", "reason": "unsafe post-fill execution was immediately closed"}]
            return [{"status": "SKIP", "reason": "unsafe post-fill execution requires broker reconciliation"}]
        if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
            send_telegram_message(format_paper_signal(candidate, ai, quantity, broker_order), TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
        return [{"status": "ACCEPTED_PAPER", "fingerprint": candidate.fingerprint, "ai": ai.raw}]
    except Exception as exc:
        logger.exception("AI VST execution failed for %s", pair)
        return [{"status": "SKIP", "reason": f"AI/VST execution failed: {type(exc).__name__}"}]


def scan_configured_pairs(provider: MarketDataProvider) -> None:
    account_state = _vst_account_context()
    if not account_state["available"]:
        details = {key: account_state.get(key) for key in ("category", "http_status", "bingx_code", "bingx_msg")}
        execution_alerts.report("vst-account-context-unavailable",
                                "BingX VST account holati olinmadi; AI scan va yangi orderlar fail-closed to‘xtatildi.",
                                details=details, remind_after_minutes=None)
        logger.warning("Skipping configured-pair AI scan: VST account context is unavailable")
        return
    execution_alerts.resolve("vst-account-context-unavailable", note="BingX VST account holati tiklandi; AI scan qayta yoqildi.",
                             notify=True)
    for pair in configured_pairs():
        try:
            scan_pair(pair, provider, account_state=account_state)
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
    readiness = trade_readiness()
    demo_execution = readiness["ready"]
    try:
        alerts = execution_alerts.status()
    except Exception:
        alerts = {"unavailable": True}
    return jsonify(status="ok", service="multi-strategy-paper", paper_only=not demo_execution, demo_only=True,
                   provider=os.environ.get("MULTI_STRATEGY_PROVIDER", "").strip().lower() or "bingx",
                   auto_execute_trades=demo_execution,
                   auto_execute_trades_configured=readiness["auto_execute_trades_configured"],
                   trade_readiness=readiness,
                   execution_alerts=alerts, vst_account=dict(_VST_ACCOUNT_DIAGNOSTIC)), 200


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


def initialize() -> None:
    """Initialize persistence, integrations, and background jobs."""
    scalping_storage.init_db()
    execution_alerts.init_db()
    resolve_retired_static_risk_alerts()
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


def main() -> None:
    """Start the production HTTP process."""
    initialize()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))


if __name__ == "__main__":
    main()