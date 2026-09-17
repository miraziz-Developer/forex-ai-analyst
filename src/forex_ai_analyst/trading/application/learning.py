"""Statistically gated outcome summaries; this module cannot alter code or risk limits."""
from __future__ import annotations

from collections import defaultdict

MIN_SAMPLE_SIZE = 20
MIN_EXPECTANCY_USDT = 0.0


def summarize(rows: list[dict], pair: str) -> dict:
    """Give the AI soft evidence only when a comparable sample is sufficiently large."""
    groups: dict[tuple[str, str], list[float]] = defaultdict(list)
    for row in rows:
        if row.get("pair") != pair.upper() or row.get("status") not in {"WIN", "LOSS", "EXPIRED"}:
            continue
        groups[(str(row.get("regime", "UNKNOWN")), str(row.get("direction", "UNKNOWN")))].append(
            float(row.get("realized_pnl_usdt") or 0))
    evidence = []
    for (regime, direction), pnls in sorted(groups.items()):
        sample = len(pnls)
        expectancy = sum(pnls) / sample
        if sample >= MIN_SAMPLE_SIZE:
            evidence.append({"regime": regime, "direction": direction, "sample_size": sample,
                             "expectancy_usdt": round(expectancy, 4),
                             "guidance": "softly supportive" if expectancy > MIN_EXPECTANCY_USDT else "soft caution"})
    return {"policy": "Outcome learning is descriptive only: it cannot change code, leverage, risk caps, or execution permissions.",
            "minimum_sample_size": MIN_SAMPLE_SIZE, "eligible_patterns": evidence,
            "insufficient_sample_patterns": sum(1 for values in groups.values() if len(values) < MIN_SAMPLE_SIZE)}