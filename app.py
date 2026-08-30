import json
import logging
import os
import re
from datetime import datetime, time, timezone

from dotenv import load_dotenv
from flask import Flask, jsonify
from openai import OpenAI

import broker
import storage
from charting import bars_to_chart_base64
from indicators import atr_pct
from institutional_data import fetch_institutional_context, format_institutional_context
from market_data import fetch_bars, fetch_multi_timeframe
from notifier import send_telegram_message
from scheduler import start_scheduler

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler("bot.log"), logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

AZURE_OPENAI_API_KEY = os.environ["AZURE_OPENAI_API_KEY"]
AZURE_OPENAI_ENDPOINT = os.environ["AZURE_OPENAI_ENDPOINT"]
AZURE_OPENAI_DEPLOYMENT = os.environ["AZURE_OPENAI_DEPLOYMENT"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# Crypto perpetuals trade 24/7 — this stays available for anyone who wants a
# restricted window, but defaults to no restriction at all.
TRADING_DAYS = os.environ.get("TRADING_DAYS", "ALL")
TRADING_WINDOW_START = os.environ.get("TRADING_WINDOW_START", "00:00")
TRADING_WINDOW_END = os.environ.get("TRADING_WINDOW_END", "23:59")

# Fallback target/stop if ATR can't be computed (e.g. not enough 1H history yet).
# Normal operation uses ATR-based dynamic stops instead — see compute_stop_target_pct.
FALLBACK_TARGET_PCT, FALLBACK_STOP_PCT = (
    float(x) for x in os.environ.get("TARGET_PCT_STOP_PCT", "1.5/0.6").split("/")
)
# 2x ATR is the most broadly-validated general-purpose multiplier for BTC/ETH-style
# intraday setups (not fit to our own backtest sample — this is standard practice:
# https://www.luxalgo.com/blog/5-atr-stop-loss-strategies-for-risk-control/).
# Target stays at the same reward:risk ratio our fixed 1.5/0.6 config already used.
ATR_MULTIPLIER = float(os.environ.get("ATR_MULTIPLIER", "2.0"))
REWARD_RISK_RATIO = float(os.environ.get("REWARD_RISK_RATIO", "2.5"))


def compute_stop_target_pct(bars_1h: list[dict]) -> tuple[float, float]:
    """Volatility-adjusted stop (ATR_MULTIPLIER x ATR%) and target (stop x
    REWARD_RISK_RATIO), so each pair's stop reflects its own current volatility
    instead of one fixed percentage for every pair regardless of how it's moving."""
    atr = atr_pct(bars_1h)
    if atr is None:
        return FALLBACK_TARGET_PCT, FALLBACK_STOP_PCT
    stop_pct = atr * ATR_MULTIPLIER
    target_pct = stop_pct * REWARD_RISK_RATIO
    return target_pct, stop_pct

AUTO_EXECUTE_TRADES = os.environ.get("AUTO_EXECUTE_TRADES", "false").strip().lower() == "true"
POSITION_SIZE_USDT = float(os.environ.get("POSITION_SIZE_USDT", "100"))  # fallback only, if equity calc fails
RISK_PCT_PER_TRADE = float(os.environ.get("RISK_PCT_PER_TRADE", "1.5"))  # % of equity risked if stop is hit
# NOT BingX's demo wallet balance (~$99,932 by default — an unrealistic size
# that would make every trade's risk math simulate a $100k account). This is
# the capital you'd actually plan to deposit for real, so sizing and P&L stay
# a meaningful simulation of "what would happen with real money", not
# BingX's inflated demo playground balance.
STARTING_EQUITY_USDT = float(os.environ.get("STARTING_EQUITY_USDT", "200"))
LEVERAGE = int(os.environ.get("LEVERAGE", "3"))
# Ceiling expressed as max MARGIN used per trade (not raw notional) — a flat
# % of notional makes no sense once leverage is in the picture (it would
# either never bind at small equity or be meaninglessly loose at large
# equity). 20% of equity as margin, at LEVERAGE x, is the actual notional cap.
MAX_MARGIN_PCT_OF_EQUITY = 0.20

WEEKDAY_CODES = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]

with open("system_prompt.txt", encoding="utf-8") as f:
    SYSTEM_PROMPT = f.read()

openai_client = OpenAI(base_url=AZURE_OPENAI_ENDPOINT, api_key=AZURE_OPENAI_API_KEY)


def in_trading_window(now_utc: datetime) -> bool:
    if TRADING_DAYS.strip().upper() == "ALL":
        days_ok = True
    else:
        allowed_days = {d.strip().upper() for d in TRADING_DAYS.split(",")}
        days_ok = WEEKDAY_CODES[now_utc.weekday()] in allowed_days

    start_h, start_m = (int(x) for x in TRADING_WINDOW_START.split(":"))
    end_h, end_m = (int(x) for x in TRADING_WINDOW_END.split(":"))
    window_ok = time(start_h, start_m) <= now_utc.time() <= time(end_h, end_m)

    return days_ok and window_ok


def _pct_change(bars: list[dict], bars_back: int) -> float | None:
    """% change from `bars_back` bars ago (bars[bars_back]) to the most recent
    close (bars[0]). Bars are most-recent-first."""
    if len(bars) <= bars_back:
        return None
    old_close, new_close = bars[bars_back]["close"], bars[0]["close"]
    if not old_close:
        return None
    return (new_close - old_close) / old_close * 100


def build_market_context_text(symbol: str) -> str:
    """BTC still leads the whole crypto market's direction most of the time —
    a real trader always has a 'what's BTC doing' backdrop even when trading
    an altcoin. Cheap to fetch: BTC-USDT's 1h/4h bars get refreshed every
    cycle anyway (BTC is itself one of the watched pairs), so this almost
    always hits market_data's cache rather than spending extra rate-limit
    budget. Deliberately just raw % moves, not a structure call — the model
    already reads structure far better from the actual chart than a % number
    could summarize; this is just the fast 'which way is the tide' context."""
    if symbol == "BTC-USDT":
        return ""  # BTC's own analysis already covers this directly
    try:
        btc_1h = fetch_bars("BTC-USDT", "1h", outputsize=24)
        btc_4h = fetch_bars("BTC-USDT", "4h", outputsize=6)
    except Exception:
        logger.exception("Failed to fetch BTC market context")
        return "Global market context (BTC-USDT): unavailable this cycle.\n\n"

    change_4h = _pct_change(btc_1h, 4)
    change_24h = _pct_change(btc_4h, 5)
    if change_4h is None and change_24h is None:
        return ""

    parts = []
    if change_4h is not None:
        parts.append(f"last 4h {change_4h:+.2f}%")
    if change_24h is not None:
        parts.append(f"last ~24h {change_24h:+.2f}%")
    return (
        f"Global market context — BTC-USDT (crypto market's usual directional leader) "
        f"is {', '.join(parts)}. Weigh this as backdrop: an altcoin setup that fights a "
        f"strongly moving BTC is inherently higher risk, one that aligns with it is "
        f"lower risk — but this is context, not a gate on its own.\n\n"
    )


def build_session_context_text(now_utc: datetime) -> str:
    """Crypto trades 24/7, but liquidity isn't flat across the day — thinner
    books mean wicks/fakeouts are more common and a 'clean' break is more
    likely to be noise. A real trader carries this awareness; give the model
    the same. Rough UTC session bands, not exact, just the general shape."""
    hour = now_utc.hour
    is_weekend = now_utc.weekday() >= 5  # Saturday=5, Sunday=6

    if 12 <= hour < 16:
        session = "London/Nyu-York ustma-ust tushishi — kunning eng yuqori likvidlik oynasi"
    elif 7 <= hour < 12 or 16 <= hour < 21:
        session = "London yoki Nyu-York sessiyasi — normal likvidlik"
    elif 0 <= hour < 7:
        session = "Osiyo sessiyasi — odatda pastroq likvidlik, ba'zan diapazonda harakat"
    else:  # 21:00-24:00 UTC
        session = "Nyu-York yopilgandan keyingi, Osiyo hali to'liq boshlanmagan oyna — odatda eng past likvidlik kunning"

    weekend_note = " Dam olish kuni — hajmlar odatda haftaning ish kunlaridan past." if is_weekend else ""
    return (f"Session context: {now_utc.strftime('%H:%M')} UTC, {session}.{weekend_note} "
            f"Past likvidlik oynasida sindirish/breakout'larga ko'proq shubha bilan qarang — "
            f"soxta sindirish (fakeout) ehtimoli yuqoriroq.\n\n")


def build_analysis_text(symbol: str, bars: dict, institutional: dict, target_pct: float, stop_pct: float) -> str:
    return (
        f"Pair: {symbol} (perpetual futures, USDT-margined)\n"
        f"Current UTC time: {datetime.now(timezone.utc).isoformat()}\n"
        f"Configured target: {target_pct:.3f}% , max stop: {stop_pct:.3f}% "
        f"(volatility-adjusted: {ATR_MULTIPLIER}x current 1H ATR, {REWARD_RISK_RATIO}:1 reward:risk)\n\n"
        f"{format_institutional_context(institutional)}\n\n"
        f"{build_session_context_text(datetime.now(timezone.utc))}"
        f"{build_market_context_text(symbol)}"
        f"{build_trade_history_text(symbol)}\n\n"
        f"1D bars (macro trend context, most recent first):\n{json.dumps(bars.get('1d', [])[:30])}\n\n"
        f"4H bars (most recent first):\n{json.dumps(bars.get('4h', [])[:20])}\n\n"
        f"1H bars:\n{json.dumps(bars.get('1h', [])[:24])}\n\n"
        f"15min bars:\n{json.dumps(bars.get('15min', [])[:20])}\n\n"
        f"5min bars:\n{json.dumps(bars.get('5min', [])[:20])}\n"
    )


def build_analysis_input(symbol: str, bars: dict, institutional: dict, target_pct: float, stop_pct: float) -> list:
    """Multi-part input: the numeric text block plus rendered 1H/15min chart
    images, so the model gets a real visual read alongside the precise numbers."""
    content = [{"type": "input_text",
                "text": build_analysis_text(symbol, bars, institutional, target_pct, stop_pct)}]

    for label, tf_name in (("1h", "1 soatlik"), ("15min", "15 daqiqalik")):
        chart_b64 = bars_to_chart_base64(bars.get(label, []), title=f"{symbol} {tf_name}")
        if chart_b64:
            content.append({"type": "input_text", "text": f"({tf_name} grafik rasmi quyida)"})
            content.append({"type": "input_image", "image_url": f"data:image/png;base64,{chart_b64}"})

    return [{"role": "user", "content": content}]


def run_analysis(model_input: list) -> str:
    response = openai_client.responses.create(
        model=AZURE_OPENAI_DEPLOYMENT,
        instructions=SYSTEM_PROMPT,
        input=model_input,
        tools=[{"type": "web_search_preview"}],
    )
    return response.output_text


def extract_recommendation(analysis: str) -> str:
    """Reads the model's own 'TAVSIYA: ...' line. The model decides — there's no
    separate indicator gating this; it's purely the AI's judgment."""
    for line in analysis.splitlines():
        normalized = re.sub(r"[’‘'ʻʼ`´]", "", line).upper()
        if "TAVSIYA" in normalized:
            if "SAVDONI KUZATISH" in normalized:
                return "TRADE_WATCH"
            if "OTKAZIB YUBORISH" in normalized:
                return "SKIP"
    return "UNKNOWN"


def extract_direction(analysis: str) -> str | None:
    """Reads the model's own 'Yo'nalish: ...' line, used to compute target/stop
    prices for outcome tracking. Returns 'BUY', 'SELL', or None if unparseable."""
    for line in analysis.splitlines():
        normalized = re.sub(r"[’‘'ʻʼ`´]", "", line).upper()
        if "YONALISH" in normalized:
            if "SOTIB OLISH" in normalized:
                return "BUY"
            if "SOTISH" in normalized:
                return "SELL"
    return None


def extract_reasoning(analysis: str) -> str | None:
    """Reads the model's own 'ASOSLASH: ...' line — used to give a future check
    a one-line summary of *why* a past trade was taken, not just its outcome."""
    for line in analysis.splitlines():
        if line.upper().startswith("ASOSLASH"):
            return line.split(":", 1)[1].strip() if ":" in line else line.strip()
    return None


def build_trade_history_text(symbol: str) -> str:
    """Last few resolved trades for this pair, as soft context for the next
    check — NOT a rule to extrapolate from (see system_prompt.txt for how the
    model is told to use this). Sample per pair is small, so this is framed as
    awareness, not a pattern."""
    try:
        history = storage.get_recent_resolved_signals(symbol, limit=15)
    except Exception:
        logger.exception("%s: failed to fetch trade history", symbol)
        return "Recent trade history: unavailable this cycle."

    if not history:
        return "Recent trade history for this pair: none yet (no resolved trades)."

    lines = ["Recent trade history for this pair (most recent first, small sample — see instructions):"]
    for row in history:
        reasoning = extract_reasoning(row.get("analysis_text") or "") or "(no reasoning captured)"
        lines.append(
            f"- {row['signal_time'][:16]} UTC — {row['direction']} @ {row['entry_price']} "
            f"-> {row['outcome']} @ {row['outcome_price']}. Reasoning was: {reasoning[:220]}"
        )
    return "\n".join(lines)


_CONFIDENCE_MULTIPLIERS = {"YUQORI": 1.0, "ORTA": 0.7, "PAST": 0.45}


def extract_confidence(analysis: str) -> str | None:
    """Reads the model's own 'Ishonch darajasi: ...' line. Returns 'YUQORI',
    'ORTA', 'PAST', or None if unparseable (treated as ORTA — see caller)."""
    for line in analysis.splitlines():
        normalized = re.sub(r"[’‘'ʻʼ`´]", "", line).upper()
        if "ISHONCH DARAJASI" in normalized:
            for level in ("YUQORI", "ORTA", "PAST"):
                if level in normalized:
                    return level
    return None


def extract_price(analysis: str, label: str) -> float | None:
    """Reads a numeric price off a labeled line (e.g. 'Stop-loss narxi: 63,850')."""
    for line in analysis.splitlines():
        if label in line.upper():
            match = re.search(r"[\d][\d,]*\.?\d*", line)
            if match:
                try:
                    return float(match.group().replace(",", ""))
                except ValueError:
                    return None
    return None


def analyze_and_notify(symbol: str) -> dict:
    """Shared pipeline: trading-window check -> fetch bars -> full AI read -> Telegram
    only when the AI itself calls a new TRADE WATCH. Runs on every scheduler tick for
    every watched pair — the AI decides each time, not a mechanical indicator."""
    now_utc = datetime.now(timezone.utc)
    if not in_trading_window(now_utc):
        logger.info("%s skipped — outside trading window (%s UTC)", symbol, now_utc.isoformat())
        return {"status": "ignored", "reason": "outside trading window"}

    logger.info("Analyzing %s", symbol)

    try:
        bars = fetch_multi_timeframe(symbol)
        institutional = fetch_institutional_context(symbol)
        target_pct, stop_pct = compute_stop_target_pct(bars.get("1h", []))
        model_input = build_analysis_input(symbol, bars, institutional, target_pct, stop_pct)
        analysis = run_analysis(model_input)
    except Exception:
        logger.exception("Analysis failed for %s", symbol)
        return {"status": "error", "reason": "analysis failed"}

    recommendation = extract_recommendation(analysis)
    logger.info("%s verdict: %s", symbol, recommendation)

    if recommendation != "TRADE_WATCH":
        # Log the model's own one-line reasoning even on SKIP — otherwise there's
        # no way to ever tell, from the logs, WHY it's skipping (too strict a
        # rule vs. genuinely quiet market vs. something misconfigured).
        reasoning = extract_reasoning(analysis)
        if reasoning:
            logger.info("%s reasoning: %s", symbol, reasoning)
        return {"status": "no_signal", "recommendation": recommendation}

    if storage.has_open_signal(symbol):
        # already have an open, unresolved signal for this pair — don't stack
        # another one on top. DB-backed (not in-memory) so this holds even
        # across a redeploy mid-setup, unlike a plain in-process dedup would.
        logger.info("%s: TRADE_WATCH but already has an open signal, skipping", symbol)
        return {"status": "no_signal", "recommendation": recommendation, "reason": "already open"}

    message, executed = _log_and_maybe_execute_signal(
        symbol, bars, analysis, target_pct, stop_pct, institutional.get("funding_rate_pct"))

    sent = send_telegram_message(message, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
    logger.info("Signal sent for %s (telegram sent: %s, executed: %s)", symbol, sent, executed)

    return {"status": "signal_sent", "telegram_sent": sent, "executed": executed}


def _resolve_target_stop(entry_price: float, direction: str, ai_target: float | None, ai_stop: float | None,
                          fallback_target_pct: float, fallback_stop_pct: float) -> tuple[float, float, str]:
    """Prefer the model's own structural stop/target (real SMC/S&R levels) over
    a formula. The ATR-based percentage only acts as a sanity bound — reject the
    model's numbers if they're on the wrong side of entry, basically zero distance
    (parsing noise), or wildly outside a sane multiple of the ATR estimate (a sign
    of a misread/typo'd level), and fall back to the percentage-based calc."""
    if ai_stop is not None and ai_target is not None:
        stop_pct_actual = abs(entry_price - ai_stop) / entry_price * 100
        on_correct_side = (direction == "BUY" and ai_stop < entry_price < ai_target) or \
                           (direction == "SELL" and ai_target < entry_price < ai_stop)
        sane_distance = 0.05 <= stop_pct_actual <= max(fallback_stop_pct * 6, 3.0)
        if on_correct_side and sane_distance:
            return ai_target, ai_stop, "structural"
        logger.warning("Model's stop/target failed sanity check (stop=%s target=%s entry=%s dir=%s) — "
                        "falling back to ATR-based percentage", ai_stop, ai_target, entry_price, direction)

    if direction == "BUY":
        target_price = entry_price * (1 + fallback_target_pct / 100)
        stop_price = entry_price * (1 - fallback_stop_pct / 100)
    else:
        target_price = entry_price * (1 - fallback_target_pct / 100)
        stop_price = entry_price * (1 + fallback_stop_pct / 100)
    return target_price, stop_price, "atr_fallback"


def _correlation_size_multiplier(direction: str) -> tuple[float, int]:
    """The 5 watched pairs move together most of the time — 3 simultaneous BUYs
    isn't diversification, it's the same directional bet 3x over. A real trader
    sizes down as their same-direction exposure stacks up, even across
    'different' correlated assets. Returns (multiplier, same_direction_count).
    Formula: 1/(1+n), floored at 0.3x so it never goes to a token size."""
    try:
        same_direction = sum(1 for s in storage.get_open_signals() if s["direction"] == direction)
    except Exception:
        logger.exception("Failed to compute correlation exposure — defaulting to full size")
        return 1.0, 0
    return max(0.3, 1 / (1 + same_direction)), same_direction


def _risk_based_position_size(stop_distance_pct: float) -> tuple[float, str]:
    """Position notional such that, if the stop is hit, the loss equals
    RISK_PCT_PER_TRADE % of equity — not a fixed dollar amount regardless of
    account size. Tighter structural stops justify a bigger position (same $
    risk); wider stops get a smaller one. 'Equity' here is STARTING_EQUITY_USDT
    plus our own tracked realized P&L (not BingX's demo wallet balance — see
    STARTING_EQUITY_USDT's definition for why), so this simulates what would
    actually happen starting from the capital you plan to really deposit.
    Capped at MAX_MARGIN_PCT_OF_EQUITY of equity used as margin (at LEVERAGE x,
    that's the notional cap) as a hard ceiling — protects against a freak
    very-tight stop blowing this up — and falls back to the flat
    POSITION_SIZE_USDT if the P&L lookup itself fails. Returns (size_usdt, source)."""
    try:
        realized_pnl = storage.get_stats()["all_time"]["realized_pnl_usdt"]
    except Exception:
        logger.exception("Failed to fetch realized P&L for equity calc — falling back to flat POSITION_SIZE_USDT")
        return POSITION_SIZE_USDT, "flat_fallback"

    equity = max(STARTING_EQUITY_USDT + realized_pnl, 10.0)  # floor so a deep drawdown can't zero/invert sizing

    if stop_distance_pct <= 0:
        return POSITION_SIZE_USDT, "flat_fallback"

    risk_based = equity * RISK_PCT_PER_TRADE / stop_distance_pct
    notional_cap = equity * MAX_MARGIN_PCT_OF_EQUITY * LEVERAGE
    capped = min(risk_based, notional_cap)
    return max(capped, 10.0), "equity_risk"  # BingX's practical minimum trade size


def _log_and_maybe_execute_signal(symbol: str, bars: dict, analysis: str,
                                   target_pct: float, stop_pct: float,
                                   funding_rate_pct: float | None = None) -> tuple[str, bool]:
    """Computes entry/target/stop, optionally places a real BingX demo order, logs
    the signal (with the broker order ID/qty if one was opened), and returns the
    Telegram message text (analysis + an explicit execution status line) plus
    whether an order was actually placed."""
    direction = extract_direction(analysis)
    freshest_5min = bars.get("5min") or []
    if not direction or not freshest_5min:
        logger.warning("%s: could not log signal (direction=%s, has_bars=%s)", symbol, direction, bool(freshest_5min))
        return analysis, False

    # Real execution price is always our own live feed, never the model's
    # (possibly rounded/approximate) restated number.
    entry_price = float(freshest_5min[0]["close"])

    ai_stop = extract_price(analysis, "STOP-LOSS NARXI")
    ai_target = extract_price(analysis, "TAKE-PROFIT NARXI")
    target_price, stop_price, level_source = _resolve_target_stop(
        entry_price, direction, ai_target, ai_stop, target_pct, stop_pct)
    logger.info("%s: stop/target source=%s stop=%s target=%s", symbol, level_source, stop_price, target_price)

    broker_order_id = None
    broker_qty = None
    executed = False
    execution_line = "Ijro: o'chirilgan (faqat tahlil rejimi)"

    if AUTO_EXECUTE_TRADES:
        try:
            correlation_mult, same_direction_count = _correlation_size_multiplier(direction)
            confidence = extract_confidence(analysis)
            confidence_mult = _CONFIDENCE_MULTIPLIERS.get(confidence, 0.7)  # unparsed -> treat as ORTA
            size_multiplier = correlation_mult * confidence_mult

            stop_distance_pct = abs(entry_price - stop_price) / entry_price * 100
            base_size_usdt, size_source = _risk_based_position_size(stop_distance_pct)
            position_size_usdt = base_size_usdt * size_multiplier
            quantity = broker.round_quantity(symbol, position_size_usdt / entry_price)
            fill = broker.place_market_order(symbol, direction, quantity, target_price, stop_price)
            broker_order_id = fill["order_id"]
            broker_qty = quantity
            executed = True

            size_basis = (f"risk-based, {RISK_PCT_PER_TRADE}% equity" if size_source == "equity_risk"
                          else "fixed fallback")
            note_parts = [f"asos: ${base_size_usdt:.0f} ({size_basis})",
                          f"ishonch: {confidence or 'noaniq'} (x{confidence_mult:.2f})"]
            if same_direction_count:
                note_parts.append(f"{same_direction_count} bir xil yo'nalishdagi ochiq pozitsiya (x{correlation_mult:.2f})")
            execution_line = (
                f"Ijro: BAJARILDI ✅ (BingX demo) — order #{broker_order_id}, "
                f"narx {fill['fill_price']}, hajm {quantity} ({', '.join(note_parts)})"
            )
        except Exception as exc:
            logger.exception("%s: order execution failed", symbol)
            execution_line = f"Ijro: XATOLIK ⚠️ — {exc}"

    try:
        signal_id = storage.log_signal(symbol, direction, entry_price, target_price, stop_price,
                                        analysis, broker_order_id, broker_qty, funding_rate_pct)
        logger.info("%s: logged signal id=%s dir=%s entry=%s target=%s stop=%s order_id=%s qty=%s",
                     symbol, signal_id, direction, entry_price, target_price, stop_price,
                     broker_order_id, broker_qty)
    except Exception:
        logger.exception("%s: failed to log signal to database", symbol)

    return f"{symbol}\n\n{analysis}\n\n{execution_line}", executed


@app.route("/health")
def health():
    return jsonify(status="ok")


@app.route("/stats")
def stats():
    return jsonify(storage.get_stats())


if __name__ == "__main__":
    storage.init_db()
    start_scheduler(
        on_check=analyze_and_notify,
        telegram_bot_token=TELEGRAM_BOT_TOKEN,
        telegram_chat_id=TELEGRAM_CHAT_ID,
    )
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
