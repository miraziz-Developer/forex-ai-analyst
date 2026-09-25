"""Turso persistence for the single paper-only multi-strategy service."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os

from forex_ai_analyst.shared import turso as storage
from forex_ai_analyst.trading.domain.models import CandidateSignal, CandidateStatus, Decision

_CREATE_CANDIDATES = """
CREATE TABLE IF NOT EXISTS signal_candidates (
 id INTEGER PRIMARY KEY AUTOINCREMENT, fingerprint TEXT NOT NULL UNIQUE, strategy TEXT NOT NULL,
 pair TEXT NOT NULL, direction TEXT NOT NULL, regime TEXT NOT NULL, candle_time INTEGER NOT NULL,
 entry_price REAL NOT NULL, stop_price REAL NOT NULL, target_price REAL NOT NULL, expiry_time TEXT NOT NULL,
  score INTEGER NOT NULL, confirmations_json TEXT NOT NULL, features_json TEXT NOT NULL,
  status TEXT NOT NULL, rejection_reason TEXT, risk_usdt REAL, quantity REAL,
  outcome_price REAL, outcome_time TEXT, broker_order_id TEXT, broker_fill_price REAL,
  broker_quantity REAL, created_at TEXT NOT NULL
);"""
_CREATE_RISK = """
CREATE TABLE IF NOT EXISTS daily_risk_state (
 trade_date TEXT PRIMARY KEY, trade_count INTEGER NOT NULL DEFAULT 0,
 realized_pnl_usdt REAL NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
);"""
_CREATE_DECISIONS = """
CREATE TABLE IF NOT EXISTS signal_decisions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, fingerprint TEXT NOT NULL, status TEXT NOT NULL,
 reason TEXT, decided_at TEXT NOT NULL
);"""
_CREATE_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS market_snapshots (
 id INTEGER PRIMARY KEY AUTOINCREMENT, pair TEXT NOT NULL, timeframe TEXT NOT NULL,
 candle_time INTEGER NOT NULL, regime TEXT NOT NULL, features_json TEXT NOT NULL,
 created_at TEXT NOT NULL, UNIQUE(pair, timeframe, candle_time)
);"""
_CREATE_AI_REVIEWS = """CREATE TABLE IF NOT EXISTS ai_trade_reviews (
 id INTEGER PRIMARY KEY AUTOINCREMENT, fingerprint TEXT NOT NULL UNIQUE, outcome TEXT NOT NULL,
 realized_pnl_usdt REAL NOT NULL, review TEXT NOT NULL, created_at TEXT NOT NULL
);"""


def init_db() -> None:
    storage._execute(_CREATE_CANDIDATES)
    storage._execute(_CREATE_RISK)
    storage._execute(_CREATE_DECISIONS)
    storage._execute(_CREATE_SNAPSHOTS)
    storage._execute(_CREATE_AI_REVIEWS)
    for migration in (
        "ALTER TABLE signal_candidates ADD COLUMN risk_usdt REAL",
        "ALTER TABLE signal_candidates ADD COLUMN quantity REAL",
        "ALTER TABLE signal_candidates ADD COLUMN outcome_price REAL",
        "ALTER TABLE signal_candidates ADD COLUMN outcome_time TEXT",
        "ALTER TABLE signal_candidates ADD COLUMN broker_order_id TEXT",
        "ALTER TABLE signal_candidates ADD COLUMN broker_fill_price REAL",
        "ALTER TABLE signal_candidates ADD COLUMN broker_quantity REAL",
        "ALTER TABLE signal_candidates ADD COLUMN gross_pnl_usdt REAL",
        "ALTER TABLE signal_candidates ADD COLUMN actual_fees_usdt REAL",
        "ALTER TABLE signal_candidates ADD COLUMN actual_funding_usdt REAL",
        "ALTER TABLE signal_candidates ADD COLUMN actual_net_pnl_usdt REAL",
        "ALTER TABLE signal_candidates ADD COLUMN reconciled_at TEXT",
        "ALTER TABLE signal_candidates ADD COLUMN broker_close_order_id TEXT",
        "ALTER TABLE signal_candidates ADD COLUMN broker_close_fill_price REAL",
         "ALTER TABLE signal_candidates ADD COLUMN broker_close_reason TEXT",
        "ALTER TABLE signal_candidates ADD COLUMN reconciliation_state TEXT NOT NULL DEFAULT 'NOT_REQUIRED'",
        "ALTER TABLE signal_candidates ADD COLUMN reconciliation_note TEXT",
    ):
        try:
            storage._execute(migration)
        except RuntimeError as exc:
            if "duplicate column" not in str(exc).lower():
                raise


def log_decision(decision: Decision) -> None:
    candidate = decision.candidate
    storage._execute(
        """INSERT OR IGNORE INTO signal_candidates (fingerprint, strategy, pair, direction, regime, candle_time,
           entry_price, stop_price, target_price, expiry_time, score, confirmations_json, features_json,
           status, rejection_reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [candidate.fingerprint, candidate.strategy, candidate.pair, candidate.direction, candidate.regime,
         candidate.candle_time_ms, candidate.entry_price, candidate.stop_price, candidate.target_price,
         candidate.expires_at.isoformat(), candidate.score, json.dumps(candidate.confirmations),
         json.dumps(candidate.features, sort_keys=True), decision.status, decision.reason,
         datetime.now(timezone.utc).isoformat()])
    storage._execute("INSERT INTO signal_decisions (fingerprint, status, reason, decided_at) VALUES (?, ?, ?, ?)",
                     [candidate.fingerprint, decision.status, decision.reason, datetime.now(timezone.utc).isoformat()])


def log_market_snapshot(pair: str, timeframe: str, candle_time_ms: int, regime: str, features: dict) -> None:
    """Store one idempotent closed-candle diagnostic snapshot for paper/backtest review."""
    storage._execute("""INSERT OR IGNORE INTO market_snapshots
                      (pair, timeframe, candle_time, regime, features_json, created_at) VALUES (?, ?, ?, ?, ?, ?)""",
                     [pair.upper(), timeframe, candle_time_ms, regime, json.dumps(features, sort_keys=True),
                      datetime.now(timezone.utc).isoformat()])


def existing_fingerprints() -> set[str]:
    result = storage._execute("SELECT fingerprint FROM signal_candidates")
    return {row["fingerprint"] for row in storage._rows_as_dicts(result)}


def risk_state() -> tuple[int, float]:
    day = datetime.now(timezone.utc).date().isoformat()
    result = storage._execute("SELECT trade_count, realized_pnl_usdt FROM daily_risk_state WHERE trade_date = ?", [day])
    rows = storage._rows_as_dicts(result)
    return (int(rows[0]["trade_count"]), float(rows[0]["realized_pnl_usdt"])) if rows else (0, 0.0)


def mark_accepted(decision: Decision, risk_usdt: float, quantity: float, broker_order: dict | None = None) -> None:
    """Persist one idempotent accepted signal and reserve its daily risk slot."""
    candidate = decision.candidate
    day, now = datetime.now(timezone.utc).date().isoformat(), datetime.now(timezone.utc).isoformat()
    result = storage._execute(
        """INSERT OR IGNORE INTO signal_candidates (fingerprint, strategy, pair, direction, regime, candle_time,
           entry_price, stop_price, target_price, expiry_time, score, confirmations_json, features_json,
           status, rejection_reason, risk_usdt, quantity, broker_order_id, broker_fill_price, broker_quantity, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [candidate.fingerprint, candidate.strategy, candidate.pair, candidate.direction, candidate.regime,
         candidate.candle_time_ms, candidate.entry_price, candidate.stop_price, candidate.target_price,
         candidate.expires_at.isoformat(), candidate.score, json.dumps(candidate.confirmations),
         json.dumps(candidate.features, sort_keys=True), decision.status, decision.reason, risk_usdt, quantity,
         (broker_order or {}).get("order_id"), (broker_order or {}).get("fill_price"),
         (broker_order or {}).get("quantity"), now])
    if result.get("affected_row_count", 0) == 0:
        return
    storage._execute("INSERT INTO signal_decisions (fingerprint, status, reason, decided_at) VALUES (?, ?, ?, ?)",
                     [candidate.fingerprint, decision.status, decision.reason, now])
    storage._execute("""INSERT INTO daily_risk_state (trade_date, trade_count, realized_pnl_usdt, updated_at)
                      VALUES (?, 1, 0, ?) ON CONFLICT(trade_date) DO UPDATE SET
                      trade_count = trade_count + 1, updated_at = excluded.updated_at""", [day, now])


def open_paper_positions() -> int:
    result = storage._execute("SELECT count(*) AS n FROM signal_candidates WHERE status = 'ACCEPTED_PAPER'")
    return int(storage._rows_as_dicts(result)[0]["n"])


def open_paper_signals() -> list[dict]:
    result = storage._execute("""SELECT fingerprint, strategy, pair, direction, entry_price, target_price, stop_price,
                               expiry_time, risk_usdt, quantity, broker_order_id, broker_quantity,
                               broker_fill_price, candle_time
                               FROM signal_candidates WHERE status = 'ACCEPTED_PAPER'""")
    return storage._rows_as_dicts(result)


def open_vst_orders() -> list[dict]:
    """Open journal rows backed by immutable BingX VST entry-order IDs."""
    result = storage._execute("""SELECT fingerprint, pair, direction, broker_order_id, broker_quantity,
        broker_fill_price, entry_price, expiry_time, created_at FROM signal_candidates
        WHERE status = 'ACCEPTED_PAPER' AND broker_order_id IS NOT NULL""")
    return storage._rows_as_dicts(result)


def closed_paper_signals(limit: int = 10) -> list[dict]:
    """Newest resolved VST/paper orders with their realized journal P&L."""
    safe_limit = min(max(int(limit), 1), 500)
    result = storage._execute("""SELECT fingerprint, pair, direction, regime, entry_price, broker_fill_price, outcome_price, outcome_time, status,
                                      quantity, broker_order_id, created_at, reconciled_at, gross_pnl_usdt,
                                      actual_fees_usdt, actual_funding_usdt, actual_net_pnl_usdt
                               FROM signal_candidates
                               WHERE status IN ('WIN', 'LOSS', 'TIME_EXIT')
                               ORDER BY outcome_time DESC LIMIT ?""", [safe_limit])
    rows = storage._rows_as_dicts(result)
    for row in rows:
        multiplier = 1 if row["direction"] == "BUY" else -1
        entry = float(row.get("broker_fill_price") or row["entry_price"])
        row["realized_pnl_usdt"] = ((float(row["outcome_price"]) - entry) *
                                    float(row["quantity"]) * multiplier)
    return rows


def performance_summary() -> dict:
    """Return journal-level realized P&L and outcome counts for the button UI."""
    result = storage._execute("""SELECT direction, entry_price, broker_fill_price, outcome_price, quantity, status, outcome_time
                               FROM signal_candidates WHERE status IN ('WIN', 'LOSS', 'TIME_EXIT')""")
    rows = storage._rows_as_dicts(result)
    today = datetime.now(timezone.utc).date().isoformat()
    pnl, today_pnl = 0.0, 0.0
    counts = {"WIN": 0, "LOSS": 0, "TIME_EXIT": 0}
    for row in rows:
        multiplier = 1 if row["direction"] == "BUY" else -1
        entry = float(row.get("broker_fill_price") or row["entry_price"])
        trade_pnl = ((float(row["outcome_price"]) - entry) *
                     float(row["quantity"]) * multiplier)
        pnl += trade_pnl
        counts[row["status"]] += 1
        if str(row["outcome_time"]).startswith(today):
            today_pnl += trade_pnl
    decided = counts["WIN"] + counts["LOSS"]
    return {"closed_orders": len(rows), "wins": counts["WIN"], "losses": counts["LOSS"],
            "time_exits": counts["TIME_EXIT"], "win_rate_pct": round(counts["WIN"] / decided * 100, 1) if decided else None,
            "realized_pnl_usdt": pnl, "today_pnl_usdt": today_pnl}


def first_broker_order_time() -> datetime | None:
    """Return the first strategy-owned VST entry time for a bounded account query."""
    rows = storage._rows_as_dicts(storage._execute("""SELECT min(created_at) AS created_at FROM signal_candidates
                                                       WHERE broker_order_id IS NOT NULL"""))
    value = rows[0].get("created_at") if rows else None
    return datetime.fromisoformat(value) if value else None


def resolve_paper_signal(fingerprint: str, status: CandidateStatus, exit_price: float,
                          broker_close_order: dict | None = None, *, close_reason: str | None = None) -> None:
    """Close exactly one open paper position and account its realized, risk-sized P&L once."""
    if status not in {CandidateStatus.WIN, CandidateStatus.LOSS, CandidateStatus.TIME_EXIT}:
        raise ValueError("paper signal requires a terminal status")
    result = storage._execute("""SELECT direction, entry_price, broker_fill_price, quantity, expiry_time FROM signal_candidates
                               WHERE fingerprint = ? AND status = 'ACCEPTED_PAPER'""", [fingerprint])
    rows = storage._rows_as_dicts(result)
    if not rows:
        return
    signal, now = rows[0], datetime.now(timezone.utc)
    direction_multiplier = 1 if signal["direction"] == "BUY" else -1
    entry = float(signal.get("broker_fill_price") or signal["entry_price"])
    pnl = (float(exit_price) - entry) * float(signal["quantity"]) * direction_multiplier
    update = storage._execute("""UPDATE signal_candidates SET status = ?, outcome_price = ?, outcome_time = ?,
                               broker_close_order_id = ?, broker_close_fill_price = ?,
                                broker_close_reason = ?,
                               reconciliation_state = CASE WHEN broker_order_id IS NOT NULL THEN 'PENDING' ELSE 'NOT_REQUIRED' END
                               WHERE fingerprint = ? AND status = 'ACCEPTED_PAPER'""",
                              [status, exit_price, now.isoformat(), (broker_close_order or {}).get("order_id"),
                                (broker_close_order or {}).get("fill_price"), close_reason, fingerprint])
    if update.get("affected_row_count", 0) == 0:
        return
    day = now.date().isoformat()
    storage._execute("""INSERT INTO daily_risk_state (trade_date, trade_count, realized_pnl_usdt, updated_at)
                       VALUES (?, 0, ?, ?) ON CONFLICT(trade_date) DO UPDATE SET
                       realized_pnl_usdt = realized_pnl_usdt + excluded.realized_pnl_usdt,
                       updated_at = excluded.updated_at""", [day, pnl, now.isoformat()])
    review = ("Trade yakuni: " + str(status) + ". Keyingi qarorda aynan shu natijani yakka o‘zi qoida sifatida "
              "qabul qilma; o‘xshash regime, yo‘nalish va setup namunalarini birgalikda bahola.")
    storage._execute("""INSERT OR IGNORE INTO ai_trade_reviews
                      (fingerprint, outcome, realized_pnl_usdt, review, created_at) VALUES (?, ?, ?, ?, ?)""",
                     [fingerprint, status, pnl, review, now.isoformat()])


def recent_ai_reviews(pair: str, limit: int = 8) -> list[dict]:
    result = storage._execute("""SELECT c.pair, c.direction, c.regime, c.features_json, r.outcome,
                                      r.realized_pnl_usdt, r.review, r.created_at
                               FROM ai_trade_reviews r JOIN signal_candidates c ON c.fingerprint = r.fingerprint
                               WHERE c.pair = ? ORDER BY r.id DESC LIMIT ?""", [pair.upper(), min(max(limit, 1), 20)])
    return storage._rows_as_dicts(result)


def recent_candidates(limit: int = 100) -> list[dict]:
    safe_limit = min(max(int(limit), 1), 250)
    result = storage._execute("""SELECT fingerprint, strategy, pair, direction, regime, entry_price, stop_price,
                               target_price, score, status, rejection_reason, risk_usdt, quantity,
                               outcome_price, outcome_time, created_at FROM signal_candidates
                               ORDER BY created_at DESC LIMIT ?""", [safe_limit])
    return storage._rows_as_dicts(result)


def reconcile_broker_pnl(fingerprint: str, gross_pnl: float, fees: float, funding: float) -> None:
    """Persist exchange-derived values only; estimates must never be passed here."""
    storage._execute("""UPDATE signal_candidates SET gross_pnl_usdt = ?, actual_fees_usdt = ?,
                      actual_funding_usdt = ?, actual_net_pnl_usdt = ?, reconciled_at = ? WHERE fingerprint = ?""",
                     [gross_pnl, fees, funding, gross_pnl - fees + funding,
                      datetime.now(timezone.utc).isoformat(), fingerprint])


def closed_vst_orders_pending_reconciliation() -> list[dict]:
    """Closed VST rows needing exchange order-ID reconciliation."""
    result = storage._execute("""SELECT fingerprint, pair, broker_order_id, broker_close_order_id,
        broker_fill_price, broker_close_fill_price, status, outcome_time FROM signal_candidates
        WHERE broker_order_id IS NOT NULL AND status IN ('WIN', 'LOSS', 'TIME_EXIT')
        AND reconciliation_state = 'PENDING'""")
    return storage._rows_as_dicts(result)


def mark_reconciliation(fingerprint: str, state: str, note: str | None = None, *, gross_pnl: float | None = None,
                        fees: float | None = None, funding: float | None = None) -> None:
    if state not in {"VERIFIED", "PENDING", "UNAVAILABLE"}:
        raise ValueError("invalid reconciliation state")
    net = gross_pnl - fees + funding if None not in (gross_pnl, fees, funding) else None
    storage._execute("""UPDATE signal_candidates SET reconciliation_state = ?, reconciliation_note = ?,
        gross_pnl_usdt = COALESCE(?, gross_pnl_usdt), actual_fees_usdt = COALESCE(?, actual_fees_usdt),
        actual_funding_usdt = COALESCE(?, actual_funding_usdt), actual_net_pnl_usdt = COALESCE(?, actual_net_pnl_usdt),
        reconciled_at = CASE WHEN ? = 'VERIFIED' THEN ? ELSE reconciled_at END WHERE fingerprint = ?""",
        [state, note, gross_pnl, fees, funding, net, state, datetime.now(timezone.utc).isoformat(), fingerprint])


def reconciliation_status() -> dict:
    rows = storage._rows_as_dicts(storage._execute("""SELECT count(*) AS executed,
        sum(CASE WHEN reconciled_at IS NOT NULL THEN 1 ELSE 0 END) AS reconciled,
        sum(actual_net_pnl_usdt) AS actual_net_pnl_usdt FROM signal_candidates
        WHERE broker_order_id IS NOT NULL AND status != 'ACCEPTED_PAPER'"""))
    row = rows[0] if rows else {}
    return {"executed_closed": int(row.get("executed") or 0), "reconciled": int(row.get("reconciled") or 0),
            "actual_net_pnl_usdt": float(row.get("actual_net_pnl_usdt") or 0),
            "policy": "BingX income is account-scoped and is not attributed to individual AI orders; Telegram shows it separately."}