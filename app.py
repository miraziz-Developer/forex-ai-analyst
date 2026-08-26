import json
import logging
import os
from datetime import datetime, time, timezone

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from openai import OpenAI

from market_data import fetch_multi_timeframe, normalize_symbol
from notifier import send_telegram_message

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


def pip_target_and_stop(symbol: str) -> tuple[float, float]:
    target_pips, stop_pips = (float(x) for x in RISK_REWARD_PIPS.split("/"))
    if symbol.endswith("/JPY") or symbol.startswith("JPY/"):
        return target_pips, stop_pips  # JPY pip convention handled in the prompt's context text
    return target_pips, stop_pips


def build_analysis_prompt(raw_ticker: str, symbol: str, bars: dict, target_pips: float, stop_pips: float) -> str:
    pip_note = "0.01 (JPY pair)" if "JPY" in symbol else "0.0001"
    return (
        f"Pair: {symbol} (alert ticker: {raw_ticker})\n"
        f"Current UTC time: {datetime.now(timezone.utc).isoformat()}\n"
        f"Pip size for this pair: {pip_note}\n"
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


@app.route("/health")
def health():
    return jsonify(status="ok")


@app.route("/webhook/tradingview", methods=["POST"])
def tradingview_webhook():
    try:
        payload = request.get_json(force=True, silent=False)
    except Exception:
        logger.warning("Received malformed (non-JSON) webhook payload")
        return jsonify(error="invalid JSON"), 400

    raw_ticker = payload.get("ticker") if payload else None
    if not raw_ticker:
        logger.warning("Webhook payload missing 'ticker': %s", payload)
        return jsonify(error="missing 'ticker'"), 400

    now_utc = datetime.now(timezone.utc)
    if not in_trading_window(now_utc):
        logger.info("Alert for %s ignored — outside trading window (%s UTC)", raw_ticker, now_utc.isoformat())
        return jsonify(status="ignored", reason="outside trading window"), 200

    try:
        symbol = normalize_symbol(raw_ticker)
    except ValueError as exc:
        logger.warning("Rejected webhook: %s", exc)
        return jsonify(error=str(exc)), 400

    logger.info("Processing alert for %s", symbol)

    try:
        bars = fetch_multi_timeframe(raw_ticker, TWELVEDATA_API_KEY)
        target_pips, stop_pips = pip_target_and_stop(symbol)
        user_prompt = build_analysis_prompt(raw_ticker, symbol, bars, target_pips, stop_pips)
        analysis = run_analysis(user_prompt)
    except Exception:
        logger.exception("Analysis failed for %s", symbol)
        return jsonify(error="analysis failed"), 500

    sent = send_telegram_message(f"{symbol}\n\n{analysis}", TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
    logger.info("Analysis complete for %s (telegram sent: %s)", symbol, sent)

    return jsonify(status="processed", telegram_sent=sent), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
