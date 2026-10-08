"""Pre-FOMC drift on the MT5 US500 CFD, forward test on the demo (docs/IDEAS_STUDY.md #1: 109 meetings,
+0.10% per event, PF 1.63, P 0.958).

Buy US500 at 14:00 New York on the weekday before a scheduled FOMC statement; sell at 13:55 New York on
statement day, before the 14:00 release. A protective stop 2 daily ATR(14) below the entry (rarely reached in
24 hours) sizes the trade so that it costs FX_FOMC_RISK_PCT (0.5) of equity. Meeting dates are read once a day
from the Fed's calendar page; if that fails, the last good list or the built-in 2026-2027 schedule is used.
Journalled as `pre_fomc_v1`.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from forex_ai_analyst.forex import index_live
from forex_ai_analyst.forex import mt5_guards as guards
from forex_ai_analyst.forex.trend_live import OPEN_ENDED, daily_bars
from forex_ai_analyst.lab.strategies import atr

VERSION, LABEL, MARKET = "pre_fomc_v1", "fomc", "US500"
NEW_YORK = ZoneInfo("America/New_York")
CALENDAR_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
# Fed schedule as published (statement on the second day); used when the page cannot be read
BUILT_IN = ("2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16", "2026-10-28",
            "2026-12-09", "2027-01-27", "2027-03-17", "2027-04-28", "2027-06-09", "2027-07-28", "2027-09-15",
            "2027-10-27", "2027-12-08")
STOP_DAILY_ATR = 2.0


def parse_calendar(page: str) -> list[date]:
    """Statement days (the last day of each two-day meeting); single-day notation votes are skipped."""
    out = []
    for header in re.finditer(r"(\d{4}) FOMC Meetings", page):
        year = int(header.group(1))
        end = page.find("FOMC Meetings", header.end())
        seg = page[header.end(): end if end > 0 else len(page)]
        months = re.findall(r'fomc-meeting__month[^>]*>\s*<strong>([^<]+)</strong>', seg)
        days = re.findall(r'fomc-meeting__date[^>]*>([^<]+)<', seg)
        for month, span in zip(months, days):
            m = re.match(r"\s*(\d+)\s*-\s*(\d+)", span)
            if not m:
                continue                                   # notation vote or other single-day entry
            names = [x.strip().lower()[:3] for x in month.split("/")]
            first_day, last_day = int(m.group(1)), int(m.group(2))
            name = names[-1] if last_day < first_day and len(names) > 1 else names[0]
            if name in MONTHS:
                out.append(date(year, MONTHS.index(name) + 1, last_day))
    return sorted(set(out))


def schedule(db: sqlite3.Connection, today: date) -> list[date]:
    key = f"fomc_schedule_checked:{today.isoformat()}"
    if guards.get(db, key) is None:
        guards.put(db, key, 1)
        try:
            days = parse_calendar(requests.get(CALENDAR_URL, timeout=30, headers={"User-Agent": "Mozilla/5.0"}).text)
            if days:
                guards.put(db, "fomc_schedule", ",".join(d.isoformat() for d in days))
        except requests.RequestException:
            pass
    saved = guards.get(db, "fomc_schedule")
    return [date.fromisoformat(x) for x in (saved.split(",") if saved else BUILT_IN)]


def prev_weekday(d: date) -> date:
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def cycle(mt5, db: sqlite3.Connection, now: datetime, account, paused: bool, bot) -> list[str]:
    local = now.astimezone(NEW_YORK)
    today = local.date()
    row = db.execute("SELECT * FROM trades WHERE model_version = ? AND status = 'OPEN'", [VERSION]).fetchone()
    if row is not None:
        return close_if_due(mt5, db, row, local, bot)
    if not 14 <= local.hour < 15:
        return []
    upcoming = [d for d in schedule(db, today) if d > today and prev_weekday(d) == today]
    if not upcoming:
        return []
    statement = upcoming[0].isoformat()
    if db.execute("SELECT 1 FROM trades WHERE model_version = ? AND decision_day = ?", [VERSION, statement]).fetchone():
        return []
    symbol = index_live.resolve(mt5, MARKET)
    if not symbol:
        return [f"⚠️ [{LABEL}] US500 simvoli topilmadi (FX_SYMBOL_MAP bilan ko'rsating)"]
    bars = daily_bars(mt5, symbol)
    a = atr(bars, 14)[-1] if len(bars) > 20 else None
    if not a:
        return [f"⚠️ [{LABEL}] US500 kunlik ma'lumoti yetarli emas"]
    return enter(mt5, db, now, account, paused, bot, symbol, statement, STOP_DAILY_ATR * a)


def close_if_due(mt5, db, row, local: datetime, bot) -> list[str]:
    positions = mt5.positions_get(ticket=row["ticket"]) or ()
    if not positions:
        db.execute("UPDATE trades SET status = 'CLOSED', note = 'closed by its protective stop' WHERE id = ?",
                   [row["id"]])
        db.commit()
        return [f"⛔ [{LABEL}] US500: himoya stopi yopgan"]
    statement = date.fromisoformat(row["decision_day"])
    if local.date() < statement or (local.date() == statement and (local.hour, local.minute) < (13, 55)):
        return []
    position = positions[0]
    result = bot._send(mt5, row["symbol"], -1, position.volume, None, f"{LABEL} exit", position=position.ticket)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        if guards.market_closed(mt5, result):
            return []
        return [f"❌ [{LABEL}] US500 yopilmadi: {getattr(result, 'comment', mt5.last_error())}"]
    db.execute("UPDATE trades SET status = 'CLOSED', exit_price = ?, profit = ?, note = 'closed before the 14:00 "
               "FOMC release' WHERE id = ?", [result.price, position.profit, row["id"]])
    db.commit()
    return [f"{'🟢' if position.profit > 0 else '🔴'} [{LABEL}] US500 FOMC e'lonidan oldin yopildi: "
            f"{position.profit:+.2f}"]


def _skip(db, base: list, note: str) -> None:
    db.execute("INSERT OR IGNORE INTO trades (model_version, decision_day, market, side, prob, exit_day, "
               "created_at, status, note) VALUES (?, ?, ?, ?, ?, ?, ?, 'SKIPPED', ?)", base + [note[:120]])
    db.commit()


def enter(mt5, db, now, account, paused, bot, symbol, statement, stop_distance) -> list[str]:
    tick = mt5.symbol_info_tick(symbol)
    info = mt5.symbol_info(symbol)
    risk_pct = guards.engine_risk_pct(db, VERSION, guards.env_float("FX_FOMC_RISK_PCT", 0.5))
    volume = bot.volume_for(mt5, symbol, stop_distance, account.equity * risk_pct / 100)
    mpp = info.trade_tick_value / (info.trade_tick_size or info.point)
    volume = guards.stress_volume(MARKET, volume, mpp, tick.ask, account.equity, info.volume_step,
                                  info.volume_min) if volume > 0 else 0.0
    risk = bot.risk_of(mt5, symbol, volume, stop_distance) if volume > 0 else 0.0
    stress = guards.stress_loss(MARKET, volume, mpp, tick.ask)
    base = [VERSION, statement, MARKET, 1, 0.0, OPEN_ENDED, now.isoformat()]
    blocked = ("below minimum volume" if volume <= 0 else "drawdown pause" if paused else
               "stress limit" if not guards.stress_room(db, account.equity, stress) else
               "portfolio risk limit" if not guards.risk_room(db, account.equity, risk) else
               "spread too wide" if not guards.spread_ok(tick.ask, tick.bid, stop_distance) else None)
    if blocked:
        if blocked != "spread too wide":                   # a wide spread is retried within the hour
            _skip(db, base, blocked)
        return [f"⏸ [{LABEL}] US500 FOMC oldi signali: {blocked}"]
    stop = tick.ask - stop_distance
    result = bot._send(mt5, symbol, 1, volume, stop, LABEL)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        if guards.market_closed(mt5, result):
            return []
        why = getattr(result, "comment", None) or str(mt5.last_error())
        _skip(db, base, f"order rejected: {why}")
        return [f"❌ [{LABEL}] US500: order rad etildi ({why})"]
    db.execute("INSERT OR IGNORE INTO trades (model_version, decision_day, market, symbol, side, prob, ticket, "
               "volume, entry_price, stop, exit_day, created_at, status, risk_money, requested_price, slippage, "
               "stress_money) VALUES (?, ?, ?, ?, 1, 0.0, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?)",
               [VERSION, statement, MARKET, symbol, result.order, volume, result.price, stop, OPEN_ENDED,
                now.isoformat(), risk, tick.ask, result.price - tick.ask, stress])
    db.commit()
    return [f"📌 [{LABEL}] US500 BUY {volume} lot @ {result.price} (ertaga {statement} FOMC; e'lon oldidan "
            f"13:55 NY da yopiladi, himoya stopi {stop:.5g})"]
