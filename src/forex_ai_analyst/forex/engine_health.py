"""Per-engine heartbeat for the MT5 demo bot, so no engine can stop working without anyone noticing.

Every engine run is recorded (last success, last error). An enabled engine that has not completed a run for
longer than its own rhythm allows is reported to Telegram (at most every six hours per engine), the daily
status lists each engine's state, and a lost MT5 connection is reported once and retried instead of
restarting the bot every minute.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from forex_ai_analyst.forex import mt5_guards as guards

FAST = {"fix", "rebound", "fomc"}              # run every minute; the others every 15 minutes
STALE_MINUTES = {"fast": 10, "slow": 45}
ALERT_EVERY = timedelta(hours=6)


def record_ok(db: sqlite3.Connection, name: str, now: datetime) -> None:
    guards.put(db, f"engine_ok:{name}", now.isoformat())


def record_error(db: sqlite3.Connection, name: str, now: datetime, exc: BaseException) -> None:
    guards.put(db, f"engine_err:{name}", f"{now.isoformat()}|{type(exc).__name__}: {exc}"[:300])


def _limit(name: str) -> timedelta:
    return timedelta(minutes=STALE_MINUTES["fast" if name in FAST else "slow"])


def _ago(now: datetime, then: datetime) -> str:
    minutes = int((now - then).total_seconds() // 60)
    return f"{minutes} daq oldin" if minutes < 120 else f"{minutes // 60} soat oldin"


def stalled(db: sqlite3.Connection, engines: list[str], now: datetime, started: datetime) -> list[str]:
    """Alerts for engines that have not completed a run within their limit (once the bot has run that long)."""
    messages = []
    for name in engines:
        limit = _limit(name)
        if now - started < limit:
            continue
        ok = guards.get(db, f"engine_ok:{name}")
        last_ok = datetime.fromisoformat(ok) if ok else None
        if last_ok is not None and now - last_ok <= limit:
            continue
        alerted = guards.get(db, f"stall_alert:{name}")
        if alerted and now - datetime.fromisoformat(alerted) < ALERT_EVERY:
            continue
        guards.put(db, f"stall_alert:{name}", now.isoformat())
        err = guards.get(db, f"engine_err:{name}")
        why = f"; oxirgi xato: {err.split('|', 1)[1]}" if err else ""
        since = f"oxirgi muvaffaqiyat {_ago(now, last_ok)}" if last_ok else "hali bir marta ham muvaffaqiyatli ishlamadi"
        messages.append(f"🚨 [{name}] dvigatel ishlamayapti: {since}{why}")
    return messages


def summary(db: sqlite3.Connection, engines: list[str], now: datetime) -> list[str]:
    lines = []
    for name in engines:
        ok, err = guards.get(db, f"engine_ok:{name}"), guards.get(db, f"engine_err:{name}")
        last_ok = datetime.fromisoformat(ok) if ok else None
        last_err = datetime.fromisoformat(err.split("|", 1)[0]) if err else None
        if last_ok is not None and now - last_ok <= _limit(name) and (last_err is None or last_err < last_ok):
            lines.append(f"✅ {name}: ishlayapti ({_ago(now, last_ok)})")
        elif last_err is not None and (last_ok is None or last_err >= last_ok):
            lines.append(f"❌ {name}: xato {_ago(now, last_err)} — {err.split('|', 1)[1]}")
        else:
            lines.append(f"⚠️ {name}: {'oxirgi ish ' + _ago(now, last_ok) if last_ok else 'hali ishlamagan'}")
    return lines


def connection_lost(mt5) -> bool:
    """True when the terminal has no account or no broker connection (the bot waits and reconnects)."""
    if mt5.account_info() is None:
        return True
    info = getattr(mt5, "terminal_info", lambda: None)()
    return info is not None and getattr(info, "connected", True) is False
