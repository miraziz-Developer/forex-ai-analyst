"""Crypto capitulation rebound on the MT5 crypto CFDs, H1, forward test on the demo
(docs/CRYPTO_ENGINE2_STUDY.md rule B; replicated on ten unseen coins; docs/CRYPTO_CFD_STUDY.md cost check).

On completed H1 bars: a close at least 12% below the close 24 hours earlier, on a green hour, buys at
market. Stop 0.5 ATR(14) below the 24-hour low and a target half-way back up the 24-hour fall, both sent to
MT5; the trade is closed at market after 24 completed hours if neither was hit. The stop and target keep the
backtest's distances from the fill. A signal that price has already run more than half a stop distance away
from is skipped. Risk FX_REBOUND_RISK_PCT (0.3), with adaptive allocation and the stress, portfolio,
spread and drawdown guards. Journalled as `crypto_rebound_h1_v1`; on a hedge account it lives beside the
H4 Donchian position on the same coin.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from forex_ai_analyst.forex import mt5_guards as guards
from forex_ai_analyst.forex.crypto_live import CRYPTO
from forex_ai_analyst.forex.trend_live import OPEN_ENDED, completed_bars
from forex_ai_analyst.lab.engine2_study import HOLD, capitulation_rebound

VERSION, LABEL = "crypto_rebound_h1_v1", "rebound"
CANDIDATES, resolve = CRYPTO.candidates, CRYPTO.resolve
HOUR_MS = 3_600_000
MAX_CHASE = 0.5


def bar_key(bar: dict) -> str:
    return datetime.fromtimestamp(bar["datetime"] / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M")


def cycle(mt5, db: sqlite3.Connection, now: datetime, account, paused: bool, bot) -> list[str]:
    messages = []
    for market in CANDIDATES:
        symbol = resolve(mt5, market)
        if not symbol:
            continue
        bars = completed_bars(mt5, symbol, "H1", 300)
        if len(bars) < 100:
            continue
        row = db.execute("SELECT * FROM trades WHERE model_version = ? AND market = ? AND status = 'OPEN'",
                         [VERSION, market]).fetchone()
        if row is not None:
            messages += manage(mt5, db, row, bars, bot)
            continue
        sig = capitulation_rebound(bars)
        last = len(bars) - 1
        if not sig.long_entry[last] or not sig.stop_distance[last]:
            continue
        key = bar_key(bars[last])
        if db.execute("SELECT 1 FROM trades WHERE model_version = ? AND market = ? AND decision_day = ?",
                      [VERSION, market, key]).fetchone():
            continue
        messages += enter(mt5, db, now, account, paused, bot, market, symbol, key, bars[last]["close"],
                          sig.stop_distance[last], sig.target_distance[last])
    return messages


def manage(mt5, db, row, bars: list[dict], bot) -> list[str]:
    positions = mt5.positions_get(ticket=row["ticket"]) or ()
    if not positions:
        deals = getattr(mt5, "history_deals_get", lambda **_: None)(position=row["ticket"]) or ()
        profit = sum(d.profit + d.commission + d.swap for d in deals) if deals else None
        db.execute("UPDATE trades SET status = 'CLOSED', profit = ?, note = 'closed by its stop or target' "
                   "WHERE id = ?", [profit, row["id"]])
        db.commit()
        result = "" if profit is None else f": {profit:+.2f}"
        return [f"{'🎯' if (profit or 0) > 0 else '⛔'} [{LABEL}] {row['market']} stop/target bilan yopildi{result}"]
    signal_open = datetime.strptime(row["decision_day"], "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc)
    entry_ms = int(signal_open.timestamp() * 1000) + HOUR_MS          # bought after the signal hour closed
    if sum(1 for b in bars if b["datetime"] >= entry_ms) < HOLD:
        return []
    position = positions[0]
    result = bot._send(mt5, row["symbol"], -1, position.volume, None, f"{LABEL} time", position=position.ticket)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        if guards.market_closed(mt5, result):
            return []
        return [f"❌ [{LABEL}] {row['market']} yopilmadi: {getattr(result, 'comment', mt5.last_error())}"]
    db.execute("UPDATE trades SET status = 'CLOSED', exit_price = ?, profit = ?, note = '24 h time exit' WHERE id = ?",
               [result.price, position.profit, row["id"]])
    db.commit()
    return [f"{'🟢' if position.profit > 0 else '🔴'} [{LABEL}] {row['market']} 24 soatdan keyin yopildi: "
            f"{position.profit:+.2f}"]


def _skip(db, base: list, note: str) -> None:
    db.execute("INSERT OR IGNORE INTO trades (model_version, decision_day, market, side, prob, exit_day, "
               "created_at, status, note) VALUES (?, ?, ?, ?, ?, ?, ?, 'SKIPPED', ?)", base + [note[:120]])
    db.commit()


def enter(mt5, db, now, account, paused, bot, market, symbol, key, close, stop_distance, target_distance) -> list[str]:
    tick = mt5.symbol_info_tick(symbol)
    info = mt5.symbol_info(symbol)
    base = [VERSION, key, market, 1, 0.0, OPEN_ENDED, now.isoformat()]
    if tick.ask - close > MAX_CHASE * stop_distance:
        _skip(db, base, "price already ran away from the signal")
        return [f"⏸ [{LABEL}] {market}: narx signaldan uzoqlashgan, quvlamaydi"]
    risk_pct = guards.engine_risk_pct(db, VERSION, guards.env_float("FX_REBOUND_RISK_PCT", 0.3))
    volume = bot.volume_for(mt5, symbol, stop_distance, account.equity * risk_pct / 100)
    mpp = info.trade_tick_value / (info.trade_tick_size or info.point)
    volume = guards.stress_volume(market, volume, mpp, tick.ask, account.equity, info.volume_step,
                                  info.volume_min) if volume > 0 else 0.0
    risk = bot.risk_of(mt5, symbol, volume, stop_distance) if volume > 0 else 0.0
    stress = guards.stress_loss(market, volume, mpp, tick.ask)
    blocked = ("below minimum volume" if volume <= 0 else "drawdown pause" if paused else
               "stress limit" if not guards.stress_room(db, account.equity, stress) else
               "portfolio risk limit" if not guards.risk_room(db, account.equity, risk) else
               "spread too wide" if not guards.spread_ok(tick.ask, tick.bid, stop_distance) else None)
    if blocked:
        if blocked != "spread too wide":                   # a wide spread is retried on the next cycle
            _skip(db, base, blocked)
        return [f"⏸ [{LABEL}] {market} BUY signali: {blocked}"]
    stop, target = tick.ask - stop_distance, tick.ask + target_distance
    result = bot._send(mt5, symbol, 1, volume, stop, LABEL, tp=target)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        if guards.market_closed(mt5, result):
            return []                                       # no journal row: retried when the market reopens
        why = getattr(result, "comment", None) or str(mt5.last_error())
        _skip(db, base, f"order rejected: {why}")
        return [f"❌ [{LABEL}] {market}: order rad etildi ({why})"]
    db.execute("INSERT OR IGNORE INTO trades (model_version, decision_day, market, symbol, side, prob, ticket, "
               "volume, entry_price, stop, exit_day, created_at, status, risk_money, requested_price, slippage, "
               "stress_money) VALUES (?, ?, ?, ?, 1, 0.0, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?)",
               [VERSION, key, market, symbol, result.order, volume, result.price, stop, OPEN_ENDED,
                now.isoformat(), risk, tick.ask, result.price - tick.ask, stress])
    db.commit()
    return [f"📌 [{LABEL}] {market} BUY {volume} lot @ {result.price} (24 soatda 12%+ qulash, yashil soat; "
            f"stop {stop:.5g}, target {target:.5g}, ko'pi bilan 24 soat)"]
