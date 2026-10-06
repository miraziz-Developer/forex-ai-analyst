"""Index pullback in an uptrend (IDX1), forward test on the MT5 demo (docs/INDEX_STUDY.md, rule unchanged).

On completed D1 bars of the broker's index CFDs: close above its 200-day average and RSI(2) below 10 buys
at market with a 3 ATR(14) stop; the trade is closed after a close above the 5-day average or after 10
completed bars. Markets: US500, US30, NAS100, GER40, UK100, JP225 (whichever the broker offers).
Sizing: the 3 ATR stop costs FX_INDEX_RISK_PCT (0.5) of equity, with adaptive allocation and the stress,
portfolio, spread and drawdown guards. Journalled as `index_pullback_v1`.
The study used cash-index daily bars; CFD D1 bars include the overnight session (stated, not adjusted).
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone

from forex_ai_analyst.forex import mt5_guards as guards
from forex_ai_analyst.forex.index_study import MAX_HOLD, STOP_ATR, sma
from forex_ai_analyst.lab.candidates import rsi
from forex_ai_analyst.lab.strategies import atr

VERSION = "index_pullback_v1"
OPEN_ENDED = "9999-12-31"
CANDIDATES = {
    "US500": ["US500", "SPX500", "SP500", "US500Cash", "USA500"],
    "US30": ["US30", "DJ30", "WS30", "DOW30", "US30Cash", "USA30"],
    "NAS100": ["US100", "NAS100", "USTEC", "NDX100", "US100Cash", "USTECH"],
    "GER40": ["DE40", "GER40", "DE30", "GER30", "DAX40", "DE40Cash"],
    "UK100": ["UK100", "FTSE100", "UK100Cash"],
    "JP225": ["JP225", "JPN225", "NIKKEI225", "JP225Cash", "NI225"],
}
_SUFFIXES = ("", "m", ".", "#", ".r", "c", "pro", ".a", "-ECN", ".cash")
# Worst documented one-day falls, rounded up: March 2020 (-12% to -13%), Aug 2024 Nikkei (-12.4%), 1987.
guards.STRESS_MOVE.update({"US500": 0.13, "US30": 0.14, "NAS100": 0.14, "GER40": 0.14, "UK100": 0.12,
                           "JP225": 0.16})


def resolve(mt5, market: str) -> str | None:
    mapping = dict(item.split("=", 1) for item in os.environ.get("FX_SYMBOL_MAP", "").split(",") if "=" in item)
    names = ([mapping[market]] if market in mapping else []) + \
        [c + s for c in CANDIDATES[market] for s in _SUFFIXES]
    for name in names:
        if mt5.symbol_info(name) is not None:
            mt5.symbol_select(name, True)
            return name
    return None


def signals(bars: list[dict]) -> tuple[bool, bool, float | None]:
    """(entry, exit_signal, 3 ATR stop distance) on the last completed bar."""
    closes = [b["close"] for b in bars]
    last = len(bars) - 1
    s200, s5, r2, a = sma(closes, 200)[last], sma(closes, 5)[last], rsi(closes, 2)[last], atr(bars, 14)[last]
    entry = s200 is not None and r2 is not None and closes[last] > s200 and r2 < 10
    exit_ = s5 is not None and closes[last] > s5
    return entry, exit_, (STOP_ATR * a if a else None)


def cycle(mt5, db: sqlite3.Connection, now: datetime, account, paused: bool, bot) -> list[str]:
    from forex_ai_analyst.forex.trend_live import daily_bars
    messages = []
    for market in CANDIDATES:
        symbol = resolve(mt5, market)
        if not symbol:
            continue
        bars = daily_bars(mt5, symbol)
        if len(bars) < 210:
            continue
        entry, exit_signal, stop_distance = signals(bars)
        day = datetime.fromtimestamp(bars[-1]["datetime"] / 1000, timezone.utc).date().isoformat()
        row = db.execute("SELECT * FROM trades WHERE model_version = ? AND market = ? AND status = 'OPEN'",
                         [VERSION, market]).fetchone()
        if row is not None:
            held = sum(1 for b in bars
                       if datetime.fromtimestamp(b["datetime"] / 1000, timezone.utc).date().isoformat() > row["decision_day"])
            messages += close_if(mt5, db, row, exit_signal or held >= MAX_HOLD, bot)
        elif entry and stop_distance:
            if not db.execute("SELECT 1 FROM trades WHERE model_version = ? AND market = ? AND decision_day = ?",
                              [VERSION, market, day]).fetchone():
                messages += enter(mt5, db, now, account, paused, bot, market, symbol, day, stop_distance)
    return messages


def close_if(mt5, db, row, due: bool, bot) -> list[str]:
    positions = mt5.positions_get(ticket=row["ticket"]) or ()
    if not positions:
        db.execute("UPDATE trades SET status = 'CLOSED', note = 'closed by the 3 ATR stop' WHERE id = ?", [row["id"]])
        db.commit()
        return [f"⛔ [index] {row['market']}: 3 ATR stop yopgan"]
    if not due:
        return []
    position = positions[0]
    result = bot._send(mt5, row["symbol"], -1, position.volume, None, "index exit", position=position.ticket)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        return [f"❌ [index] {row['market']} yopilmadi: {getattr(result, 'comment', mt5.last_error())}"]
    db.execute("UPDATE trades SET status = 'CLOSED', exit_price = ?, profit = ? WHERE id = ?",
               [result.price, position.profit, row["id"]])
    db.commit()
    return [f"{'🟢' if position.profit > 0 else '🔴'} [index] {row['market']} yopildi: {position.profit:+.2f}"]


def enter(mt5, db, now, account, paused, bot, market, symbol, day, stop_distance) -> list[str]:
    tick = mt5.symbol_info_tick(symbol)
    info = mt5.symbol_info(symbol)
    risk_pct = guards.engine_risk_pct(db, VERSION, guards.env_float("FX_INDEX_RISK_PCT", 0.5))
    volume = bot.volume_for(mt5, symbol, stop_distance, account.equity * risk_pct / 100)
    mpp = info.trade_tick_value / (info.trade_tick_size or info.point)
    volume = guards.stress_volume(market, volume, mpp, tick.ask, account.equity, info.volume_step, info.volume_min) \
        if volume > 0 else 0.0
    risk = bot.risk_of(mt5, symbol, volume, stop_distance) if volume > 0 else 0.0
    stress = guards.stress_loss(market, volume, mpp, tick.ask)
    base = [VERSION, day, market, 1, 0.0, OPEN_ENDED, now.isoformat()]
    blocked = ("below minimum volume" if volume <= 0 else "drawdown pause" if paused else
               "stress limit" if not guards.stress_room(db, account.equity, stress) else
               "portfolio risk limit" if not guards.risk_room(db, account.equity, risk) else
               "spread too wide" if not guards.spread_ok(tick.ask, tick.bid, stop_distance) else None)
    if blocked:
        if blocked != "spread too wide":                       # a wide spread is retried on the next cycle
            db.execute("INSERT OR IGNORE INTO trades (model_version, decision_day, market, side, prob, exit_day, "
                       "created_at, status, note) VALUES (?, ?, ?, ?, ?, ?, ?, 'SKIPPED', ?)", base + [blocked])
            db.commit()
        return [f"⏸ [index] {market} BUY signali: {blocked}"]
    stop = tick.ask - stop_distance
    result = bot._send(mt5, symbol, 1, volume, stop, "index")
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        why = getattr(result, "comment", None) or str(mt5.last_error())
        db.execute("INSERT OR IGNORE INTO trades (model_version, decision_day, market, side, prob, exit_day, "
                   "created_at, status, note) VALUES (?, ?, ?, ?, ?, ?, ?, 'SKIPPED', ?)",
                   base + [f"order rejected: {why}"[:120]])
        db.commit()
        return [f"❌ [index] {market}: order rad etildi ({why})"]
    db.execute("INSERT OR IGNORE INTO trades (model_version, decision_day, market, symbol, side, prob, ticket, volume, "
               "entry_price, stop, exit_day, created_at, status, risk_money, requested_price, slippage, stress_money) "
               "VALUES (?, ?, ?, ?, 1, 0.0, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?)",
               [VERSION, day, market, symbol, result.order, volume, result.price, stop, OPEN_ENDED, now.isoformat(),
                risk, tick.ask, result.price - tick.ask, stress])
    db.commit()
    return [f"📌 [index] {market} BUY {volume} lot @ {result.price} (trendda keskin tushish, stop {stop:.5g})"]
