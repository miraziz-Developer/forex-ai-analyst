"""Long-only Donchian engines for the MT5 demo, on the broker's own completed bars.

- Commodity trend (docs/COMMODITY_TREND_STUDY.md): D1, 55-day breakout, 20-day exit, 2 ATR stop, on gold,
  silver, WTI, Brent, copper, platinum, palladium. Risk FX_TREND_RISK_PCT (0.5). `commodity_trend_long_v1`.
- Crypto CFDs (docs/CRYPTO_CFD_STUDY.md, `crypto_live.py`): the live BingX rule, H4, 100/20, 3 ATR.

A close above the previous entry-channel high buys at market with the ATR stop sent to MT5; a close below
the previous exit-channel low closes the trade. Sizing: the stop costs the engine's risk % of equity
(adaptive allocation), then the stress, portfolio-risk, spread and drawdown guards apply.
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from forex_ai_analyst.forex import mt5_guards as guards
from forex_ai_analyst.lab.strategies import donchian

OPEN_ENDED = "9999-12-31"            # exit_day for trades closed by a signal, never by the calendar
_SUFFIXES = ("", "m", ".", "#", ".r", "c", "pro", ".a", "-ECN")


def completed_bars(mt5, symbol: str, timeframe: str, count: int) -> list[dict]:
    """Completed bars, oldest first (the broker's current, still-forming bar is dropped)."""
    rates = mt5.copy_rates_from_pos(symbol, getattr(mt5, f"TIMEFRAME_{timeframe}"), 0, count)
    if rates is None or len(rates) < 80:
        return []
    return [{"datetime": int(r["time"]) * 1000, "open": float(r["open"]), "high": float(r["high"]),
             "low": float(r["low"]), "close": float(r["close"])} for r in rates[:-1]]


def daily_bars(mt5, symbol: str, count: int = 260) -> list[dict]:
    return completed_bars(mt5, symbol, "D1", count)


@dataclass(frozen=True)
class DonchianEngine:
    version: str
    label: str                       # Telegram tag, e.g. "trend"
    params: dict
    candidates: dict
    timeframe: str                   # "D1" or "H4"
    risk_env: str
    bar_count: int = 260

    def resolve(self, mt5, market: str) -> str | None:
        mapping = dict(item.split("=", 1) for item in os.environ.get("FX_SYMBOL_MAP", "").split(",") if "=" in item)
        names = ([mapping[market]] if market in mapping else []) + \
            [c + s for c in self.candidates[market] for s in _SUFFIXES]
        for name in names:
            if mt5.symbol_info(name) is not None:
                mt5.symbol_select(name, True)
                return name
        return None

    def bar_key(self, bar: dict) -> str:
        t = datetime.fromtimestamp(bar["datetime"] / 1000, timezone.utc)
        return t.date().isoformat() if self.timeframe == "D1" else t.strftime("%Y-%m-%dT%H:%M")

    def cycle(self, mt5, db: sqlite3.Connection, now: datetime, account, paused: bool, bot) -> list[str]:
        messages = []
        for market in self.candidates:
            symbol = self.resolve(mt5, market)
            if not symbol:
                continue
            bars = completed_bars(mt5, symbol, self.timeframe, self.bar_count)
            if len(bars) <= self.params["entry_n"]:
                continue
            sig = donchian(bars, **self.params)
            last = len(bars) - 1
            key = self.bar_key(bars[last])
            open_row = db.execute("SELECT * FROM trades WHERE model_version = ? AND market = ? AND status = 'OPEN'",
                                  [self.version, market]).fetchone()
            if open_row is not None:
                messages += self.manage(mt5, db, open_row, sig.long_exit[last], bot)
            elif sig.long_entry[last] and sig.stop_distance[last]:
                seen = db.execute("SELECT 1 FROM trades WHERE model_version = ? AND market = ? AND decision_day = ?",
                                  [self.version, market, key]).fetchone()
                if not seen:
                    messages += self.enter(mt5, db, now, account, paused, bot, market, symbol, key,
                                           sig.stop_distance[last])
        return messages

    def manage(self, mt5, db, row, exit_signal: bool, bot) -> list[str]:
        positions = mt5.positions_get(ticket=row["ticket"]) or ()
        stop_atr = self.params["stop_atr"]
        if not positions:
            db.execute("UPDATE trades SET status = 'CLOSED', note = ? WHERE id = ?",
                       [f"closed by the {stop_atr:g} ATR stop", row["id"]])
            db.commit()
            return [f"⛔ [{self.label}] {row['market']}: {stop_atr:g} ATR stop yopgan"]
        if not exit_signal:
            return []
        position = positions[0]
        result = bot._send(mt5, row["symbol"], -1, position.volume, None, f"{self.label} exit", position=position.ticket)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            return [f"❌ [{self.label}] {row['market']} yopilmadi: {getattr(result, 'comment', mt5.last_error())}"]
        db.execute("UPDATE trades SET status = 'CLOSED', exit_price = ?, profit = ? WHERE id = ?",
                   [result.price, position.profit, row["id"]])
        db.commit()
        return [f"{'🟢' if position.profit > 0 else '🔴'} [{self.label}] {row['market']} yopildi "
                f"({self.params['exit_n']} barlik past nuqta): {position.profit:+.2f}"]

    def enter(self, mt5, db, now, account, paused, bot, market, symbol, key, stop_distance) -> list[str]:
        tick = mt5.symbol_info_tick(symbol)
        info = mt5.symbol_info(symbol)
        risk_pct = guards.engine_risk_pct(db, self.version, guards.env_float(self.risk_env, 0.5))
        volume = bot.volume_for(mt5, symbol, stop_distance, account.equity * risk_pct / 100)
        mpp = info.trade_tick_value / (info.trade_tick_size or info.point)
        volume = guards.stress_volume(market, volume, mpp, tick.ask, account.equity, info.volume_step,
                                      info.volume_min) if volume > 0 else 0.0
        risk = bot.risk_of(mt5, symbol, volume, stop_distance) if volume > 0 else 0.0
        stress = guards.stress_loss(market, volume, mpp, tick.ask)
        base = [self.version, key, market, 1, 0.0, OPEN_ENDED, now.isoformat()]
        blocked = ("below minimum volume" if volume <= 0 else "drawdown pause" if paused else
                   "stress limit" if not guards.stress_room(db, account.equity, stress) else
                   "portfolio risk limit" if not guards.risk_room(db, account.equity, risk) else
                   "spread too wide" if not guards.spread_ok(tick.ask, tick.bid, stop_distance) else None)
        if blocked:
            if blocked != "spread too wide":                   # a wide spread is retried on the next cycle
                db.execute("INSERT OR IGNORE INTO trades (model_version, decision_day, market, side, prob, exit_day, "
                           "created_at, status, note) VALUES (?, ?, ?, ?, ?, ?, ?, 'SKIPPED', ?)", base + [blocked])
                db.commit()
            return [f"⏸ [{self.label}] {market} BUY signali: {blocked}"]
        stop = tick.ask - stop_distance
        result = bot._send(mt5, symbol, 1, volume, stop, self.label)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            why = getattr(result, "comment", None) or str(mt5.last_error())
            db.execute("INSERT OR IGNORE INTO trades (model_version, decision_day, market, side, prob, exit_day, "
                       "created_at, status, note) VALUES (?, ?, ?, ?, ?, ?, ?, 'SKIPPED', ?)",
                       base + [f"order rejected: {why}"[:120]])
            db.commit()
            return [f"❌ [{self.label}] {market}: order rad etildi ({why})"]
        db.execute("INSERT OR IGNORE INTO trades (model_version, decision_day, market, symbol, side, prob, ticket, "
                   "volume, entry_price, stop, exit_day, created_at, status, risk_money, requested_price, slippage, "
                   "stress_money) VALUES (?, ?, ?, ?, 1, 0.0, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?)",
                   [self.version, key, market, symbol, result.order, volume, result.price, stop, OPEN_ENDED,
                    now.isoformat(), risk, tick.ask, result.price - tick.ask, stress])
        db.commit()
        return [f"📌 [{self.label}] {market} BUY {volume} lot @ {result.price} "
                f"({self.params['entry_n']} barlik yuqori nuqta yorildi, stop {stop:.5g})"]


COMMODITIES = DonchianEngine(
    version="commodity_trend_long_v1", label="trend",
    params={"entry_n": 55, "exit_n": 20, "stop_atr": 2.0, "sides": "long"},
    candidates={
        "XAUUSD": ["XAUUSD", "GOLD"], "XAGUSD": ["XAGUSD", "SILVER"],
        "WTI": ["XTIUSD", "USOIL", "WTI", "USOil", "CL-OIL", "OILUSD"], "BRENT": ["XBRUSD", "UKOIL", "BRENT", "UKOil"],
        "COPPER": ["XCUUSD", "COPPER", "HG"], "PLATINUM": ["XPTUSD", "PLATINUM"], "PALLADIUM": ["XPDUSD", "PALLADIUM"],
    },
    timeframe="D1", risk_env="FX_TREND_RISK_PCT")
guards.STRESS_MOVE.update({"WTI": 0.30, "BRENT": 0.30, "COPPER": 0.10, "PLATINUM": 0.12, "PALLADIUM": 0.20,
                           "XAGUSD": 0.15})

# module-level API used by the bot and the tests
VERSION, PARAMS, CANDIDATES = COMMODITIES.version, COMMODITIES.params, COMMODITIES.candidates
resolve, cycle = COMMODITIES.resolve, COMMODITIES.cycle
