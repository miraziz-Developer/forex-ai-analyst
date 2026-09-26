# Edge lab report

Append-only log of pre-registered hypothesis results. The authoritative record
is `research_output/edge_lab/manifests/ledger.jsonl`; this file is a readable
summary. Verdicts are never edited, only superseded by new versions.

## crowding_exhaustion_v2 — event study (2026-09-26)

Discovery 2021-12-01..2024-01-01, 10 USDT perpetuals, 16 configurations per
direction, primary horizon 4h, gate: >= 100 events, bootstrap P(median > 0)
>= 0.90, effect above 0.14% round-trip cost, >= 3 supportive pairs,
leave-one-pair-out stable.

| Direction | Events per configuration | Verdict |
|---|---|---|
| SHORT | 0-4 | INSUFFICIENT_EVIDENCE |
| LONG | 0 in every configuration | INSUFFICIENT_EVIDENCE |

No configuration came close to the 100-event minimum, so nothing about
direction or size can be concluded; the handful of SHORT events are
anecdotes.

**Flaw found after the run.** Feature v1 ranked percentiles as "share of the
window <= current". Funding stays at the same baseline rate for long periods,
so baseline funding scored near 100 (the SHORT "crowded longs" condition did
not test crowding) and a low tail was nearly unreachable (hence zero LONG
events). The v2 verdict stands as recorded and its 32 trials count toward
multiple-testing corrections. `crowding_exhaustion_v3` changes only the tie
rule (mid-rank); thresholds, grid, periods, costs and gates are identical.
It was registered and committed before being run.

## crowding_exhaustion_v3 — event study (2026-09-26)

Same design as v2 with mid-rank percentiles (feature v2).

| Direction | Events per configuration | Verdict |
|---|---|---|
| SHORT | 0-1 | INSUFFICIENT_EVIDENCE |
| LONG | 0 | INSUFFICIENT_EVIDENCE |

**Why so few events.** A condition-by-condition count on ETH (no returns
examined) showed the setup is internally contradictory: `up/down_impact` is
ranked among all bars, and on bars moving the other way it is negative, so any
bar making a new high (low) almost always ranks above the 50th percentile that
the setup requires it to stay below. "New extreme" and "low impact" together
occurred on 0.00% of bars.

**Power check before any v4 (event counts only, returns not examined).**
Ranking impact only among same-direction moves fixes the contradiction, but the
most frequent configuration then yields about 10 SHORT and 2 LONG events on
BTC/ETH/SOL over two years — roughly 30 across all ten markets, far below the
100-event gate. Five to six simultaneous extreme conditions plus a 24h cooldown
are too rare for the available history (open-interest data starts 2021-12).

**Decision: the crowding_exhaustion family is closed as untestable at this
resolution.** No v4 is registered; loosening thresholds until events appear
would be the forking-paths search the lab exists to prevent. 64 event-study
trials (v2 + v3) are on the ledger.
