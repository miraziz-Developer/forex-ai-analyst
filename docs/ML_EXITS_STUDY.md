# FX ML v2c — take-profit and trailing exits — pre-registration

Written and committed, with its code (`ml_exits.py`), **before** it is run.

The owner asked why the demo bot has no take-profit: a trade can be well in
profit in mid-week and give it all back by the 5-day exit. The question is
answered with data, not by intuition, on the walk-forward trades of the model
that runs on the demo as v2c (v1 features + COT, logistic C = 0.1, 2010-2026,
yearly retraining, label purge, same costs).

Each trade is replayed on daily bars from the entry open to the close of the
5th trading day. Daily bars do not show the order of high and low, so a day
that touches both the stop and the target counts as the stop (pessimistic). A
gap through a level fills at the open.

- **S — reference.** 3 ATR(14) stop, time exit after 5 days: what the demo bot
  actually does.
- **T15 — take-profit 1.5 ATR** plus the S stop.
- **T3 — take-profit 3 ATR** (1:1 with the stop) plus the S stop.
- **TR — trailing.** S stop; after a close at least 1 ATR in profit the stop
  moves to entry; from then on it trails 1.5 ATR behind the best close.

**Decision.** A variant replaces S on the demo only if, over the same trades,
its mean net per trade beats S with paired day-block bootstrap
P(variant - S > 0) >= **0.95** (three trials at once), and it does not make
2010-2017 or 2018+ worse. Otherwise the bot keeps the time exit. The S row also
reports how much the 3 ATR stop itself changes the original study result.

## Result (2026-10-06) — T3 passes; v2c gets a 3 ATR take-profit

| Variant | Hit | Mean net | PF | P(mean>0) | 2010-17 / 2018+ | P(better than S) | Verdict |
|---|---|---|---|---|---|---|---|
| original study (no stop) | 52.4% | +0.045% | 1.08 | 0.90 | -0.001% / +0.146% | — | — |
| S — 3 ATR stop, time exit | 52.3% | +0.045% | 1.08 | 0.91 | +0.009% / +0.123% | — | reference |
| T15 — TP 1.5 ATR | 53.2% | +0.049% | 1.09 | 0.94 | +0.012% / +0.132% | 0.66 | FAIL |
| **T3 — TP 3 ATR** | 52.3% | **+0.054%** | **1.10** | **0.95** | **+0.019% / +0.131%** | **0.97** | **PASS** |
| TR — trailing | 50.9% | +0.041% | 1.08 | 0.89 | +0.006% / +0.118% | 0.18 | FAIL |

- The 3 ATR stop barely changes the original result, as expected for a wide
  emergency stop.
- A take-profit far enough away (3 ATR) helps a little: it banks the rare large
  mid-week spikes that then reverse, and both periods improve. A tight one
  (1.5 ATR) cuts too many winners to be reliable. Trailing makes it worse:
  normal noise stops trades out before the move.
- T3 is adopted for v2c only (new orders carry `tp`). v1 keeps its pure time
  exit so the two forward tests stay comparable. The gain is small (about
  +0.01% per trade); the model is still well below the PF 1.2 graduation bar.
- Trials on this data: 3 more (T15, T3, TR).


## Rerun on corrected data (2026-10-06) — T3 no longer passes; take-profit removed

After the loader fix (docs/ML_STUDY.md, "Data fix") every variant is negative:
S -0.026%, T15 -0.006% (P better than S 0.97), T3 -0.023% (P 0.85), TR -0.034%
(P 0.005). T3 fails the 0.95 bar, so v2c goes back to the time exit only
(`tp_atr` None). T15 beats S but on a model with no edge; it is not adopted,
since an exit rule cannot create an edge the entries do not have.
