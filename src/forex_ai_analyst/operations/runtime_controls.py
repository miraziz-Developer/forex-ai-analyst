"""Persisted, allowlisted runtime controls for the VST-only service.

Telegram may request these controls, but only a separately confirmed action can
apply them.  This module deliberately has no facility for credentials, source
code, endpoints, or live-money execution.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import math
import secrets

from forex_ai_analyst.shared import turso as storage

_CREATE_SETTINGS = """CREATE TABLE IF NOT EXISTS runtime_controls (
 key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at TEXT NOT NULL, updated_by TEXT NOT NULL
);"""
_CREATE_AUDIT = """CREATE TABLE IF NOT EXISTS runtime_control_audit (
 id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT NOT NULL, action TEXT NOT NULL,
 details_json TEXT NOT NULL, created_at TEXT NOT NULL
);"""
_CREATE_PENDING = """CREATE TABLE IF NOT EXISTS runtime_control_pending (
 code TEXT PRIMARY KEY, chat_id TEXT NOT NULL, updates_json TEXT NOT NULL,
 expires_at TEXT NOT NULL, created_at TEXT NOT NULL
);"""

DEFAULTS = {
    "kill_switch": False,
    "demo_execution": None,  # None defers to deployment environment.
    "blocked_pairs": [],
    # These are balance-relative VST safety envelopes, not fixed USDT or
    # leverage limits.  The AI chooses its values within the live envelope.
    "risk_per_trade_pct": 1.0,
    "max_daily_loss_pct": 5.0,
    "max_margin_utilization_pct": 25.0,
}


def init_db() -> None:
    storage._execute(_CREATE_SETTINGS)
    storage._execute(_CREATE_AUDIT)
    storage._execute(_CREATE_PENDING)


def settings() -> dict:
    values = dict(DEFAULTS)
    rows = storage._rows_as_dicts(storage._execute("SELECT key, value_json FROM runtime_controls"))
    for row in rows:
        if row["key"] in values:
            try:
                values[row["key"]] = json.loads(row["value_json"])
            except (TypeError, json.JSONDecodeError):
                continue
    return values


def _resolve_blocked_pairs_delta(delta: dict) -> list[str]:
    """Merge a {'add'|'remove': [pairs]} delta against the live blocked_pairs value.

    Resolving against the current DB value here (not a value computed at
    preview time, up to 10 minutes earlier) means two operators previewing
    concurrent BLOCK/UNBLOCK commands from the same stale snapshot no longer
    silently undo each other on confirm.
    """
    current = {str(item).upper() for item in settings()["blocked_pairs"]}
    current |= {str(pair).upper() for pair in delta.get("add", [])}
    current -= {str(pair).upper() for pair in delta.get("remove", [])}
    return sorted(current)


def apply(chat_id: str, updates: dict) -> dict:
    """Apply pre-validated updates and retain an immutable-enough audit trail."""
    now = datetime.now(timezone.utc).isoformat()
    resolved = dict(updates)
    if isinstance(resolved.get("blocked_pairs"), dict):
        resolved["blocked_pairs"] = _resolve_blocked_pairs_delta(resolved["blocked_pairs"])
    for key, value in resolved.items():
        if key not in DEFAULTS:
            raise ValueError("unsupported runtime control")
        storage._execute("""INSERT INTO runtime_controls (key, value_json, updated_at, updated_by)
                          VALUES (?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json,
                          updated_at = excluded.updated_at, updated_by = excluded.updated_by""",
                         [key, json.dumps(value), now, chat_id])
    storage._execute("INSERT INTO runtime_control_audit (chat_id, action, details_json, created_at) VALUES (?, ?, ?, ?)",
                     [chat_id, "APPLIED", json.dumps(resolved, ensure_ascii=False), now])
    return settings()


def audit(chat_id: str, action: str, details: dict) -> None:
    storage._execute("INSERT INTO runtime_control_audit (chat_id, action, details_json, created_at) VALUES (?, ?, ?, ?)",
                     [chat_id, action, json.dumps(details, ensure_ascii=False), datetime.now(timezone.utc).isoformat()])


def create_pending(chat_id: str, updates: dict) -> tuple[str, datetime]:
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=10)
    code = secrets.token_hex(3).upper()
    storage._execute("DELETE FROM runtime_control_pending WHERE chat_id = ? OR expires_at <= ?", [chat_id, now.isoformat()])
    storage._execute("INSERT INTO runtime_control_pending (code, chat_id, updates_json, expires_at, created_at) VALUES (?, ?, ?, ?, ?)",
                     [code, chat_id, json.dumps(updates, ensure_ascii=False), expires_at.isoformat(), now.isoformat()])
    audit(chat_id, "PREVIEWED", updates)
    return code, expires_at


def confirm(chat_id: str, code: str) -> dict | None:
    rows = storage._rows_as_dicts(storage._execute("SELECT updates_json, expires_at FROM runtime_control_pending WHERE code = ? AND chat_id = ?",
                                                    [code.upper(), chat_id]))
    deleted = storage._execute("DELETE FROM runtime_control_pending WHERE code = ? AND chat_id = ?", [code.upper(), chat_id])
    # Telegram delivers webhooks at-least-once and retries on a slow/non-2xx
    # response, so the same confirmation can arrive twice concurrently. Both
    # requests can pass the SELECT above before either DELETE lands, but the
    # DELETE itself only affects a row once: gate applying on THIS call being
    # the one that actually removed it, so a retried duplicate is a no-op.
    consumed_here = deleted.get("affected_row_count", 0) > 0
    if not rows or not consumed_here or datetime.fromisoformat(rows[0]["expires_at"]) <= datetime.now(timezone.utc):
        audit(chat_id, "CONFIRMATION_REJECTED", {"code": code.upper()})
        return None
    updates = json.loads(rows[0]["updates_json"])
    return apply(chat_id, updates)


def balance_risk_limit(account_state: dict, controls: dict | None = None) -> float | None:
    """Return remaining VST risk budget in USDT from current account equity.

    Daily PnL is strategy-local until broker income can be safely attributed to
    individual strategy orders.  A missing or malformed account snapshot never
    produces a usable budget.
    """
    values = controls or settings()
    try:
        equity = float(account_state["equity_usdt"])
        daily_pnl = float(account_state.get("daily_strategy_pnl_usdt", 0.0))
        trade_pct = float(values["risk_per_trade_pct"])
        daily_loss_pct = float(values["max_daily_loss_pct"])
    except (KeyError, TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in (equity, daily_pnl, trade_pct, daily_loss_pct)):
        return None
    if equity <= 0 or not 0 < trade_pct <= 100 or not 0 < daily_loss_pct <= 100:
        return None
    daily_loss_budget = equity * daily_loss_pct / 100
    remaining_daily_loss = daily_loss_budget + min(daily_pnl, 0.0)
    return max(0.0, min(equity * trade_pct / 100, remaining_daily_loss))


def trade_permitted(pair: str, risk_usdt: float, leverage: int, cooldown_minutes: int) -> str | None:
    """Return a rejection for immutable execution conditions only.

    Trade sizing is account-relative and is normalized in ``app`` using live
    VST equity/margin; fixed USDT, leverage and cooldown limits are retired.
    """
    values = settings()
    if values["kill_switch"]:
        return "runtime kill switch faol"
    if pair.upper() in {str(item).upper() for item in values["blocked_pairs"]}:
        return f"{pair.upper()} runtime bloklangan"
    if not 1 <= int(leverage) <= 125:
        return "AI leverage BingX 1..125x oralig‘idan tashqarida"
    return None