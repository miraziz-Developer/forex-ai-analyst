"""Turso persistence for the single paper-only multi-strategy service."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os

import storage
from scalping_core import CandidateSignal, CandidateStatus, Decision

_CREATE_CANDIDATES = """
CREATE TABLE IF NOT EXISTS signal_candidates (
 id INTEGER PRIMARY KEY AUTOINCREMENT, fingerprint TEXT NOT NULL UNIQUE, strategy TEXT NOT NULL,
 pair TEXT NOT NULL, direction TEXT NOT NULL, regime TEXT NOT NULL, candle_time INTEGER NOT NULL,
 entry_price REAL NOT NULL, stop_price REAL NOT NULL, target_price REAL NOT NULL, expiry_time TEXT NOT NULL,
  score INTEGER NOT NULL, confirmations_json TEXT NOT NULL, features_json TEXT NOT NULL,
  status TEXT NOT NULL, rejection_reason TEXT, risk_usdt REAL, quantity REAL,
  outcome_price REAL, outcome_time TEXT, created_at TEXT NOT NULL
);"""
_CREATE_RISK = """
CREATE TABLE IF NOT EXISTS daily_risk_state (
 trade_date TEXT PRIMARY KEY, trade_count INTEGER NOT NULL DEFAULT 0,
 realized_pnl_usdt REAL NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
);"""


def init_db() -> None:
    storage._execute(_CREATE_CANDIDATES)
    storage._execute(_CREATE_RISK)
    for migration in (
        "ALTER TABLE signal_candidates ADD COLUMN risk_usdt REAL",
        "ALTER TABLE signal_candidates ADD COLUMN quantity REAL",
        "ALTER TABLE signal_candidates ADD COLUMN outcome_price REAL",
        "ALTER TABLE signal_candidates ADD COLUMN outcome_time TEXT",
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


def existing_fingerprints() -> set[str]:
    result = storage._execute("SELECT fingerprint FROM signal_candidates")
    return {row["fingerprint"] for row in storage._rows_as_dicts(result)}


def risk_state() -> tuple[int, float]:
    day = datetime.now(timezone.utc).date().isoformat()
    result = storage._execute("SELECT trade_count, realized_pnl_usdt FROM daily_risk_state WHERE trade_date = ?", [day])
    rows = storage._rows_as_dicts(result)
    return (int(rows[0]["trade_count"]), float(rows[0]["realized_pnl_usdt"])) if rows else (0, 0.0)


def mark_accepted(decision: Decision, risk_usdt: float, quantity: float) -> None:
    """Persist one idempotent accepted paper position and reserve its daily risk slot."""
    candidate = decision.candidate
    day, now = datetime.now(timezone.utc).date().isoformat(), datetime.now(timezone.utc).isoformat()
    result = storage._execute(
        """INSERT OR IGNORE INTO signal_candidates (fingerprint, strategy, pair, direction, regime, candle_time,
           entry_price, stop_price, target_price, expiry_time, score, confirmations_json, features_json,
           status, rejection_reason, risk_usdt, quantity, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [candidate.fingerprint, candidate.strategy, candidate.pair, candidate.direction, candidate.regime,
         candidate.candle_time_ms, candidate.entry_price, candidate.stop_price, candidate.target_price,
         candidate.expires_at.isoformat(), candidate.score, json.dumps(candidate.confirmations),
         json.dumps(candidate.features, sort_keys=True), decision.status, decision.reason, risk_usdt, quantity, now])
    if result.get("affected_row_count", 0) == 0:
        return
    storage._execute("""INSERT INTO daily_risk_state (trade_date, trade_count, realized_pnl_usdt, updated_at)
                      VALUES (?, 1, 0, ?) ON CONFLICT(trade_date) DO UPDATE SET
                      trade_count = trade_count + 1, updated_at = excluded.updated_at""", [day, now])


def open_paper_positions() -> int:
    result = storage._execute("SELECT count(*) AS n FROM signal_candidates WHERE status = 'ACCEPTED_PAPER'")
    return int(storage._rows_as_dicts(result)[0]["n"])


def open_paper_signals() -> list[dict]:
    result = storage._execute("""SELECT fingerprint, pair, direction, entry_price, target_price, stop_price,
                               expiry_time, risk_usdt, quantity, candle_time
                               FROM signal_candidates WHERE status = 'ACCEPTED_PAPER'""")
    return storage._rows_as_dicts(result)


def resolve_paper_signal(fingerprint: str, status: CandidateStatus, exit_price: float) -> None:
    """Close exactly one open paper position and account its realized, risk-sized P&L once."""
    if status not in {CandidateStatus.WIN, CandidateStatus.LOSS, CandidateStatus.EXPIRED}:
        raise ValueError("paper signal requires a terminal status")
    result = storage._execute("""SELECT direction, entry_price, quantity, expiry_time FROM signal_candidates
                               WHERE fingerprint = ? AND status = 'ACCEPTED_PAPER'""", [fingerprint])
    rows = storage._rows_as_dicts(result)
    if not rows:
        return
    signal, now = rows[0], datetime.now(timezone.utc)
    direction_multiplier = 1 if signal["direction"] == "BUY" else -1
    pnl = (float(exit_price) - float(signal["entry_price"])) * float(signal["quantity"]) * direction_multiplier
    update = storage._execute("""UPDATE signal_candidates SET status = ?, outcome_price = ?, outcome_time = ?
                               WHERE fingerprint = ? AND status = 'ACCEPTED_PAPER'""",
                              [status, exit_price, now.isoformat(), fingerprint])
    if update.get("affected_row_count", 0) == 0:
        return
    day = now.date().isoformat()
    storage._execute("""INSERT INTO daily_risk_state (trade_date, trade_count, realized_pnl_usdt, updated_at)
                       VALUES (?, 0, ?, ?) ON CONFLICT(trade_date) DO UPDATE SET
                       realized_pnl_usdt = realized_pnl_usdt + excluded.realized_pnl_usdt,
                       updated_at = excluded.updated_at""", [day, pnl, now.isoformat()])


def recent_candidates(limit: int = 100) -> list[dict]:
    safe_limit = min(max(int(limit), 1), 250)
    result = storage._execute("""SELECT fingerprint, strategy, pair, direction, regime, entry_price, stop_price,
                               target_price, score, status, rejection_reason, risk_usdt, quantity,
                               outcome_price, outcome_time, created_at FROM signal_candidates
                               ORDER BY created_at DESC LIMIT ?""", [safe_limit])
    return storage._rows_as_dicts(result)