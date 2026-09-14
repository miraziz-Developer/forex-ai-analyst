"""Durable, deduplicated operational alerts for the VST execution workflow."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
import os

import storage
from notifier import send_telegram_message

logger = logging.getLogger(__name__)

_CREATE = """CREATE TABLE IF NOT EXISTS execution_incidents (
 id INTEGER PRIMARY KEY AUTOINCREMENT, incident_key TEXT NOT NULL UNIQUE,
 severity TEXT NOT NULL, message TEXT NOT NULL, details_json TEXT NOT NULL,
 first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, occurrences INTEGER NOT NULL DEFAULT 1,
 notified_at TEXT, state TEXT NOT NULL DEFAULT 'OPEN', resolved_at TEXT
);"""


def init_db() -> None:
    storage._execute(_CREATE)
    for migration in (
        "ALTER TABLE execution_incidents ADD COLUMN state TEXT NOT NULL DEFAULT 'OPEN'",
        "ALTER TABLE execution_incidents ADD COLUMN resolved_at TEXT",
    ):
        try:
            storage._execute(migration)
        except RuntimeError as exc:
            if "duplicate column" not in str(exc).lower():
                raise


def report(incident_key: str, message: str, *, severity: str = "WARNING", details: dict | None = None,
           remind_after_minutes: int | None = 60) -> bool:
    """Persist an incident and notify once per reminder window.

    Alert delivery failure does not hide the database incident and never changes
    a trade state. Pass ``None`` to notify only on initial detection or reopening.
    `incident_key` must identify the unsafe condition precisely.
    """
    try:
        now = datetime.now(timezone.utc)
        rows = storage._rows_as_dicts(storage._execute(
            "SELECT notified_at, state FROM execution_incidents WHERE incident_key = ?", [incident_key]))
        reopened = bool(rows and rows[0].get("state") == "RESOLVED")
        notify = not rows or reopened
        if rows and rows[0].get("notified_at") and not reopened and remind_after_minutes is not None:
            try:
                notify = datetime.fromisoformat(rows[0]["notified_at"]) <= now - timedelta(minutes=remind_after_minutes)
            except ValueError:
                notify = True
        payload = json.dumps(details or {}, ensure_ascii=False, sort_keys=True)
        storage._execute("""INSERT INTO execution_incidents
        (incident_key, severity, message, details_json, first_seen_at, last_seen_at, occurrences, notified_at, state, resolved_at)
        VALUES (?, ?, ?, ?, ?, ?, 1, ?, 'OPEN', NULL)
        ON CONFLICT(incident_key) DO UPDATE SET severity=excluded.severity, message=excluded.message,
        details_json=excluded.details_json, last_seen_at=excluded.last_seen_at,
        occurrences=execution_incidents.occurrences + 1,
        state='OPEN', resolved_at=NULL,
        notified_at=CASE WHEN ? THEN excluded.notified_at ELSE execution_incidents.notified_at END""",
            [incident_key, severity, message, payload, now.isoformat(), now.isoformat(),
             now.isoformat() if notify else None, int(notify)])
        if not notify:
            return False
        token, chat_id = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(), os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        if token and chat_id:
            send_telegram_message(f"⚠️ VST {severity}: {message}", token, chat_id)
        else:
            logger.warning("VST %s: %s", severity, message)
        return True
    except Exception:
        logger.exception("Failed to persist VST incident %s", incident_key)
        return False


def resolve(incident_key: str, *, note: str | None = None, notify: bool = False) -> bool:
    """Close an incident when its independently observed unsafe condition clears."""
    try:
        now = datetime.now(timezone.utc).isoformat()
        result = storage._execute("""UPDATE execution_incidents SET state = 'RESOLVED', resolved_at = ?,
            details_json = ? WHERE incident_key = ? AND state = 'OPEN'""",
            [now, json.dumps({"resolution": note or "condition cleared"}, ensure_ascii=False, sort_keys=True), incident_key])
        resolved = result.get("affected_row_count", 0) > 0
        if resolved and notify:
            token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
            chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
            if token and chat_id:
                send_telegram_message(f"✅ VST RECOVERED: {note or 'condition cleared'}", token, chat_id)
            else:
                logger.info("VST RECOVERED: %s", note or "condition cleared")
        return resolved
    except Exception:
        logger.exception("Failed to resolve VST incident %s", incident_key)
        return False


def status() -> dict:
    rows = storage._rows_as_dicts(storage._execute("""SELECT count(*) AS open_incidents,
        max(last_seen_at) AS last_incident_at FROM execution_incidents WHERE state = 'OPEN'"""))
    return rows[0] if rows else {"open_incidents": 0, "last_incident_at": None}