import json
import logging
import os
import re
from datetime import datetime, time, timezone

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from openai import OpenAI

import storage
from market_data import fetch_multi_timeframe, normalize_symbol
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
TWELVEDATA_API_KEY = os.environ["TWELVEDATA_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

TRADING_DAYS = os.environ.get("TRADING_DAYS", "MON,TUE,WED,THU,FRI")
TRADING_WINDOW_START = os.environ.get("TRADING_WINDOW_START", "12:00")
TRADING_WINDOW_END = os.environ.get("TRADING_WINDOW_END", "16:00")
RISK_REWARD_PIPS = os.environ.get("RISK_REWARD_PIPS", "20/5")
SPREAD_PIPS = float(os.environ.get("SPREAD_PIPS", "1.5"))

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


def pip_size_for(symbol: str) -> float:
    return 0.01 if "JPY" in symbol else 0.0001


def pip_target_and_stop(symbol: str) -> tuple[float, float]:
    target_pips, stop_pips = (float(x) for x in RISK_REWARD_PIPS.split("/"))
    return target_pips, stop_pips


def build_analysis_prompt(raw_ticker: str, symbol: str, bars: dict, target_pips: float, stop_pips: float) -> str:
    pip_note = "0.01 (JPY pair)" if "JPY" in symbol else "0.0001"
    return (
        f"Pair: {symbol} (alert ticker: {raw_ticker})\n"
        f"Current UTC time: {datetime.now(timezone.utc).isoformat()}\n"
        f"Pip size for this pair: {pip_note}\n"
        f"Typical spread for this pair: {SPREAD_PIPS} pips\n"
        f"Configured target: {target_pips} pips, max stop: {stop_pips} pips\n\n"
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


def analyze_and_notify(raw_ticker: str) -> dict:
    """Shared pipeline: trading-window check -> fetch bars -> full AI read -> Telegram
    only when the AI itself calls a new TRADE WATCH. Runs on every scheduler tick for
    every watched pair — the AI decides each time, not a mechanical indicator."""
    now_utc = datetime.now(timezone.utc)
    if not in_trading_window(now_utc):
        logger.info("%s skipped — outside trading window (%s UTC)", raw_ticker, now_utc.isoformat())
        return {"status": "ignored", "reason": "outside trading window"}

    try:
        symbol = normalize_symbol(raw_ticker)
    except ValueError as exc:
        logger.warning("Rejected ticker: %s", exc)
        return {"status": "error", "reason": str(exc)}

    logger.info("Analyzing %s", symbol)

    try:
        bars = fetch_multi_timeframe(raw_ticker, TWELVEDATA_API_KEY)
        target_pips, stop_pips = pip_target_and_stop(symbol)
        user_prompt = build_analysis_prompt(raw_ticker, symbol, bars, target_pips, stop_pips)
        analysis = run_analysis(user_prompt)
    except Exception:
        logger.exception("Analysis failed for %s", symbol)
        return {"status": "error", "reason": "analysis failed"}

    recommendation = extract_recommendation(analysis)
    logger.info("%s verdict: %s", symbol, recommendation)

    previous = _last_recommendation.get(raw_ticker)
    _last_recommendation[raw_ticker] = recommendation

    if recommendation != "TRADE_WATCH" or previous == "TRADE_WATCH":
        # either nothing worth trading, or this is the same setup we already signaled
        return {"status": "no_signal", "recommendation": recommendation}

    sent = send_telegram_message(f"{symbol}\n\n{analysis}", TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
    logger.info("Signal sent for %s (telegram sent: %s)", symbol, sent)

    _log_signal_if_possible(symbol, bars, target_pips, stop_pips, analysis)

    return {"status": "signal_sent", "telegram_sent": sent}


def _log_signal_if_possible(symbol: str, bars: dict, target_pips: float, stop_pips: float, analysis: str) -> None:
    direction = extract_direction(analysis)
    freshest_5min = bars.get("5min") or []
    if not direction or not freshest_5min:
        logger.warning("%s: could not log signal (direction=%s, has_bars=%s)", symbol, direction, bool(freshest_5min))
        return

    entry_price = float(freshest_5min[0]["close"])
    pip_size = pip_size_for(symbol)
    if direction == "BUY":
        target_price = entry_price + target_pips * pip_size
        stop_price = entry_price - stop_pips * pip_size
    else:
        target_price = entry_price - target_pips * pip_size
        stop_price = entry_price + stop_pips * pip_size

    try:
        signal_id = storage.log_signal(symbol, direction, entry_price, target_price, stop_price, analysis)
        logger.info("%s: logged signal id=%s dir=%s entry=%s target=%s stop=%s",
                     symbol, signal_id, direction, entry_price, target_price, stop_price)
    except Exception:
        logger.exception("%s: failed to log signal to database", symbol)


@app.route("/health")
def health():
    return jsonify(status="ok")


@app.route("/stats")
def stats():
    return jsonify(storage.get_stats())


@app.route("/webhook/tradingview", methods=["POST"])
def tradingview_webhook():
    """Optional: only useful if you're on a TradingView plan that supports webhook
    alerts. The bot doesn't depend on this — see scheduler.py for the self-triggered path."""
    try:
        payload = request.get_json(force=True, silent=False)
    except Exception:
        logger.warning("Received malformed (non-JSON) webhook payload")
        return jsonify(error="invalid JSON"), 400

    raw_ticker = payload.get("ticker") if payload else None
    if not raw_ticker:
        logger.warning("Webhook payload missing 'ticker': %s", payload)
        return jsonify(error="missing 'ticker'"), 400

    result = analyze_and_notify(raw_ticker)
    status_code = 500 if result["status"] == "error" else 200
    return jsonify(result), status_code


if __name__ == "__main__":
    storage.init_db()
    start_scheduler(
        on_check=analyze_and_notify,
        twelvedata_api_key=TWELVEDATA_API_KEY,
        telegram_bot_token=TELEGRAM_BOT_TOKEN,
        telegram_chat_id=TELEGRAM_CHAT_ID,
    )
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
