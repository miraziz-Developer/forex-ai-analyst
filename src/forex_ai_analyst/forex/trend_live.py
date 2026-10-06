"""Commodity long trend, forward test on the MT5 demo (docs/COMMODITY_TREND_STUDY.md, rule unchanged).

Long-only Donchian on completed daily bars of the broker's own D1 data: a close above the previous
55-day high buys at market with a 2 ATR stop; a close below the previous 20-day low closes the trade.
Markets: gold, silver, WTI, Brent, copper, platinum, palladium (whichever the broker offers).
Sizing: the 2 ATR stop costs FX_TREND_RISK_PCT (0.5) of equity, then the stress, portfolio-risk,
spread and drawdown guards apply. Journalled as `commodity_trend_long_v1`.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone

from forex_ai_analyst.forex import mt5_guards as guards
from forex_ai_analyst.lab.strategies import donchian

VERSION = "commodity_trend_long_v1"
PARAMS = {"entry_n": 55, "exit_n": 20, "stop_atr": 2.0, "sides": "long"}
OPEN_ENDED = "9999-12-31"            # exit_day for trades closed by a signal, never by the calendar
CANDIDATES = {
    "XAUUSD": ["XAUUSD", "GOLD"], "XAGUSD": ["XAGUSD", "SILVER"],
    "WTI": ["XTIUSD", "USOIL", "WTI", "USOil", "CL-OIL", "OILUSD"], "BRENT": ["XBRUSD", "UKOIL", "BRENT", "UKOil"],
    "COPPER": ["XCUUSD", "COPPER", "HG"], "PLATINUM": ["XPTUSD", "PLATINUM"], "PALLADIUM": ["XPDUSD", "PALLADIUM"],
}
_SUFFIXES = ("", "m", ".", "#", ".r", "c", "pro", ".a", "-ECN")
guards.STRESS_MOVE.update({"WTI": 0.30, "BRENT": 0.30, "COPPER": 0.10, "PLATINUM": 0.12, "PALLADIUM": 0.20,
                           "XAGUSD": 0.15})


def resolve(mt5, market: str) -> str | None:
    mapping = dict(item.split("=", 1) for item in os.environ.get("FX_SYMBOL_MAP", "").split(",") if "=" in item)
    names = ([mapping[market]] if market in mapping else []) + \
        [c + s for c in CANDIDATES[market] for s in _SUFFIXES]
    for name in names:
        if mt5.symbol_info(name) is not None:
            mt5.symbol_select(name, True)
            return name
    return None


def daily_bars(mt5, symbol: str, count: int = 260) -> list[dict]:
    """Completed D1 bars, oldest first (the broker's current, still-forming bar is dropped)."""
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_D1, 0, count)
    if rates is None or len(rates) < 80:
        return []
    return [{"datetime": int(r["time"]) * 1000, "open": float(r["open"]), "high": float(r["high"]),
             "low": float(r["low"]), "close": float(r["close"])} for r in rates[:-1]]


def cycle(mt5, db: sqlite3.Connection, now: datetime, account, paused: bool, bot) -> list[str]:
    messages = []
    for market in CANDIDATES:
        symbol = resolve(mt5, market)
        if not symbol:
            continue
        bars = daily_bars(mt5, symbol)
        if not bars:
            continue
        sig = donchian(bars, **PARAMS)
        last = len(bars) - 1
        day = datetime.fromtimestamp(bars[last]["datetime"] / 1000, timezone.utc).date().isoformat()
        open_row = db.execute("SELECT * FROM trades WHERE model_version = ? AND market = ? AND status = 'OPEN'",
                              [VERSION, market]).fetchone()
        if open_row is not None:
            messages += manage(mt5, db, open_row, sig.long_exit[last], bot)
        elif sig.long_entry[last] and sig.stop_distance[last]:
            seen = db.execute("SELECT 1 FROM trades WHERE model_version = ? AND market = ? AND decision_day = ?",
                              [VERSION, market, day]).fetchone()
            if not seen:
                messages += enter(mt5, db, now, account, paused, bot, market, symbol, day, sig.stop_distance[last])
    return messages


def manage(mt5, db, row, exit_signal: bool, bot) -> list[str]:
    positions = mt5.positions_get(ticket=row["ticket"]) or ()
    if not positions:
        db.execute("UPDATE trades SET status = 'CLOSED', note = 'closed by the 2 ATR stop' WHERE id = ?", [row["id"]])
        db.commit()
        return [f"⛔ [trend] {row['market']}: 2 ATR stop yopgan"]
    if not exit_signal:
        return []
    position = positions[0]
    result = bot._send(mt5, row["symbol"], -1, position.volume, None, "trend exit", position=position.ticket)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        return [f"❌ [trend] {row['market']} yopilmadi: {getattr(result, 'comment', mt5.last_error())}"]
    db.execute("UPDATE trades SET status = 'CLOSED', exit_price = ?, profit = ? WHERE id = ?",
               [result.price, position.profit, row["id"]])
    db.commit()
    return [f"{'🟢' if position.profit > 0 else '🔴'} [trend] {row['market']} yopildi (20 kunlik past nuqta): "
            f"{position.profit:+.2f}"]


def enter(mt5, db, now, account, paused, bot, market, symbol, day, stop_distance) -> list[str]:
    tick = mt5.symbol_info_tick(symbol)
    info = mt5.symbol_info(symbol)
    volume = bot.volume_for(mt5, symbol, stop_distance, account.equity * guards.env_float("FX_TREND_RISK_PCT", 0.5) / 100)
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
        return [f"⏸ [trend] {market} BUY signali: {blocked}"]
    stop = tick.ask - stop_distance
    result = bot._send(mt5, symbol, 1, volume, stop, f"trend {VERSION}")
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        return [f"❌ [trend] {market}: order rad etildi ({getattr(result, 'comment', mt5.last_error())})"]
    db.execute("INSERT OR IGNORE INTO trades (model_version, decision_day, market, symbol, side, prob, ticket, volume, "
               "entry_price, stop, exit_day, created_at, status, risk_money, requested_price, slippage, stress_money) "
               "VALUES (?, ?, ?, ?, 1, 0.0, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?)",
               [VERSION, day, market, symbol, result.order, volume, result.price, stop, OPEN_ENDED, now.isoformat(),
                risk, tick.ask, result.price - tick.ask, stress])
    db.commit()
    return [f"📌 [trend] {market} BUY {volume} lot @ {result.price} (55 kunlik yuqori nuqta yorildi, stop {stop:.5g})"]
