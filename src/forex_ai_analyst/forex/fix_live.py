"""Month-end London 4pm fix reversal, forward test on the MT5 demo (docs/FIX_STUDY.md, rules unchanged).

The backtest (2016-2026) pointed the right way but missed the evidence bar (P 0.67 / 0.81). The only
honest way to get more evidence is data nobody has seen: this runs the exact pre-registered rule live.
On the last weekday of each month (London date): mid at 15:00 and at 15:55 London, enter against the
move at 16:03, exit at 12:00 London on the next weekday. Every trade is journalled as FIX1; those with
|move| >= 0.10% also count for FIX2. Sizing: a 1% emergency stop that costs FX_FIX_RISK_PCT (0.25) of
equity. The portfolio, stress and drawdown guards apply; the news filter does not (the rule trades
through the fix by design).
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from forex_ai_analyst.forex import fix_study
from forex_ai_analyst.forex import mt5_guards as guards

VERSION = "fix_month_end_v1"
LONDON = ZoneInfo("Europe/London")
STOP_FRACTION = 0.01


def last_weekday(d: date) -> date:
    nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1) - timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt -= timedelta(days=1)
    return nxt


def _mid(mt5, symbol: str) -> float | None:
    tick = mt5.symbol_info_tick(symbol)
    return (tick.ask + tick.bid) / 2 if tick else None


def cycle(mt5, db: sqlite3.Connection, now: datetime, account, paused: bool, bot) -> list[str]:
    """Called every minute. `bot` is the mt5_bot module (symbol, volume and order helpers)."""
    london = now.astimezone(LONDON)
    today, hm = london.date(), (london.hour, london.minute)
    messages = close_due(mt5, db, now, bot)
    if today != last_weekday(today) or today.weekday() >= 5:
        return messages
    for pair in fix_study.PAIRS:
        symbol = bot.resolve_symbol(mt5, pair)
        if not symbol:
            continue
        key = f"fix:{today.isoformat()}:{pair}"
        if (15, 0) <= hm < (15, 5) and guards.get(db, key + ":a") is None:
            guards.put(db, key + ":a", _mid(mt5, symbol))
        elif (15, 55) <= hm < (15, 58) and guards.get(db, key + ":b") is None:
            guards.put(db, key + ":b", _mid(mt5, symbol))
        elif (16, 3) <= hm < (16, 15) and guards.get(db, key + ":done") is None:
            guards.put(db, key + ":done", 1)
            messages += enter(mt5, db, now, account, paused, bot, pair, symbol, key)
    return messages


def enter(mt5, db, now, account, paused, bot, pair, symbol, key) -> list[str]:
    a, b = guards.get(db, key + ":a"), guards.get(db, key + ":b")
    if a is None or b is None or float(a) <= 0:
        return [f"⚠️ [fix] {pair}: 15:00/15:55 narxlari yozilmagan (bot o'sha paytda ishlamagan), o'tkazildi"]
    move = float(b) / float(a) - 1
    if move == 0:
        return []
    side = -1 if move > 0 else 1
    tick = mt5.symbol_info_tick(symbol)
    price = tick.ask if side > 0 else tick.bid
    stop_distance = STOP_FRACTION * price
    volume = bot.volume_for(mt5, symbol, stop_distance, account.equity * guards.engine_risk_pct(db, VERSION, guards.env_float("FX_FIX_RISK_PCT", 0.25)) / 100)
    info = mt5.symbol_info(symbol)
    mpp = info.trade_tick_value / (info.trade_tick_size or info.point)
    volume = guards.stress_volume(pair, volume, mpp, price, account.equity, info.volume_step, info.volume_min) \
        if volume > 0 else 0.0
    risk = bot.risk_of(mt5, symbol, volume, stop_distance) if volume > 0 else 0.0
    stress = guards.stress_loss(pair, volume, mpp, price)
    exit_day = fix_study.next_weekday(now.astimezone(LONDON).date()).isoformat()
    base = [VERSION, now.astimezone(LONDON).date().isoformat(), pair, side, move, exit_day, now.isoformat()]
    blocked = ("below minimum volume" if volume <= 0 else "drawdown pause" if paused else
               "stress limit" if not guards.stress_room(db, account.equity, stress) else
               "portfolio risk limit" if not guards.risk_room(db, account.equity, risk) else
               "spread too wide" if not guards.spread_ok(tick.ask, tick.bid, stop_distance) else None)
    if blocked:
        db.execute("INSERT OR IGNORE INTO trades (model_version, decision_day, market, side, prob, exit_day, "
                   "created_at, status, note) VALUES (?, ?, ?, ?, ?, ?, ?, 'SKIPPED', ?)", base + [blocked])
        db.commit()
        return [f"⏸ [fix] {pair}: {blocked}"]
    stop = price - side * stop_distance
    result = bot._send(mt5, symbol, side, volume, stop, "fix")
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        return [f"❌ [fix] {pair}: order rad etildi ({getattr(result, 'comment', mt5.last_error())})"]
    db.execute("INSERT OR IGNORE INTO trades (model_version, decision_day, market, symbol, side, prob, ticket, volume, "
               "entry_price, stop, exit_day, created_at, status, risk_money, requested_price, slippage, stress_money) "
               "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?)",
               [VERSION, base[1], pair, symbol, side, move, result.order, volume, result.price, stop, exit_day,
                now.isoformat(), risk, price, side * (result.price - price), stress])
    db.commit()
    return [f"📌 [fix] {pair} {'BUY' if side > 0 else 'SELL'} {volume} lot @ {result.price} "
            f"(fix oldidan harakat {move * 100:+.3f}%, ertaga 12:00 Londonda yopiladi)"]


def close_due(mt5, db, now: datetime, bot) -> list[str]:
    london = now.astimezone(LONDON)
    if (london.hour, london.minute) < (12, 0):
        cutoff = (london.date() - timedelta(days=1)).isoformat()      # before noon only overdue trades
    else:
        cutoff = london.date().isoformat()
    messages = []
    for row in db.execute("SELECT * FROM trades WHERE model_version = ? AND status = 'OPEN' AND exit_day <= ?",
                          [VERSION, cutoff]).fetchall():
        positions = mt5.positions_get(ticket=row["ticket"]) or ()
        if not positions:
            db.execute("UPDATE trades SET status = 'CLOSED', note = 'closed by the emergency stop' WHERE id = ?",
                       [row["id"]])
            messages.append(f"⛔ [fix] {row['market']}: favqulodda stop yopgan")
            continue
        position = positions[0]
        result = bot._send(mt5, row["symbol"], -row["side"], position.volume, None, "fix exit",
                           position=position.ticket)
        if result is not None and result.retcode == mt5.TRADE_RETCODE_DONE:
            db.execute("UPDATE trades SET status = 'CLOSED', exit_price = ?, profit = ? WHERE id = ?",
                       [result.price, position.profit, row["id"]])
            messages.append(f"{'🟢' if position.profit > 0 else '🔴'} [fix] {row['market']} yopildi: "
                            f"{position.profit:+.2f}")
    db.commit()
    return messages

