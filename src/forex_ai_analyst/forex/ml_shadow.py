"""Forward (paper) test of the FX logistic model — no orders are ever sent.

Daily job (00:45 UTC):
1. resolve: every open paper trade whose 5th trading day after the decision is
   complete gets entry = next day's open and exit = 5th day's close, exactly as
   in the walk-forward study, net of the same cost;
2. decide: when the latest complete daily bar is a Monday and no decision exists
   for it, score every market and record the confident ones.
Results go to Turso and Telegram.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from forex_ai_analyst.forex import ml_model
from forex_ai_analyst.forex.ml_features import feature_row, load_context
from forex_ai_analyst.forex.regime_system_study import FX_ONLY
from forex_ai_analyst.shared import turso as storage
from forex_ai_analyst.shared.notifier import send_telegram_message

logger = logging.getLogger(__name__)

_CREATE = """CREATE TABLE IF NOT EXISTS fx_ml_shadow (
 id INTEGER PRIMARY KEY AUTOINCREMENT, model_version TEXT NOT NULL, decision_day TEXT NOT NULL,
 market TEXT NOT NULL, side INTEGER NOT NULL, prob REAL NOT NULL, entry_day TEXT, entry_price REAL,
 exit_day TEXT, exit_price REAL, net REAL, status TEXT NOT NULL DEFAULT 'OPEN', created_at TEXT NOT NULL,
 UNIQUE(model_version, decision_day, market))"""


def init_db() -> None:
    storage._execute(_CREATE)
    storage._execute("CREATE INDEX IF NOT EXISTS idx_fx_ml_shadow_status ON fx_ml_shadow (status, decision_day)")


def _complete(days: list[str], today: str) -> int:
    """Index of the last daily bar strictly before today (today's bar may still be forming)."""
    last = len(days) - 1
    while last >= 0 and days[last] >= today:
        last -= 1
    return last


def _notify(text: str) -> None:
    token, chats = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(), os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    for chat in (c.strip() for c in chats.split(",") if c.strip()):
        if token:
            send_telegram_message(text, token, chat)


def stats() -> dict:
    rows = storage._rows_as_dicts(storage._execute(
        "SELECT net FROM fx_ml_shadow WHERE status = 'CLOSED' ORDER BY decision_day"))
    net = [float(r["net"]) for r in rows]
    if not net:
        return {"closed": 0}
    gains, losses = sum(x for x in net if x > 0), -sum(x for x in net if x < 0)
    return {"closed": len(net), "hit_rate": sum(x > 0 for x in net) / len(net), "mean_net_pct": sum(net) / len(net) * 100,
            "total_net_pct": sum(net) * 100, "profit_factor": gains / losses if losses else None}


def run(now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    today = now.date().isoformat()
    model = ml_model.load()
    ctx = load_context(FX_ONLY)
    horizon = int(model["horizon_days"])
    resolved, decided = [], []

    for row in storage._rows_as_dicts(storage._execute(
            "SELECT id, market, side, decision_day FROM fx_ml_shadow WHERE status = 'OPEN'")):
        c = ctx.caches[row["market"]]
        if row["decision_day"] not in c.days:
            continue
        i = c.days.index(row["decision_day"])
        if i + horizon >= len(c.days) or c.days[i + horizon] >= today:
            continue
        entry, exit_ = c.bars[i + 1]["open"], c.bars[i + horizon]["close"]
        net = int(row["side"]) * (exit_ / entry - 1) - c.cost
        storage._execute("""UPDATE fx_ml_shadow SET entry_day = ?, entry_price = ?, exit_day = ?, exit_price = ?,
                            net = ?, status = 'CLOSED' WHERE id = ? AND status = 'OPEN'""",
                         [c.days[i + 1], entry, c.days[i + horizon], exit_, net, row["id"]])
        resolved.append((row["market"], int(row["side"]), net))

    reference = ctx.caches[FX_ONLY[0].name]
    last = _complete(reference.days, today)
    decision_day = reference.days[last] if last >= 0 else None
    if decision_day and datetime.fromisoformat(decision_day).weekday() == 0:
        existing = storage._rows_as_dicts(storage._execute(
            "SELECT count(*) AS n FROM fx_ml_shadow WHERE model_version = ? AND decision_day = ?",
            [model["version"], decision_day]))
        if not existing or not existing[0]["n"]:
            for market in FX_ONLY:
                c = ctx.caches[market.name]
                if decision_day not in c.days:
                    continue
                feats = feature_row(ctx, market.name, c.days.index(decision_day))
                if feats is None:
                    continue
                p = ml_model.probability_up(model, feats)
                if p >= model["long_threshold"] or p <= model["short_threshold"]:
                    side = 1 if p >= model["long_threshold"] else -1
                    storage._execute("""INSERT OR IGNORE INTO fx_ml_shadow (model_version, decision_day, market, side,
                                        prob, status, created_at) VALUES (?, ?, ?, ?, ?, 'OPEN', ?)""",
                                     [model["version"], decision_day, market.name, side, p, now.isoformat()])
                    decided.append((market.name, side, p))

    if resolved or decided:
        lines = ["🧪 FX ML (faqat qog'ozda, real order yo'q)"]
        if decided:
            lines.append(f"\nYangi signallar ({decision_day} yopilishi, 5 kun ushlab turiladi):")
            lines += [f"• {m} {'BUY' if s > 0 else 'SELL'} — ehtimol {p:.0%}" for m, s, p in decided]
        if resolved:
            lines.append("\nYopilgan natijalar:")
            lines += [f"{'🟢' if n > 0 else '🔴'} {m} {'BUY' if s > 0 else 'SELL'}: {n * 100:+.2f}%" for m, s, n in resolved]
        s = stats()
        if s["closed"]:
            pf = f"{s['profit_factor']:.2f}" if s["profit_factor"] else "—"
            lines.append(f"\nJami: {s['closed']} ta | aniqlik {s['hit_rate']:.0%} | o'rtacha {s['mean_net_pct']:+.3f}% | "
                         f"PF {pf} | jami {s['total_net_pct']:+.2f}%")
        _notify("\n".join(lines))
    logger.info("FX ML shadow: %s resolved, %s decided (decision day %s)", len(resolved), len(decided), decision_day)
    return {"resolved": resolved, "decided": decided, "decision_day": decision_day}
