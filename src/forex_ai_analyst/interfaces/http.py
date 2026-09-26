"""The one Render entrypoint: deterministic Donchian 4h trend service, BingX VST demo only (no LLM in the trade path)."""
from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv()

from flask import Flask, abort, jsonify, request

from forex_ai_analyst.trading.application import trend_engine
from forex_ai_analyst.trading.domain.models import CandidateSignal, CandidateStatus, Decision, Direction
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


def configured_pairs() -> tuple[str, ...]:
    pairs = tuple(item.strip().upper() for item in os.environ.get(
        "MULTI_STRATEGY_PAIRS", "BTC-USDT,ETH-USDT,SOL-USDT,XRP-USDT,BNB-USDT,DOGE-USDT,ADA-USDT,LINK-USDT,AVAX-USDT,LTC-USDT").split(",") if item.strip())
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


def _vst_account_context() -> dict:
    """Build a bounded, non-sensitive VST account snapshot for risk sizing."""
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


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def position_gate_rejection(pair: str, open_signals: list[dict], closed_signals: list[dict],
                            now: datetime) -> str | None:
    """Stop the bot from stacking near-identical trades.

    One position per pair, a global cap, and a per-pair cooldown after a close.
    Limits are env-tunable:
    MAX_CONCURRENT_POSITIONS (default 6) and TRADE_COOLDOWN_MINUTES (default 60,
    per pair, measured from the last close).
    """
    pair = pair.upper()
    if any(str(item.get("pair", "")).upper() == pair for item in open_signals):
        return f"{pair} bo'yicha pozitsiya allaqachon ochiq - pozitsiya filtri"
    max_open = _env_int("MAX_CONCURRENT_POSITIONS", 6)
    if len(open_signals) >= max_open:
        return f"ochiq pozitsiyalar limiti to'lgan ({len(open_signals)}/{max_open}) - pozitsiya filtri"
    cooldown = timedelta(minutes=_env_int("TRADE_COOLDOWN_MINUTES", 60))
    for item in closed_signals:
        if str(item.get("pair", "")).upper() != pair or not item.get("outcome_time"):
            continue
        try:
            closed_at = datetime.fromisoformat(str(item["outcome_time"]))
        except ValueError:
            continue
        if closed_at.tzinfo is None:
            closed_at = closed_at.replace(tzinfo=timezone.utc)
        if now - closed_at < cooldown:
            return f"{pair} yaqinda yopilgan (cooldown {int(cooldown.total_seconds() // 60)} daqiqa) - pozitsiya filtri"
    return None


TREND_MAX_HOLD_DAYS = 60  # safety net only; the strategy exits on its stop or exit channel


def execute_trend_order(pair: str, signal: dict, risk_usdt: float, leverage: int) -> dict | None:
    """Long market order with an exchange-side stop only (no fixed take-profit)."""
    if not demo_execution_enabled():
        return None
    from forex_ai_analyst.trading.infrastructure import bingx_broker as broker
    quantity = broker.round_quantity(pair, risk_usdt / signal["stop_distance"])
    if quantity <= 0:
        raise ValueError(f"BingX VST quantity rounds to zero for {pair}; risk too small")
    order = broker.place_market_order(pair, "BUY", quantity, None, signal["stop"], leverage=leverage)
    filled = order.get("filled_quantity") or quantity
    result = {**order, "quantity": filled}
    if float(order["fill_price"]) > signal["stop"]:
        return result
    # Filled at or through the stop: never hold a position that is already stopped out.
    close_order = broker.close_position(pair, "BUY", filled)
    execution_alerts.report(f"trend-fill-through-stop:{pair}:{signal['candle_time_ms']}",
                            f"{pair} Donchian fill stopdan past bo'ldi va darhol yopildi.", severity="CRITICAL",
                            details={"pair": pair, "fill_price": order["fill_price"], "stop": signal["stop"]})
    return {**result, "unsafe_fill": True, "close_order": close_order}


def format_trend_signal(pair: str, signal: dict, params, risk_usdt: float, quantity: float,
                        broker_order: dict | None) -> str:
    execution = (f"BingX VST demo order ochildi: #{broker_order['order_id']}; fill: {broker_order['fill_price']:.6g}."
                 if broker_order else "Paper signal (VST execution o'chiq).")
    return (f"📈 DONCHIAN 4H LONG — {pair}\n\n"
            f"Breakout: 4h yopilish {signal['entry']:.6g} > {params.entry_n}-bar max {signal['channel_high']:.6g}\n"
            f"Stop ({params.stop_atr:g} ATR, birjada): {signal['stop']:.6g}\n"
            f"Chiqish: 4h yopilish {params.exit_n}-bar minimumdan pastda yoki stop\n"
            f"Risk: ${risk_usdt:.2f}; quantity: {quantity:.8g}; leverage {params.leverage}x\n" + execution)


def scan_pair(pair: str, provider: MarketDataProvider, now: datetime | None = None,
                    account_state: dict | None = None) -> list[dict]:
    """Donchian 4h long breakout (docs/LAB_REPORT.md); every decision is a deterministic rule."""
    now = now or datetime.now(timezone.utc)
    params = trend_engine.params_from_environment()
    bars = provider.fetch_closed_bars(pair, trend_engine.TIMEFRAME, max(params.history_bars, 260), now)
    signal = trend_engine.entry_signal(bars, params)
    if not signal:
        return [{"status": "SKIP", "reason": "Donchian breakout yo'q"}]
    regime = classify_market_regime(bars)
    candidate = CandidateSignal(
        strategy=trend_engine.STRATEGY, pair=pair.upper(), direction=Direction.BUY, regime=regime.regime,
        entry_price=signal["entry"], stop_price=signal["stop"], target_price=0.0,  # 0 = no fixed target
        expires_at=now + timedelta(days=TREND_MAX_HOLD_DAYS), signal_timeframe="4h", trend_timeframe="4h",
        candle_time_ms=signal["candle_time_ms"], score=100,
        confirmations=(f"4h close {signal['entry']:.6g} > {params.entry_n}-bar high {signal['channel_high']:.6g}",),
        invalidation_reason=f"4h close below {params.exit_n}-bar low, or stop",
        features={"entry_n": params.entry_n, "exit_n": params.exit_n, "stop_atr": params.stop_atr,
                  "stop_distance": signal["stop_distance"]})
    if candidate.fingerprint in scalping_storage.existing_fingerprints():
        return [{"status": "SKIP", "reason": "bu 4h breakout allaqachon ko'rib chiqilgan"}]
    account_snapshot = account_state if account_state is not None else _vst_account_context()
    if not account_snapshot.get("available"):
        return [{"status": "SKIP", "reason": "VST balance state unavailable; order yuborilmadi"}]
    runtime_rejection = runtime_controls.trade_permitted(pair, 0.0, params.leverage, 0)
    if runtime_rejection:
        key = "kill-switch" if "kill switch" in runtime_rejection else f"risk-control:{pair}:{runtime_rejection}"
        execution_alerts.report(key, f"{pair} yangi VST order bloklandi: {runtime_rejection}.",
                                severity="CRITICAL" if key == "kill-switch" else "WARNING",
                                details={"pair": pair, "reason": runtime_rejection})
        return [{"status": "SKIP", "reason": runtime_rejection}]
    execution_alerts.resolve("kill-switch", note="runtime execution permission restored")
    execution_alerts.resolve_prefix(f"risk-control:{pair}:", note="runtime execution permission restored")
    gate = position_gate_rejection(pair, scalping_storage.open_paper_signals(),
                                   scalping_storage.closed_paper_signals(50), now)
    if gate:
        logger.info("%s Donchian signal rejected by position gate: %s", pair, gate)
        return [{"status": "SKIP", "reason": gate}]
    controls = runtime_controls.settings()
    risk_limit = runtime_controls.balance_risk_limit(account_snapshot, controls)
    try:
        margin_supported = (float(account_snapshot["available_usdt"]) * float(controls["max_margin_utilization_pct"])
                            / 100 * params.leverage * signal["stop_distance"] / signal["entry"])
    except (KeyError, TypeError, ValueError):
        margin_supported = 0.0
    risk_usdt = min(risk_limit or 0.0, margin_supported)
    if risk_usdt <= 0:
        return [{"status": "SKIP", "reason": "VST balansi yoki marja risk uchun yetarli emas"}]

    try:
        broker_order = execute_trend_order(pair, signal, risk_usdt, params.leverage)
        quantity = float((broker_order or {}).get("quantity", risk_usdt / signal["stop_distance"]))
        scalping_storage.mark_accepted(Decision(candidate, CandidateStatus.ACCEPTED_PAPER, "Donchian 4h breakout"),
                                       risk_usdt, quantity, broker_order)
        if broker_order and broker_order.get("unsafe_fill"):
            close_order = broker_order["close_order"]
            scalping_storage.resolve_paper_signal(candidate.fingerprint, CandidateStatus.LOSS,
                                                  float(close_order["fill_price"]), close_order)
            return [{"status": "SKIP", "reason": "fill stopdan past bo'ldi va darhol yopildi"}]
        if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
            send_telegram_message(format_trend_signal(pair, signal, params, risk_usdt, quantity, broker_order),
                                  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
        return [{"status": "ACCEPTED_PAPER", "fingerprint": candidate.fingerprint, "strategy": trend_engine.STRATEGY}]
    except Exception as exc:
        logger.exception("Donchian VST execution failed for %s", pair)
        return [{"status": "SKIP", "reason": f"Donchian/VST execution failed: {type(exc).__name__}"}]


def scan_configured_pairs(provider: MarketDataProvider) -> None:
    account_state = _vst_account_context()
    if not account_state["available"]:
        details = {key: account_state.get(key) for key in ("category", "http_status", "bingx_code", "bingx_msg")}
        execution_alerts.report("vst-account-context-unavailable",
                                "BingX VST account holati olinmadi; scan va yangi orderlar fail-closed to‘xtatildi.",
                                details=details, remind_after_minutes=None)
        logger.warning("Skipping configured-pair scan: VST account context is unavailable")
        return
    execution_alerts.resolve("vst-account-context-unavailable", note="BingX VST account holati tiklandi; scan qayta yoqildi.",
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


@app.route("/api/reconciliation")
def reconciliation_api():
    _require_dashboard_access()
    return jsonify({"reconciliation": scalping_storage.reconciliation_status()})


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