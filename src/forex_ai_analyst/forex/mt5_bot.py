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
FX_BOT_DB (fx_ml_demo.sqlite), FX_SYMBOL_MAP (e.g. "XAUUSD=GOLD,EURUSD=EURUSD."), and the portfolio,
spread and health limits documented in mt5_guards.py. Signals are placed strongest first.
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

from forex_ai_analyst.forex import (crypto_live, fix_live, fomc_live, index_live, metals_live, ml_model, rebound_live,
                                   status_report, trend_live)
from forex_ai_analyst.forex import engine_health as health
from forex_ai_analyst.forex import mt5_guards as guards
from forex_ai_analyst.forex.ml_features import feature_row, load_context
from forex_ai_analyst.forex.regime_system_study import FX_ONLY
from forex_ai_analyst.shared.notifier import send_telegram_message

logger = logging.getLogger("fx_ml_demo")
MAGIC = 742026
STOP_ATR = 3.0
_SUFFIXES = ("", "m", ".", "#", "-ECN", ".r", "c", "pro", ".a")
RETRYABLE = ("symbol not found", "below minimum volume", "spread too wide", "news blackout")

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
    guards.migrate(db)
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


def risk_of(mt5, symbol: str, volume: float, stop_distance: float) -> float:
    """Money lost if `volume` lots move `stop_distance` against the position."""
    info = mt5.symbol_info(symbol)
    return volume * stop_distance * info.trade_tick_value / (info.trade_tick_size or info.point)


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


def _send(mt5, symbol: str, side: int, volume: float, sl: float | None, comment: str, position: int | None = None,
          tp: float | None = None):
    tick = mt5.symbol_info_tick(symbol)
    digits = getattr(mt5.symbol_info(symbol), "digits", None)
    def norm(price: float) -> float:        # brokers reject stops not rounded to the symbol's digits ("Invalid stops")
        return round(price, digits) if isinstance(digits, int) else price
    buy = side > 0
    # MT5 limits comments to a few short ASCII characters ("Invalid comment argument" otherwise)
    comment = re.sub(r"[^A-Za-z0-9 _-]", "", comment)[:16]
    request = {"action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": volume,
               "type": mt5.ORDER_TYPE_BUY if buy else mt5.ORDER_TYPE_SELL, "price": tick.ask if buy else tick.bid,
               "deviation": 20, "magic": MAGIC, "comment": comment, "type_time": mt5.ORDER_TIME_GTC,
               "type_filling": _filling(mt5, symbol)}
    if sl is not None:
        request["sl"] = norm(sl)
    if tp is not None:
        request["tp"] = norm(tp)
    if position is not None:
        request["position"] = position
    return mt5.order_send(request)


def close_due(mt5, db: sqlite3.Connection, today: str) -> list[str]:
    """Close every open trade whose exit day's close has passed (first cycle on a later date)."""
    messages = []
    for row in db.execute("SELECT * FROM trades WHERE status = 'OPEN' AND exit_day < ?", [today]).fetchall():
        positions = mt5.positions_get(ticket=row["ticket"]) or ()
        if not positions:
            deals = getattr(mt5, "history_deals_get", lambda **_: None)(position=row["ticket"]) or ()
            profit = sum(d.profit + d.commission + d.swap for d in deals) if deals else None
            db.execute("UPDATE trades SET status = 'CLOSED', profit = ?, note = ? WHERE id = ?",
                       [profit, "closed before the exit day (stop or take-profit)", row["id"]])
            result = "" if profit is None else f": {profit:+.2f}"
            icon = "🎯" if profit and profit > 0 else "⛔"
            messages.append(f"{icon} {row['market']}: stop yoki TP oldinroq yopgan{result}")
            continue
        position = positions[0]
        result = _send(mt5, row["symbol"], -row["side"], position.volume, None, "fx-ml exit", position=position.ticket)
        if result is not None and result.retcode == mt5.TRADE_RETCODE_DONE:
            db.execute("UPDATE trades SET status = 'CLOSED', exit_price = ?, profit = ? WHERE id = ?",
                       [result.price, position.profit, row["id"]])
            messages.append(f"{'🟢' if position.profit > 0 else '🔴'} {row['market']} "
                            f"{'BUY' if row['side'] > 0 else 'SELL'} yopildi: {position.profit:+.2f}")
        elif not guards.market_closed(mt5, result):                   # a shut market is retried quietly
            messages.append(f"❌ {row['market']} yopilmadi: {getattr(result, 'comment', mt5.last_error())}")
    db.commit()
    return messages


def self_managing_versions() -> set[str]:
    """Journal versions whose engine is on and closes its own rows when the broker position is gone."""
    engines = {"trend": trend_live.VERSION, "gold": metals_live.VERSION, "crypto": crypto_live.VERSION,
               "index": index_live.VERSION, "rebound": rebound_live.VERSION, "fix": fix_live.VERSION,
               "fomc": fomc_live.VERSION}
    return {version for name, version in engines.items() if name in enabled_engines()}


def reconcile_orphans(mt5, db: sqlite3.Connection) -> list[str]:
    """Close journal rows whose broker position no longer exists (closed by hand, by its stop, or left by an
    engine that is now off), so they stop holding the portfolio risk and stress budget. Rows of engines that
    are on are left to the engine, which reports its own exit."""
    messages, managed = [], self_managing_versions()
    for row in db.execute("SELECT * FROM trades WHERE status = 'OPEN' AND ticket IS NOT NULL").fetchall():
        if row["model_version"] in managed or mt5.positions_get(ticket=row["ticket"]):
            continue
        deals = getattr(mt5, "history_deals_get", lambda **_: None)(position=row["ticket"]) or ()
        profit = sum(d.profit + d.commission + d.swap for d in deals) if deals else None
        db.execute("UPDATE trades SET status = 'CLOSED', profit = ?, note = ? WHERE id = ?",
                   [profit, "position no longer at the broker (closed by hand or by its stop)", row["id"]])
        result = "" if profit is None else f": {profit:+.2f}"
        messages.append(f"🧹 {row['market']} ({row['model_version']}) brokerda yo'q — jurnalda yopildi{result}")
    db.commit()
    return messages


LABELS = {"fx_logistic": "v1", "fx_logistic_cot_c01": "v2c"}
_COT_WARNED: set[str] = set()


def decide(mt5, db: sqlite3.Connection, now: datetime, account, paused: bool = False) -> list[str]:
    """Run every exported model family on the same Monday close; each has its own journal rows."""
    today = now.date().isoformat()
    models = ml_model.load_all()
    messages = []
    cot_failed = False
    try:
        ctx = load_context(FX_ONLY, with_cot=any(m.get("uses_cot") for m in models))
    except Exception as exc:          # CFTC unreachable: models without COT still run; retried next cycle
        logger.warning("COT data unavailable (%s); running models without COT only", type(exc).__name__)
        ctx, cot_failed = load_context(FX_ONLY), True
    reference = ctx.caches[FX_ONLY[0].name]
    complete = [d for d in reference.days if d < today]
    if not complete or date.fromisoformat(complete[-1]).weekday() != 0:
        return []
    decision_day = complete[-1]
    if cot_failed and decision_day not in _COT_WARNED:
        _COT_WARNED.add(decision_day)
        messages.append("⚠️ CFTC COT ma'lumoti olinmadi: v2c signallari kechikadi, bot qayta urinadi")
    cot_data = ctx.cot
    for model in models:
        if model.get("uses_cot") and cot_data is None:
            continue
        ctx.cot = cot_data if model.get("uses_cot") else None
        label = LABELS.get(model.get("family", ""), model["version"])
        messages += [f"[{label}] {m}" for m in decide_model(mt5, db, now, account, ctx, model, decision_day, paused)]
    ctx.cot = cot_data
    return [m for m in messages if m]


def decide_model(mt5, db: sqlite3.Connection, now: datetime, account, ctx, model: dict, decision_day: str,
                 paused: bool = False) -> list[str]:
    today = now.date().isoformat()
    if guards.model_disabled(db, model["version"]):
        key = f"disabled_note:{model['version']}:{decision_day}"
        if guards.get(db, key):
            return []
        guards.put(db, key, 1)
        s = guards.model_stats(db, model["version"])
        return [f"⛔ model o'chirilgan: {s['trades']} yopiq trade, PF {s['pf']:.2f} < {guards.HEALTH_MIN_PF} "
                "(qayta yoqish: .env FX_BOT_NO_AUTO_DISABLE=1)"]
    existing = db.execute("SELECT market, status, note FROM trades WHERE model_version = ? AND decision_day = ?",
                          [model["version"], decision_day]).fetchall()
    if existing:
        # Signals skipped only for an execution reason (account too small, symbol missing) are retried
        # until the end of the day after the decision, e.g. after switching to a larger demo account.
        if today > nth_weekday_after(decision_day, 1):
            return []
        retry = {r["market"] for r in existing if r["status"] == "SKIPPED" and r["note"] in RETRYABLE}
        retry |= {m.name for m in FX_ONLY} - {r["market"] for r in existing}      # never evaluated (missing data)
        if not retry:
            return []
        db.execute(f"DELETE FROM trades WHERE model_version = ? AND decision_day = ? AND status = 'SKIPPED' "
                   f"AND market IN ({', '.join('?' * len(retry))})", [model["version"], decision_day, *sorted(retry)])
    else:
        retry = None
    risk_pct = guards.engine_risk_pct(db, model["version"], env_float("FX_BOT_RISK_PCT", 0.5))
    risk_money = account.equity * risk_pct / 100
    exit_day = nth_weekday_after(decision_day, 5)
    messages, candidates = [], []
    for market in FX_ONLY:
        if retry is not None and market.name not in retry:
            continue
        c = ctx.caches[market.name]
        if decision_day not in c.days:
            logger.warning("%s: no daily bar for %s; retried next cycle", market.name, decision_day)
            continue
        i = c.days.index(decision_day)
        feats = feature_row(ctx, market.name, i)
        if feats is None:
            continue
        candidates.append((market, c, i, ml_model.probability_up(model, feats)))
    # strongest signals first, so a full risk budget is spent on them rather than on list order
    for market, c, i, p in sorted(candidates, key=lambda x: -abs(x[3] - 0.5)):
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
                need = min_equity_for(mt5, symbol, stop_distance, risk_pct)
                messages.append(f"⚠️ {market.name} {'BUY' if side > 0 else 'SELL'} signali o'tkazildi: minimal lot ham "
                                f"risk chegarasidan katta. Kerakli balans ≈ ${need:,.0f} (hozir ${account.equity:,.0f})")
            continue
        tick = mt5.symbol_info_tick(symbol)
        info = mt5.symbol_info(symbol)
        mpp = info.trade_tick_value / (info.trade_tick_size or info.point)
        volume = guards.stress_volume(market.name, volume, mpp, tick.ask, account.equity, info.volume_step,
                                      info.volume_min)
        risk = risk_of(mt5, symbol, volume, stop_distance)
        stress = guards.stress_loss(market.name, volume, mpp, tick.ask)
        crowded = guards.currency_room(db, account.equity, market.name, side, risk)
        news = guards.news_blackout(market.name, now)
        blocked = ("drawdown pause" if paused else
                   "stress limit" if volume <= 0 or not guards.stress_room(db, account.equity, stress) else
                   "portfolio risk limit" if not guards.risk_room(db, account.equity, risk) else
                   "currency risk limit" if crowded else
                   "news blackout" if news else
                   "spread too wide" if not guards.spread_ok(tick.ask, tick.bid, stop_distance) else None)
        if blocked:
            db.execute("INSERT OR IGNORE INTO trades (model_version, decision_day, market, side, prob, exit_day, "
                       "created_at, status, note) VALUES (?, ?, ?, ?, ?, ?, ?, 'SKIPPED', ?)", base + [blocked])
            why = {"drawdown pause": "hisob cho'qqidan limitdan ko'p tushgan",
                   "stress limit": "tarixdagi eng yomon kun takrorlansa zarar limitdan oshadi",
                   "portfolio risk limit": "umumiy ochiq risk limiti to'lgan",
                   "currency risk limit": f"{crowded} bo'yicha bir tomonlama risk limiti to'lgan",
                   "news blackout": f"muhim yangilik yaqin ({news}), keyinroq qayta uriniladi",
                   "spread too wide": "spread juda keng, keyinroq qayta uriniladi"}[blocked]
            messages.append(f"⏸ {market.name} {'BUY' if side > 0 else 'SELL'} signali kutib turibdi: {why}")
            continue
        price = tick.ask if side > 0 else tick.bid
        stop = price - side * stop_distance
        tp_atr = ml_model.FAMILIES.get(model.get("family", ""), {}).get("tp_atr")
        target = price + side * tp_atr * c.f["atr"][i] if tp_atr else None
        result = _send(mt5, symbol, side, volume, stop, f"ml {LABELS.get(model.get('family', ''), 'x')}", tp=target)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            if guards.market_closed(mt5, result):
                continue                                    # no journal row: retried within the window
            why = getattr(result, "comment", None) or str(mt5.last_error())
            # journalled as skipped (not retryable), so a rejected order is reported once, not every 15 minutes
            db.execute("INSERT OR IGNORE INTO trades (model_version, decision_day, market, side, prob, exit_day, "
                       "created_at, status, note) VALUES (?, ?, ?, ?, ?, ?, ?, 'SKIPPED', ?)",
                       base + [f"order rejected: {why}"[:120]])
            messages.append(f"❌ {market.name}: order rad etildi ({why})")
            continue
        db.execute("INSERT OR IGNORE INTO trades (model_version, decision_day, market, symbol, side, prob, ticket, "
                   "volume, entry_price, stop, exit_day, created_at, status, risk_money, requested_price, slippage, stress_money) "
                   "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?)",
                   [model["version"], decision_day, market.name, symbol, side, p, result.order, volume, result.price,
                    stop, exit_day, now.isoformat(), risk, price, side * (result.price - price), stress])
        messages.append(f"📌 {market.name} {'BUY' if side > 0 else 'SELL'} {volume} lot @ {result.price} "
                        f"(ehtimol {p:.0%}, stop {stop:.5g}{f', TP {target:.5g}' if target else ''}, "
                        f"{exit_day} yopilishidan keyin chiqadi)")
    db.commit()
    return messages


def cycle(mt5, db: sqlite3.Connection, now: datetime | None = None) -> list[str]:
    now = now or datetime.now(timezone.utc)
    account = require_demo(mt5)
    paused, change = guards.drawdown_pause(db, account.equity)
    return ([change] if change else []) + close_due(mt5, db, now.date().isoformat()) + \
        decide(mt5, db, now, account, paused)


def main() -> None:
    import MetaTrader5 as mt5

    # The package is installed in site-packages, so look for .env in the folder the bot is started from.
    env_file = Path.cwd() / ".env"
    load_dotenv(env_file)
    setup_logging(Path.cwd() / "logs" / "mt5_bot.log")
    logger.info("settings: .env %s | risk %.2f%% | telegram %s", "found" if env_file.exists() else "NOT found",
                env_float("FX_BOT_RISK_PCT", 0.5),
                "on" if os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID") else "off")
    if not mt5.initialize():
        raise SystemExit(f"MetaTrader 5 initialize failed: {mt5.last_error()}")
    require_demo(mt5)
    db = open_db()
    notify("🧪 FX demo bot ishga tushdi (faqat DEMO hisob)\n" + startup_report(mt5))
    alerts: dict[str, float] = {}
    last_slow, started, lost_since = -1e9, datetime.now(timezone.utc), None
    while True:
        now = datetime.now(timezone.utc)
        if health.connection_lost(mt5):
            # a dropped terminal or broker link is waited out and retried, not turned into a restart every minute
            if lost_since is None:
                lost_since = now
                notify("⚠️ MT5 ulanishi yo'q (terminal yoki broker). Bot to'xtamaydi: har daqiqada qayta ulanadi.")
            logger.warning("MT5 connection lost since %s; reconnecting", lost_since.isoformat())
            mt5.initialize()
            time.sleep(60)
            continue
        if lost_since is not None:
            notify(f"✅ MT5 ulanishi tiklandi ({int((now - lost_since).total_seconds() // 60)} daqiqa uzilish).")
            lost_since = None
        slow = time.monotonic() - last_slow >= 15 * 60
        if slow:
            last_slow = time.monotonic()        # set first, so a failing engine is not retried every minute
        messages = tick(mt5, db, now, slow, alerts, started)
        if messages:
            notify("🧪 FX demo\n" + "\n".join(messages))
        if slow:
            account = mt5.account_info()
            open_n = db.execute("SELECT COUNT(*) FROM trades WHERE status = 'OPEN'").fetchone()[0]
            logger.info("heartbeat: equity %.2f | open trades %d | %s", getattr(account, "equity", 0.0), open_n,
                        " ; ".join(health.summary(db, watched_engines(), now)))
        time.sleep(60)


def setup_logging(path: Path) -> None:
    """Console plus a rotating file (5 x 5 MB) next to the bot, so what every engine did can be read back."""
    from logging.handlers import RotatingFileHandler
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(path, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(), handler])


ENGINE_ENVS = {"trend": ("FX_TREND_RISK_PCT", 0.5, "xom ashyo D1 Donchian"),
               "gold": ("FX_GOLD_RISK_PCT", 0.75, "oltin/kumush H4 Donchian"),
               "crypto": ("FX_CRYPTO_RISK_PCT", 0.3, "kripto H4 Donchian"),
               "index": ("FX_INDEX_RISK_PCT", 1.0, "indeks pullback D1"),
               "rebound": ("FX_REBOUND_RISK_PCT", 0.3, "kripto qulashdan qaytish H1"),
               "fix": ("FX_FIX_RISK_PCT", 0.25, "oy oxiri fix"),
               "fomc": ("FX_FOMC_RISK_PCT", 0.5, "FOMC oldidan US500 drift"),
               "ml": ("FX_BOT_RISK_PCT", 0.5, "haftalik ML (isbotlanmagan)")}


def watched_engines() -> list[str]:
    """Enabled engines plus the always-on risk and close steps: each must complete runs on its rhythm."""
    return [e for e in ENGINE_ENVS if e in enabled_engines()] + ["risk", "close"]


def enabled_engines() -> set[str]:
    """FX_BOT_ENGINES, e.g. "trend,fix,index,crypto,gold,rebound" to switch the weekly ML experiment off
    (default: all). Closing open trades, the risk state and the weekly report always run."""
    raw = os.environ.get("FX_BOT_ENGINES", "trend,fix,index,crypto,gold,rebound,fomc,ml")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def _markets_line(mt5, resolve, candidates, timeframe: str, need: int) -> str:
    """Markets found at this broker and whether each has the bars its signal needs."""
    found, missing, short = [], [], []
    for market in candidates:
        symbol = resolve(mt5, market)
        if not symbol:
            missing.append(market)
            continue
        try:
            # ask for at least as many bars as the engines do: completed_bars returns nothing below 80
            bars = len(trend_live.completed_bars(mt5, symbol, timeframe, max(need + 1, 300)))
        except Exception:
            bars = None
        if bars is None or bars >= need:
            found.append(f"{market}={symbol}")
        else:
            short.append(f"{market}={symbol} ({bars}/{need} {timeframe} bar)")
    line = ", ".join(found) or "yo'q"
    if short:
        line += f" | ma'lumot kam: {', '.join(short)}"
    if missing:
        line += f" | topilmadi: {', '.join(missing)} (FX_SYMBOL_MAP bilan ko'rsating)"
    return line


def startup_report(mt5) -> str:
    """Every engine, on or off, with its risk, the markets it found at this broker and whether their history is
    long enough, so a differently named symbol or a missing engine is never skipped silently."""
    enabled = enabled_engines()
    lines = []
    mode = getattr(mt5.account_info(), "margin_mode", None)
    if mode is not None and mode != getattr(mt5, "ACCOUNT_MARGIN_MODE_RETAIL_HEDGING", 2):
        lines.append("⚠️ Hisob NETTING turida: bir simvoldagi pozitsiyalar birlashib ketadi va bot ularni to'g'ri "
                     "boshqara olmaydi. HEDGE turidagi hisob oching.")
    for name, (env, default, title) in ENGINE_ENVS.items():
        state = "✅" if name in enabled else "⏸ o'chiq"
        lines.append(f"{state} {name}: {title}, risk {env_float(env, default):g}% ({env})")
    off = [n for n in ENGINE_ENVS if n not in enabled and n != "ml"]
    if off:
        lines.append(f"⚠️ O'chiq dvigatellar: {', '.join(off)}. Yoqish uchun .env: "
                     f"FX_BOT_ENGINES={','.join(sorted((enabled - {'ml'}) | set(off)))}")
    lines.append("Trend bozorlari: " + _markets_line(mt5, trend_live.resolve, trend_live.CANDIDATES, "D1",
                                                       trend_live.PARAMS["entry_n"] + 1))
    lines.append("Oltin/kumush H4: " + _markets_line(mt5, metals_live.resolve, metals_live.CANDIDATES, "H4", 101))
    lines.append("Indekslar: " + _markets_line(mt5, index_live.resolve, index_live.CANDIDATES, "D1", 210))
    lines.append("Kripto (H4 Donchian va H1 qaytish): " +
                 _markets_line(mt5, crypto_live.resolve, crypto_live.CANDIDATES, "H4", 101))
    fx_missing = [m.name for m in FX_ONLY if not resolve_symbol(mt5, m.name)]
    lines.append("FX juftliklar (fix): " + ("hammasi topildi" if not fx_missing else f"topilmadi: {', '.join(fx_missing)}"))
    lines.append("Loglar: logs\\mt5_bot.log (har 15 daqiqada heartbeat; o'qish: Get-Content ... -Encoding UTF8 -Wait)")
    return "\n".join(lines)


def tick(mt5, db: sqlite3.Connection, now: datetime, slow: bool, alerts: dict[str, float],
         started: datetime | None = None) -> list[str]:
    """One minute of the bot. slow=True also runs the 15-minute engines (risk state, weekly closes, ML,
    trend, report). Every engine is isolated: one failing never stops the others (the month-end fix
    must not be missed because a data source for the ML model is down)."""
    me = sys.modules[__name__]
    # the fix rule is time-critical (3-minute windows), so it runs before the slower engines
    engines = [("fix", lambda: fix_live.cycle(mt5, db, now, require_demo(mt5), _paused(db), me)),
               # the rebound buys the hour after a crash, so it also runs every minute (it skips stale signals)
               ("rebound", lambda: rebound_live.cycle(mt5, db, now, require_demo(mt5), _paused(db), me)),
               # buys at 14:00 New York the day before a Fed statement and must sell before 14:00 the next day
               ("fomc", lambda: fomc_live.cycle(mt5, db, now, require_demo(mt5), _paused(db), me))]
    if slow:
        engines += [("risk", lambda: [m for m in [guards.drawdown_pause(db, require_demo(mt5).equity)[1]] if m]),
                    ("close", lambda: close_due(mt5, db, now.date().isoformat()) + reconcile_orphans(mt5, db)),
                    ("ml", lambda: decide(mt5, db, now, require_demo(mt5), _paused(db))),
                    ("trend", lambda: trend_live.cycle(mt5, db, now, require_demo(mt5), _paused(db), me)),
                    ("index", lambda: index_live.cycle(mt5, db, now, require_demo(mt5), _paused(db), me)),
                    ("crypto", lambda: crypto_live.cycle(mt5, db, now, require_demo(mt5), _paused(db), me)),
                    ("gold", lambda: metals_live.cycle(mt5, db, now, require_demo(mt5), _paused(db), me)),
                    ("report", lambda: [r] if (r := guards.weekly_report(db, mt5.account_info().equity, now))
                     else []),
                    ("status", lambda: [r] if (r := status_report.daily_status(mt5, db, now, enabled_engines()))
                     else [])]
    switchable = {"fix", "ml", "trend", "index", "crypto", "gold", "rebound", "fomc"}
    engines = [(n, r) for n, r in engines if n not in switchable or n in enabled_engines()]
    messages: list[str] = []
    for name, run in engines:
        try:
            out = run()
            messages += out
            health.record_ok(db, name, now)
            if slow or out:
                logger.info("engine %s ok (%d messages)", name, len(out))
        except SystemExit:
            raise
        except Exception as exc:
            logger.exception("%s engine failed", name)
            health.record_error(db, name, now, exc)
            if time.monotonic() - alerts.get(name, -1e9) > 6 * 3600:
                messages.append(f"⚠️ {name} xatosi: {type(exc).__name__}: {exc}")
                alerts[name] = time.monotonic()
    if slow and started is not None:
        messages += health.stalled(db, watched_engines(), now, started)
    return messages

def _paused(db: sqlite3.Connection) -> bool:
    return guards.get(db, "dd_paused", "0") == "1"

if __name__ == "__main__":
    main()
