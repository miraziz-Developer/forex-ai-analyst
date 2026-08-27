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
from market_data import fetch_multi_timeframe
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
POSITION_SIZE_USDT = float(os.environ.get("POSITION_SIZE_USDT", "100"))
MAX_OPEN_POSITIONS = int(os.environ.get("MAX_OPEN_POSITIONS", "3"))

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


def build_analysis_text(symbol: str, bars: dict, institutional: dict, target_pct: float, stop_pct: float) -> str:
    return (
        f"Pair: {symbol} (perpetual futures, USDT-margined)\n"
        f"Current UTC time: {datetime.now(timezone.utc).isoformat()}\n"
        f"Configured target: {target_pct:.3f}% , max stop: {stop_pct:.3f}% "
        f"(volatility-adjusted: {ATR_MULTIPLIER}x current 1H ATR, {REWARD_RISK_RATIO}:1 reward:risk)\n\n"
        f"{format_institutional_context(institutional)}\n\n"
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


_last_recommendation: dict[str, str] = {}


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

    previous = _last_recommendation.get(symbol)
    _last_recommendation[symbol] = recommendation

    if recommendation != "TRADE_WATCH" or previous == "TRADE_WATCH":
        # either nothing worth trading, or this is the same setup we already signaled
        return {"status": "no_signal", "recommendation": recommendation}

    message, executed = _log_and_maybe_execute_signal(symbol, bars, analysis, target_pct, stop_pct)

    sent = send_telegram_message(message, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
    logger.info("Signal sent for %s (telegram sent: %s, executed: %s)", symbol, sent, executed)

    return {"status": "signal_sent", "telegram_sent": sent, "executed": executed}


def _log_and_maybe_execute_signal(symbol: str, bars: dict, analysis: str,
                                   target_pct: float, stop_pct: float) -> tuple[str, bool]:
    """Computes entry/target/stop, optionally places a real BingX demo order, logs
    the signal (with the broker order ID/qty if one was opened), and returns the
    Telegram message text (analysis + an explicit execution status line) plus
    whether an order was actually placed."""
    direction = extract_direction(analysis)
    freshest_5min = bars.get("5min") or []
    if not direction or not freshest_5min:
        logger.warning("%s: could not log signal (direction=%s, has_bars=%s)", symbol, direction, bool(freshest_5min))
        return analysis, False

    entry_price = float(freshest_5min[0]["close"])
    if direction == "BUY":
        target_price = entry_price * (1 + target_pct / 100)
        stop_price = entry_price * (1 - stop_pct / 100)
    else:
        target_price = entry_price * (1 - target_pct / 100)
        stop_price = entry_price * (1 + stop_pct / 100)

    broker_order_id = None
    broker_qty = None
    executed = False
    execution_line = "Ijro: o'chirilgan (faqat tahlil rejimi)"

    if AUTO_EXECUTE_TRADES:
        open_count = len(storage.get_open_signals())
        if open_count >= MAX_OPEN_POSITIONS:
            execution_line = f"Ijro: o'tkazib yuborildi — ochiq pozitsiyalar limiti ({MAX_OPEN_POSITIONS}) to'lgan"
        else:
            try:
                quantity = broker.round_quantity(symbol, POSITION_SIZE_USDT / entry_price)
                fill = broker.place_market_order(symbol, direction, quantity, target_price, stop_price)
                broker_order_id = fill["order_id"]
                broker_qty = quantity
                executed = True
                execution_line = (
                    f"Ijro: BAJARILDI ✅ (BingX demo) — order #{broker_order_id}, "
                    f"narx {fill['fill_price']}, hajm {quantity}"
                )
            except Exception as exc:
                logger.exception("%s: order execution failed", symbol)
                execution_line = f"Ijro: XATOLIK ⚠️ — {exc}"

    try:
        signal_id = storage.log_signal(symbol, direction, entry_price, target_price, stop_price,
                                        analysis, broker_order_id, broker_qty)
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
