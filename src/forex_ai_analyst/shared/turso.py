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
        "ALTER TABLE signals ADD COLUMN funding_rate_pct REAL",  # BingX funding rate at signal open, for P&L estimate
    ):
        try:
            _execute(migration)
        except RuntimeError as exc:
            if "duplicate column" not in str(exc).lower():
                raise
    logger.info("Database ready (signals table present)")


def log_signal(pair: str, direction: str, entry_price: float, target_price: float,
               stop_price: float, analysis_text: str,
               broker_order_id: str | None = None, broker_qty: float | None = None,
               funding_rate_pct: float | None = None) -> int:
    signal_time = datetime.now(timezone.utc).isoformat()
    result = _execute(
        """INSERT INTO signals (pair, direction, entry_price, target_price, stop_price,
                                 signal_time, analysis_text, oanda_trade_id, broker_qty, funding_rate_pct)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [pair, direction, entry_price, target_price, stop_price, signal_time, analysis_text,
         broker_order_id, broker_qty, funding_rate_pct],
    )
    return int(result["last_insert_rowid"])


def has_open_signal(pair: str) -> bool:
    """DB-backed (not in-memory) so this survives restarts/redeploys — without
    it, a redeploy mid-setup could re-signal (and re-execute) the same TRADE
    WATCH the in-memory version would have already deduped."""
    result = _execute("SELECT count(*) AS n FROM signals WHERE pair = ? AND outcome IS NULL", [pair])
    return _rows_as_dicts(result)[0]["n"] > 0


def get_recent_resolved_signals(pair: str, limit: int = 15) -> list[dict]:
    """Last N resolved signals for this pair, most recent first — fed back into
    the next analysis as soft context (see system_prompt.txt) so the model has
    some awareness of its own recent track record on this pair, not just a
    blank slate every cycle. Deliberately NOT used to hard-code any rule from
    this — the sample per pair is tiny, so this is framed to the model as
    context to weigh, not a pattern to mechanically extrapolate from."""
    result = _execute(
        """SELECT direction, entry_price, target_price, stop_price, outcome, outcome_price,
                  signal_time, analysis_text
           FROM signals WHERE pair = ? AND outcome IS NOT NULL
           ORDER BY signal_time DESC LIMIT ?""",
        [pair, limit],
    )
    return _rows_as_dicts(result)


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


# BingX USDT-perpetual taker fee — market-order entry and TP/SL-triggered exit are
# both taker fills. Actual rate can differ slightly by VIP tier; this is the
# standard/default rate, so realized_pnl_usdt is an estimate, not exchange-exact.
_TAKER_FEE_PCT = 0.05
_FUNDING_INTERVAL_HOURS = 8  # BingX perpetuals settle funding every 8h while a position is open


def estimate_signal_pnl(row: dict) -> float | None:
    """Estimated net P&L for an executed, resolved signal; analysis-only/open
    signals return None. Includes round-trip taker fees and estimated funding."""
    qty = row.get("broker_qty")
    outcome_price = row.get("outcome_price")
    if not qty or outcome_price is None:
        return None

    sign = 1 if row["direction"] == "BUY" else -1
    entry_price = float(row["entry_price"])
    outcome_price = float(outcome_price)
    qty = float(qty)
    price_pnl = (outcome_price - entry_price) * qty * sign
    fee_cost = (entry_price * qty + outcome_price * qty) * (_TAKER_FEE_PCT / 100)

    funding_cost = 0.0
    funding_rate_pct = row.get("funding_rate_pct")
    if funding_rate_pct is not None and row.get("outcome_time") and row.get("signal_time"):
        signal_time = row["signal_time"]
        outcome_time = row["outcome_time"]
        if isinstance(signal_time, str):
            signal_time = datetime.fromisoformat(signal_time)
        if isinstance(outcome_time, str):
            outcome_time = datetime.fromisoformat(outcome_time)
        hours_held = (outcome_time - signal_time).total_seconds() / 3600
        periods = int(hours_held // _FUNDING_INTERVAL_HOURS)
        funding_cost = float(funding_rate_pct) / 100 * entry_price * qty * periods * sign

    return round(price_pnl - fee_cost - funding_cost, 4)


def get_signals(status: str = "ALL", pair: str | None = None,
                direction: str | None = None, limit: int = 100,
                offset: int = 0) -> dict:
    """Filtered, newest-first signal history for the monitoring UI/API."""
    status = status.upper()
    direction = direction.upper() if direction else None
    allowed_statuses = {"ALL", "OPEN", "RESOLVED", "WIN", "LOSS", "EXPIRED"}
    if status not in allowed_statuses:
        raise ValueError(f"Unsupported status: {status}")
    if direction not in {None, "BUY", "SELL"}:
        raise ValueError(f"Unsupported direction: {direction}")

    conditions, args = [], []
    if status == "OPEN":
        conditions.append("outcome IS NULL")
    elif status == "RESOLVED":
        conditions.append("outcome IS NOT NULL")
    elif status != "ALL":
        conditions.append("outcome = ?")
        args.append(status)
    if pair:
        conditions.append("pair = ?")
        args.append(pair.upper())
    if direction:
        conditions.append("direction = ?")
        args.append(direction)

    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    count_result = _execute(f"SELECT count(*) AS n FROM signals{where}", args)
    total = _rows_as_dicts(count_result)[0]["n"]
    safe_limit = min(max(int(limit), 1), 250)
    safe_offset = max(int(offset), 0)
    result = _execute(
        "SELECT id, pair, direction, entry_price, target_price, stop_price, "
        "signal_time, analysis_text, outcome, outcome_price, outcome_time, "
        f"oanda_trade_id, broker_qty, funding_rate_pct FROM signals{where} "
        "ORDER BY signal_time DESC LIMIT ? OFFSET ?",
        [*args, safe_limit, safe_offset],
    )
    rows = _rows_as_dicts(result)
    for row in rows:
        row["status"] = row.get("outcome") or "OPEN"
        row["executed"] = bool(row.get("broker_qty"))
        row["estimated_pnl_usdt"] = estimate_signal_pnl(row)
    return {"signals": rows, "total": total, "limit": safe_limit, "offset": safe_offset}


def get_stats() -> dict:
    result = _execute(
        """SELECT outcome, signal_time, outcome_time, direction, entry_price, outcome_price,
                  broker_qty, funding_rate_pct
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
        actually answers 'is this profitable', more directly than win rate alone.
        Nets out the round-trip taker fee and an estimated funding cost (using the
        funding rate captured at signal-open, held constant for the trade's
        duration — funding actually re-settles/re-prices every 8h, so this is a
        reasonable estimate, not an exact replay of what BingX charged)."""
        return round(sum(pnl for row in rows if (pnl := estimate_signal_pnl(row)) is not None), 4)

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
