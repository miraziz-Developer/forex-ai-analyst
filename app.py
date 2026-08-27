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

TARGET_PCT, STOP_PCT = (float(x) for x in os.environ.get("TARGET_PCT_STOP_PCT", "1.5/0.6").split("/"))

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


def build_analysis_prompt(symbol: str, bars: dict) -> str:
    return (
        f"Pair: {symbol} (perpetual futures, USDT-margined)\n"
        f"Current UTC time: {datetime.now(timezone.utc).isoformat()}\n"
        f"Configured target: {TARGET_PCT}% , max stop: {STOP_PCT}%\n\n"
        f"4H bars (most recent first):\n{json.dumps(bars.get('4h', [])[:20])}\n\n"
        f"1H bars:\n{json.dumps(bars.get('1h', [])[:24])}\n\n"
        f"15min bars:\n{json.dumps(bars.get('15min', [])[:20])}\n\n"
        f"5min bars:\n{json.dumps(bars.get('5min', [])[:20])}\n"
    )


def run_analysis(user_prompt: str) -> str:
    response = openai_client.responses.create(
        model=AZURE_OPENAI_DEPLOYMENT,
        instructions=SYSTEM_PROMPT,
        input=user_prompt,
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
        user_prompt = build_analysis_prompt(symbol, bars)
        analysis = run_analysis(user_prompt)
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

    message, executed = _log_and_maybe_execute_signal(symbol, bars, analysis)

    sent = send_telegram_message(message, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
    logger.info("Signal sent for %s (telegram sent: %s, executed: %s)", symbol, sent, executed)

    return {"status": "signal_sent", "telegram_sent": sent, "executed": executed}


def _log_and_maybe_execute_signal(symbol: str, bars: dict, analysis: str) -> tuple[str, bool]:
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
        target_price = entry_price * (1 + TARGET_PCT / 100)
        stop_price = entry_price * (1 - STOP_PCT / 100)
    else:
        target_price = entry_price * (1 - TARGET_PCT / 100)
        stop_price = entry_price * (1 + STOP_PCT / 100)

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
