"""Portfolio risk, execution and monitoring guards for the MT5 demo bot (layers 5-7).

All limits come from .env with conservative defaults:
  FX_BOT_MAX_TOTAL_RISK_PCT (5.0)  open risk of all positions together, % of equity
  FX_BOT_MAX_DD_PCT (10.0)         pause new trades when equity is this far below its peak
  FX_BOT_MAX_SPREAD_FRAC (0.10)    skip an entry while the spread exceeds this share of the stop distance
  FX_BOT_NO_AUTO_DISABLE (unset)   set to 1 to keep trading a model that failed its health check
A model is disabled for new trades once it has >= 30 closed trades with a profit factor below 0.7,
far below anything its backtest produced; open positions are still managed and closed.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime

EXTRA_COLUMNS = {"risk_money": "REAL", "requested_price": "REAL", "slippage": "REAL"}
HEALTH_MIN_TRADES, HEALTH_MIN_PF = 30, 0.7


def env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def migrate(db: sqlite3.Connection) -> None:
    have = {r[1] for r in db.execute("PRAGMA table_info(trades)")}
    for column, kind in EXTRA_COLUMNS.items():
        if column not in have:
            db.execute(f"ALTER TABLE trades ADD COLUMN {column} {kind}")
    db.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    db.commit()


def get(db: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = db.execute("SELECT value FROM state WHERE key = ?", [key]).fetchone()
    return row[0] if row else default


def put(db: sqlite3.Connection, key: str, value) -> None:
    db.execute("INSERT INTO state (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
               [key, str(value)])
    db.commit()


def drawdown_pause(db: sqlite3.Connection, equity: float) -> tuple[bool, str | None]:
    """(paused, message on a change). Tracks the equity peak; pauses entries beyond FX_BOT_MAX_DD_PCT."""
    peak = max(float(get(db, "equity_peak", equity)), equity)
    put(db, "equity_peak", peak)
    limit = env_float("FX_BOT_MAX_DD_PCT", 10.0)
    drawdown = (equity / peak - 1) * 100 if peak > 0 else 0.0
    paused = drawdown <= -limit
    was = get(db, "dd_paused", "0") == "1"
    put(db, "dd_paused", int(paused))
    if paused and not was:
        return True, (f"🛑 Hisob cho'qqidan {drawdown:.1f}% pastda (limit -{limit:g}%): yangi trade'lar to'xtatildi, "
                      "ochiq pozitsiyalar odatdagidek yopiladi")
    if was and not paused:
        return False, f"✅ Hisob tiklandi ({drawdown:.1f}% cho'qqidan): yangi trade'lar qayta ochiladi"
    return paused, None


def open_risk(db: sqlite3.Connection) -> float:
    row = db.execute("SELECT COALESCE(SUM(risk_money), 0) FROM trades WHERE status = 'OPEN'").fetchone()
    return float(row[0] or 0.0)


def risk_room(db: sqlite3.Connection, equity: float, new_risk: float) -> bool:
    return open_risk(db) + new_risk <= equity * env_float("FX_BOT_MAX_TOTAL_RISK_PCT", 5.0) / 100 + 1e-9


def spread_ok(ask: float, bid: float, stop_distance: float) -> bool:
    return stop_distance > 0 and (ask - bid) <= env_float("FX_BOT_MAX_SPREAD_FRAC", 0.10) * stop_distance


def model_stats(db: sqlite3.Connection, version: str) -> dict:
    profits = [r[0] for r in db.execute("SELECT profit FROM trades WHERE model_version = ? AND status = 'CLOSED' "
                                        "AND profit IS NOT NULL ORDER BY id", [version])]
    gains, losses = sum(p for p in profits if p > 0), -sum(p for p in profits if p < 0)
    return {"trades": len(profits), "wins": sum(p > 0 for p in profits), "profit": sum(profits),
            "pf": gains / losses if losses else (float("inf") if gains else 0.0)}


def model_disabled(db: sqlite3.Connection, version: str) -> bool:
    if os.environ.get("FX_BOT_NO_AUTO_DISABLE") == "1":
        return False
    s = model_stats(db, version)
    return s["trades"] >= HEALTH_MIN_TRADES and s["pf"] < HEALTH_MIN_PF


def weekly_report(db: sqlite3.Connection, equity: float, now: datetime) -> str | None:
    """Once per ISO week, on the first cycle from Monday on."""
    week = "%d-W%02d" % now.isocalendar()[:2]
    if get(db, "report_week") == week:
        return None
    put(db, "report_week", week)
    first = db.execute("SELECT MIN(decision_day) FROM trades WHERE ticket IS NOT NULL").fetchone()[0]
    weeks = (now.date() - datetime.fromisoformat(first).date()).days // 7 if first else 0
    peak = float(get(db, "equity_peak", equity))
    lines = [f"📊 Haftalik hisobot ({week})", f"Balans {equity:,.2f} | cho'qqidan {(equity / peak - 1) * 100:+.1f}% "
             f"| ochiq risk {open_risk(db):,.2f}", f"Forward test: {weeks} hafta (kerak 26+)"]
    for (version,) in db.execute("SELECT DISTINCT model_version FROM trades ORDER BY model_version"):
        s = model_stats(db, version)
        if s["trades"] == 0:
            lines.append(f"• {version}: hali yopilgan trade yo'q")
            continue
        pf = "∞" if s["pf"] == float("inf") else f"{s['pf']:.2f}"
        slip = db.execute("SELECT AVG(slippage) FROM trades WHERE model_version = ? AND slippage IS NOT NULL",
                          [version]).fetchone()[0]
        lines.append(f"• {version}: {s['trades']} trade (kerak 60+), win {s['wins'] / s['trades']:.0%}, "
                     f"PF {pf} (kerak 1.2+), natija {s['profit']:+,.2f}"
                     + (f", o'rtacha slippage {slip:+.5g}" if slip is not None else "")
                     + (" ⛔ avtomatik o'chirilgan" if model_disabled(db, version) else ""))
    return "\n".join(lines)
