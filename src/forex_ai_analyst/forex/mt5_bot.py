"""FX ML model on a MetaTrader 5 **demo** account (Windows VM).

    pip install .            (on the VM, plus: pip install MetaTrader5)
    python -m forex_ai_analyst.forex.mt5_bot

Decisions are made exactly as in the walk-forward study: every Monday close
(Yahoo daily data, the shared feature code, the exported model), a confident
score (P(up) >= 0.55 BUY, <= 0.45 SELL) opens a position after the close, and it
is closed after the fifth trading day's close. MT5 is used only to execute.
Demo operating rules on top of the study: a 3 ATR emergency stop and a volume
sized so that the stop costs FX_BOT_RISK_PCT of equity.

The bot refuses to trade an account that is not a demo account.

Optional .env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, FX_BOT_RISK_PCT (0.5),
FX_BOT_DB (fx_ml_demo.sqlite), FX_SYMBOL_MAP (e.g. "XAUUSD=GOLD,EURUSD=EURUSD.").
"""
from __future__ import annotations

import logging
import os
import sqlite3
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

from forex_ai_analyst.forex import ml_model
from forex_ai_analyst.forex.ml_features import feature_row, load_context
from forex_ai_analyst.forex.regime_system_study import FX_ONLY
from forex_ai_analyst.shared.notifier import send_telegram_message

logger = logging.getLogger("fx_ml_demo")
MAGIC = 742026
STOP_ATR = 3.0
_SUFFIXES = ("", "m", ".", "#", "-ECN", ".r", "c", "pro", ".a")
RETRYABLE = ("symbol not found", "below minimum volume")

SCHEMA = """CREATE TABLE IF NOT EXISTS trades (
 id INTEGER PRIMARY KEY AUTOINCREMENT, model_version TEXT NOT NULL, decision_day TEXT NOT NULL, market TEXT NOT NULL,
 symbol TEXT, side INTEGER NOT NULL, prob REAL NOT NULL, ticket INTEGER, volume REAL, entry_price REAL, stop REAL,
 exit_day TEXT NOT NULL, exit_price REAL, profit REAL, status TEXT NOT NULL, note TEXT, created_at TEXT NOT NULL,
 UNIQUE(model_version, decision_day, market))"""


def env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def notify(text: str) -> None:
    token, chats = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(), os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    for chat in (c.strip() for c in chats.split(",") if c.strip()):
        if token:
            send_telegram_message(text, token, chat)
    logger.info(text.replace("\n", " | "))


def nth_weekday_after(day: str, n: int) -> str:
    """The n-th weekday after `day` (the study's 5-trading-day exit; holidays are not modelled)."""
    d, count = date.fromisoformat(day), 0
    while count < n:
        d += timedelta(days=1)
        count += d.weekday() < 5
    return d.isoformat()


def open_db(path: str | None = None) -> sqlite3.Connection:
    db = sqlite3.connect(path or os.environ.get("FX_BOT_DB", "fx_ml_demo.sqlite"))
    db.row_factory = sqlite3.Row
    db.execute(SCHEMA)
    db.commit()
    return db


def require_demo(mt5):
    account = mt5.account_info()
    if account is None:
        raise SystemExit(f"MT5 account not available: {mt5.last_error()}")
    if account.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO:
        raise SystemExit("Refusing to run: this is not a DEMO account. The FX ML model is not proven (docs/ML_STUDY.md).")
    return account


def resolve_symbol(mt5, market: str) -> str | None:
    mapping = dict(item.split("=", 1) for item in os.environ.get("FX_SYMBOL_MAP", "").split(",") if "=" in item)
    candidates = [mapping[market]] if market in mapping else []
    candidates += [market + s for s in _SUFFIXES] + (["GOLD", "GOLDm"] if market == "XAUUSD" else [])
    for name in candidates:
        if mt5.symbol_info(name) is not None:
            mt5.symbol_select(name, True)
            return name
    return None


def volume_for(mt5, symbol: str, stop_distance: float, risk_money: float) -> float:
    """Lots such that a move of `stop_distance` costs `risk_money`, rounded down to the volume step."""
    info = mt5.symbol_info(symbol)
    tick_size = info.trade_tick_size or info.point
    money_per_price_per_lot = info.trade_tick_value / tick_size
    raw = risk_money / (stop_distance * money_per_price_per_lot)
    volume = round(int(raw / info.volume_step + 1e-9) * info.volume_step, 8)
    return min(volume, info.volume_max) if volume >= info.volume_min else 0.0


def min_equity_for(mt5, symbol: str, stop_distance: float, risk_pct: float) -> float:
    """Equity needed for the broker's minimum volume to fit the risk budget."""
    info = mt5.symbol_info(symbol)
    money_per_price_per_lot = info.trade_tick_value / (info.trade_tick_size or info.point)
    return info.volume_min * stop_distance * money_per_price_per_lot / (risk_pct / 100)


def _filling(mt5, symbol: str) -> int:
    mode = mt5.symbol_info(symbol).filling_mode
    if mode & 1:
        return mt5.ORDER_FILLING_FOK
    if mode & 2:
        return mt5.ORDER_FILLING_IOC
    return mt5.ORDER_FILLING_RETURN


def _send(mt5, symbol: str, side: int, volume: float, sl: float | None, comment: str, position: int | None = None):
    tick = mt5.symbol_info_tick(symbol)
    buy = side > 0
    request = {"action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": volume,
               "type": mt5.ORDER_TYPE_BUY if buy else mt5.ORDER_TYPE_SELL, "price": tick.ask if buy else tick.bid,
               "deviation": 20, "magic": MAGIC, "comment": comment, "type_time": mt5.ORDER_TIME_GTC,
               "type_filling": _filling(mt5, symbol)}
    if sl is not None:
        request["sl"] = sl
    if position is not None:
        request["position"] = position
    return mt5.order_send(request)


def close_due(mt5, db: sqlite3.Connection, today: str) -> list[str]:
    """Close every open trade whose exit day's close has passed (first cycle on a later date)."""
    messages = []
    for row in db.execute("SELECT * FROM trades WHERE status = 'OPEN' AND exit_day < ?", [today]).fetchall():
        positions = mt5.positions_get(ticket=row["ticket"]) or ()
        if not positions:
            db.execute("UPDATE trades SET status = 'CLOSED', note = ? WHERE id = ?",
                       ["closed before the exit day (emergency stop)", row["id"]])
            messages.append(f"⛔ {row['market']}: favqulodda stop oldinroq yopgan")
            continue
        position = positions[0]
        result = _send(mt5, row["symbol"], -row["side"], position.volume, None, "fx-ml exit", position=position.ticket)
        if result is not None and result.retcode == mt5.TRADE_RETCODE_DONE:
            db.execute("UPDATE trades SET status = 'CLOSED', exit_price = ?, profit = ? WHERE id = ?",
                       [result.price, position.profit, row["id"]])
            messages.append(f"{'🟢' if position.profit > 0 else '🔴'} {row['market']} "
                            f"{'BUY' if row['side'] > 0 else 'SELL'} yopildi: {position.profit:+.2f}")
        else:
            messages.append(f"❌ {row['market']} yopilmadi: {getattr(result, 'comment', mt5.last_error())}")
    db.commit()
    return messages


def decide(mt5, db: sqlite3.Connection, now: datetime, account) -> list[str]:
    today = now.date().isoformat()
    model = ml_model.load()
    ctx = load_context(FX_ONLY)
    reference = ctx.caches[FX_ONLY[0].name]
    complete = [d for d in reference.days if d < today]
    if not complete or date.fromisoformat(complete[-1]).weekday() != 0:
        return []
    decision_day = complete[-1]
    existing = db.execute("SELECT market, status, note FROM trades WHERE model_version = ? AND decision_day = ?",
                          [model["version"], decision_day]).fetchall()
    if existing:
        # Signals skipped only for an execution reason (account too small, symbol missing) are retried
        # until the end of the day after the decision, e.g. after switching to a larger demo account.
        if today > nth_weekday_after(decision_day, 1):
            return []
        retry = {r["market"] for r in existing if r["status"] == "SKIPPED" and r["note"] in RETRYABLE}
        if not retry:
            return []
        db.execute(f"DELETE FROM trades WHERE model_version = ? AND decision_day = ? AND status = 'SKIPPED' "
                   f"AND market IN ({', '.join('?' * len(retry))})", [model["version"], decision_day, *sorted(retry)])
    else:
        retry = None
    risk_money = account.equity * env_float("FX_BOT_RISK_PCT", 0.5) / 100
    exit_day = nth_weekday_after(decision_day, 5)
    messages = []
    for market in FX_ONLY:
        if retry is not None and market.name not in retry:
            continue
        c = ctx.caches[market.name]
        if decision_day not in c.days:
            continue
        i = c.days.index(decision_day)
        feats = feature_row(ctx, market.name, i)
        if feats is None:
            continue
        p = ml_model.probability_up(model, feats)
        side = 1 if p >= model["long_threshold"] else -1 if p <= model["short_threshold"] else 0
        base = [model["version"], decision_day, market.name, side, p, exit_day, now.isoformat()]
        if side == 0:
            db.execute("INSERT OR IGNORE INTO trades (model_version, decision_day, market, side, prob, exit_day, "
                       "created_at, status) VALUES (?, ?, ?, ?, ?, ?, ?, 'SKIPPED')", base)
            continue
        symbol = resolve_symbol(mt5, market.name)
        stop_distance = STOP_ATR * c.f["atr"][i]
        volume = volume_for(mt5, symbol, stop_distance, risk_money) if symbol else 0.0
        if not symbol or volume <= 0:
            reason = "symbol not found" if not symbol else "below minimum volume"
            db.execute("INSERT OR IGNORE INTO trades (model_version, decision_day, market, side, prob, exit_day, "
                       "created_at, status, note) VALUES (?, ?, ?, ?, ?, ?, ?, 'SKIPPED', ?)", base + [reason])
            if not symbol:
                messages.append(f"⚠️ {market.name} {'BUY' if side > 0 else 'SELL'} signali: brokerda simvol topilmadi "
                                "(FX_SYMBOL_MAP bilan ko'rsating)")
            else:
                need = min_equity_for(mt5, symbol, stop_distance, env_float("FX_BOT_RISK_PCT", 0.5))
                messages.append(f"⚠️ {market.name} {'BUY' if side > 0 else 'SELL'} signali o'tkazildi: minimal lot ham "
                                f"risk chegarasidan katta. Kerakli balans ≈ ${need:,.0f} (hozir ${account.equity:,.0f})")
            continue
        tick = mt5.symbol_info_tick(symbol)
        stop = (tick.ask if side > 0 else tick.bid) - side * stop_distance
        result = _send(mt5, symbol, side, volume, stop, f"fx-ml {model['version']}")
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            messages.append(f"❌ {market.name}: order rad etildi ({getattr(result, 'comment', mt5.last_error())})")
            continue
        db.execute("INSERT OR IGNORE INTO trades (model_version, decision_day, market, symbol, side, prob, ticket, "
                   "volume, entry_price, stop, exit_day, created_at, status) "
                   "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN')",
                   [model["version"], decision_day, market.name, symbol, side, p, result.order, volume, result.price,
                    stop, exit_day, now.isoformat()])
        messages.append(f"📌 {market.name} {'BUY' if side > 0 else 'SELL'} {volume} lot @ {result.price} "
                        f"(ehtimol {p:.0%}, stop {stop:.5g}, {exit_day} yopilishidan keyin chiqadi)")
    db.commit()
    return messages


def cycle(mt5, db: sqlite3.Connection, now: datetime | None = None) -> list[str]:
    now = now or datetime.now(timezone.utc)
    account = require_demo(mt5)
    return close_due(mt5, db, now.date().isoformat()) + decide(mt5, db, now, account)


def main() -> None:
    import MetaTrader5 as mt5

    # The package is installed in site-packages, so look for .env in the folder the bot is started from.
    env_file = Path.cwd() / ".env"
    load_dotenv(env_file)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logger.info("settings: .env %s | risk %.2f%% | telegram %s", "found" if env_file.exists() else "NOT found",
                env_float("FX_BOT_RISK_PCT", 0.5),
                "on" if os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID") else "off")
    if not mt5.initialize():
        raise SystemExit(f"MetaTrader 5 initialize failed: {mt5.last_error()}")
    require_demo(mt5)
    db = open_db()
    notify("🧪 FX ML demo bot ishga tushdi (faqat DEMO hisob)")
    last_error_alert = 0.0
    while True:
        try:
            messages = cycle(mt5, db)
            if messages:
                notify("🧪 FX ML demo\n" + "\n".join(messages))
        except SystemExit:
            raise
        except Exception as exc:
            logger.exception("cycle failed")
            if time.monotonic() - last_error_alert > 6 * 3600:
                notify(f"⚠️ FX ML demo bot xatosi: {type(exc).__name__}: {exc}")
                last_error_alert = time.monotonic()
        time.sleep(15 * 60)


if __name__ == "__main__":
    main()
