"""Once-a-day status message for the MT5 demo bot, so a quiet day is visibly a quiet day.

Shows the balance, open positions, trades of the last 24 hours and, per engine, the markets closest to a
signal: distance to the Donchian breakout level for the trend engines, RSI(2) for the index pullback
(a buy needs RSI(2) < 10 above the 200-day average), the coins that fell most in 24 hours for the rebound
(a buy needs -12%) and the days left to the month-end fix.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from forex_ai_analyst.forex import crypto_live, fix_live, index_live, metals_live, trend_live
from forex_ai_analyst.forex import mt5_guards as guards
from forex_ai_analyst.forex.index_study import sma
from forex_ai_analyst.lab.candidates import rsi

SEND_FROM_HOUR_UTC = 6


def _breakout_distances(mt5, engine) -> list[tuple[float, str]]:
    out = []
    for market in engine.candidates:
        symbol = engine.resolve(mt5, market)
        if not symbol:
            continue
        bars = trend_live.completed_bars(mt5, symbol, engine.timeframe, engine.bar_count)
        n = engine.params["entry_n"]
        if len(bars) <= n:
            continue
        level = max(b["high"] for b in bars[-n - 1:-1])
        out.append((level / bars[-1]["close"] - 1, market))
    return sorted(out)


def _index_rsi(mt5) -> list[tuple[float, str]]:
    out = []
    for market in index_live.CANDIDATES:
        symbol = index_live.resolve(mt5, market)
        if not symbol:
            continue
        bars = trend_live.daily_bars(mt5, symbol)
        if len(bars) < 210:
            continue
        closes = [b["close"] for b in bars]
        if closes[-1] > (sma(closes, 200)[-1] or float("inf")):
            out.append((rsi(closes, 2)[-1] or 100.0, market))
    return sorted(out)


def _crypto_falls(mt5) -> list[tuple[float, str]]:
    out = []
    for market in crypto_live.CANDIDATES:
        symbol = crypto_live.resolve(mt5, market)
        if not symbol:
            continue
        bars = trend_live.completed_bars(mt5, symbol, "H1", 100)
        if len(bars) > 24:
            out.append((bars[-1]["close"] / bars[-25]["close"] - 1, market))
    return sorted(out)


def daily_status(mt5, db: sqlite3.Connection, now: datetime, engines: set[str]) -> str | None:
    if now.hour < SEND_FROM_HOUR_UTC:
        return None
    key = f"daily_status:{now.date().isoformat()}"
    if guards.get(db, key):
        return None
    guards.put(db, key, 1)
    open_rows = db.execute("SELECT model_version, market FROM trades WHERE status = 'OPEN'").fetchall()
    since = (now - timedelta(hours=24)).isoformat()
    recent = db.execute("SELECT COUNT(*) FROM trades WHERE ticket IS NOT NULL AND created_at >= ?", [since]).fetchone()[0]
    lines = ["☀️ Kunlik holat: bot ishlayapti",
             f"Balans {mt5.account_info().equity:,.2f} | ochiq pozitsiyalar {len(open_rows)} | "
             f"so'nggi 24 soatda ochilgan {recent}"]
    held = {r["market"] for r in open_rows}
    for name, engine in (("crypto", crypto_live.CRYPTO), ("gold", metals_live.METALS), ("trend", trend_live.COMMODITIES)):
        if name not in engines:
            continue
        try:                                     # one engine's data problem must not cost the whole message
            near = [f"{m} {d:+.1%}" for d, m in _breakout_distances(mt5, engine) if m not in held][:3]
            lines.append(f"[{name}] yorilishgacha: {', '.join(near) if near else 'maʼlumot yoʻq'}")
        except Exception as exc:
            lines.append(f"[{name}] maʼlumot olinmadi ({type(exc).__name__})")
    if "index" in engines:
        try:
            near = [f"{m} RSI {v:.0f}" for v, m in _index_rsi(mt5) if m not in held][:3]
            lines.append(f"[index] signal RSI < 10 da: {', '.join(near) if near else 'trendda indeks yoʻq'}")
        except Exception as exc:
            lines.append(f"[index] maʼlumot olinmadi ({type(exc).__name__})")
    if "rebound" in engines:
        try:
            near = [f"{m} {d:+.1%}" for d, m in _crypto_falls(mt5)][:3]
            lines.append(f"[rebound] 24 soatlik o'zgarish (signal -12% da): {', '.join(near) if near else 'maʼlumot yoʻq'}")
        except Exception as exc:
            lines.append(f"[rebound] maʼlumot olinmadi ({type(exc).__name__})")
    if "fix" in engines:
        days = (fix_live.last_weekday(now.date()) - now.date()).days
        lines.append(f"[fix] oy oxiriga {days} kun" if days > 0 else "[fix] bugun oy oxiri: 15:00-16:03 London")
    return "\n".join(lines)
