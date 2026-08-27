import logging
import os
from datetime import datetime, timedelta, timezone

import requests

logger = logging.getLogger(__name__)

TURSO_DATABASE_URL = os.environ["TURSO_DATABASE_URL"]  # libsql://<name>.turso.io
TURSO_AUTH_TOKEN = os.environ["TURSO_AUTH_TOKEN"]

_PIPELINE_URL = TURSO_DATABASE_URL.replace("libsql://", "https://") + "/v2/pipeline"

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pair TEXT NOT NULL,
    direction TEXT NOT NULL,
    entry_price REAL NOT NULL,
    target_price REAL NOT NULL,
    stop_price REAL NOT NULL,
    signal_time TEXT NOT NULL,
    analysis_text TEXT NOT NULL,
    outcome TEXT,
    outcome_price REAL,
    outcome_time TEXT
);
"""


def _typed_arg(value):
    if value is None:
        return {"type": "null", "value": None}
    if isinstance(value, bool):
        return {"type": "integer", "value": str(int(value))}
    if isinstance(value, int):
        return {"type": "integer", "value": str(value)}
    if isinstance(value, float):
        return {"type": "float", "value": value}
    return {"type": "text", "value": str(value)}


def _cell_value(cell: dict):
    if cell["type"] == "null":
        return None
    if cell["type"] == "integer":
        return int(cell["value"])
    if cell["type"] == "float":
        return float(cell["value"])
    return cell["value"]


def _execute(sql: str, args: list | None = None) -> dict:
    stmt = {"sql": sql}
    if args:
        stmt["args"] = [_typed_arg(a) for a in args]

    response = requests.post(
        _PIPELINE_URL,
        headers={"Authorization": f"Bearer {TURSO_AUTH_TOKEN}"},
        json={"requests": [{"type": "execute", "stmt": stmt}, {"type": "close"}]},
        timeout=15,
    )
    response.raise_for_status()
    result = response.json()["results"][0]
    if result["type"] == "error":
        raise RuntimeError(f"Turso query failed: {result.get('error')}")
    return result["response"]["result"]


def _rows_as_dicts(result: dict) -> list[dict]:
    col_names = [c["name"] for c in result["cols"]]
    return [dict(zip(col_names, (_cell_value(cell) for cell in row))) for row in result["rows"]]


def init_db() -> None:
    _execute(_CREATE_TABLE_SQL)
    for migration in (
        "ALTER TABLE signals ADD COLUMN oanda_trade_id TEXT",  # generic broker order id (legacy name)
        "ALTER TABLE signals ADD COLUMN broker_qty REAL",
    ):
        try:
            _execute(migration)
        except RuntimeError as exc:
            if "duplicate column" not in str(exc).lower():
                raise
    logger.info("Database ready (signals table present)")


def log_signal(pair: str, direction: str, entry_price: float, target_price: float,
               stop_price: float, analysis_text: str,
               broker_order_id: str | None = None, broker_qty: float | None = None) -> int:
    signal_time = datetime.now(timezone.utc).isoformat()
    result = _execute(
        """INSERT INTO signals (pair, direction, entry_price, target_price, stop_price,
                                 signal_time, analysis_text, oanda_trade_id, broker_qty)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [pair, direction, entry_price, target_price, stop_price, signal_time, analysis_text,
         broker_order_id, broker_qty],
    )
    return int(result["last_insert_rowid"])


def has_open_signal(pair: str) -> bool:
    """DB-backed (not in-memory) so this survives restarts/redeploys — without
    it, a redeploy mid-setup could re-signal (and re-execute) the same TRADE
    WATCH the in-memory version would have already deduped."""
    result = _execute("SELECT count(*) AS n FROM signals WHERE pair = ? AND outcome IS NULL", [pair])
    return _rows_as_dicts(result)[0]["n"] > 0


def get_open_signals() -> list[dict]:
    result = _execute("SELECT * FROM signals WHERE outcome IS NULL ORDER BY signal_time")
    rows = _rows_as_dicts(result)
    for row in rows:
        row["signal_time"] = datetime.fromisoformat(row["signal_time"])
    return rows


def resolve_signal(signal_id: int, outcome: str, price: float) -> None:
    _execute(
        "UPDATE signals SET outcome = ?, outcome_price = ?, outcome_time = ? WHERE id = ?",
        [outcome, price, datetime.now(timezone.utc).isoformat(), signal_id],
    )


def get_stats() -> dict:
    result = _execute(
        """SELECT outcome, signal_time, direction, entry_price, outcome_price, broker_qty
           FROM signals WHERE outcome IS NOT NULL"""
    )
    rows = _rows_as_dicts(result)
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)

    def _counts(rows: list[dict]) -> dict:
        counts: dict[str, int] = {}
        for row in rows:
            counts[row["outcome"]] = counts.get(row["outcome"], 0) + 1
        return counts

    def _win_rate(counts: dict) -> float | None:
        wins, losses = counts.get("WIN", 0), counts.get("LOSS", 0)
        decided = wins + losses
        return round(wins / decided * 100, 1) if decided else None

    def _realized_pnl_usdt(rows: list[dict]) -> float:
        """Real P&L only, from signals that actually had a BingX order (broker_qty
        set) — analysis-only signals never moved real (demo) money. This is what
        actually answers 'is this profitable', more directly than win rate alone."""
        total = 0.0
        for row in rows:
            if not row.get("broker_qty"):
                continue
            sign = 1 if row["direction"] == "BUY" else -1
            total += (row["outcome_price"] - row["entry_price"]) * row["broker_qty"] * sign
        return round(total, 4)

    all_time_rows = rows
    last_30d_rows = [r for r in rows if datetime.fromisoformat(r["signal_time"]) > cutoff]
    all_time = _counts(all_time_rows)
    last_30d = _counts(last_30d_rows)

    open_result = _execute("SELECT count(*) AS n FROM signals WHERE outcome IS NULL")
    open_count = _rows_as_dicts(open_result)[0]["n"]

    return {
        "open_signals": open_count,
        "all_time": {**all_time, "win_rate_pct": _win_rate(all_time),
                     "realized_pnl_usdt": _realized_pnl_usdt(all_time_rows)},
        "last_30_days": {**last_30d, "win_rate_pct": _win_rate(last_30d),
                          "realized_pnl_usdt": _realized_pnl_usdt(last_30d_rows)},
    }
