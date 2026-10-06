# FX ML v2 — three declared improvements — pre-registration

Written and committed, with its code (`ml_study.py --improve`), **before** it is run.

v2 (v1 + COT) reached P 0.86 and PF 1.07. Three improvements with an economic or
statistical reason are tested together, once, on the same walk-forward
(2010-2026, yearly retraining, label purge, same costs):

- **A — 20-day horizon.** Carry, trend and positioning act over weeks; label =
  direction from the next open to the close 20 trading days later, one decision
  every 20 trading days per market.
- **B — confidence and volatility sizing.** Same v2 signals; each trade weighted
  by |P(up) - 0.5| / 20-day volatility, so stronger and calmer signals carry
  more weight.
- **C — stronger regularisation.** Logistic C = 0.1 instead of 1.0 to fit less
  noise.

**Gates (stricter because three trials run at once):** the v1/v2 gates with
bootstrap P(mean > 0) >= **0.95** instead of 0.90: at least 200 trades, profit
factor >= 1.2, positive in 2010-2017 and in 2018+, at least 60% of markets
positive. A passing variant becomes a candidate for the MT5 demo under its own
model version; if none passes, v1 continues alone.

Trials so far on this data: forex rules 7, CFD trend 2, FX factors 4, forced
flows 3, regime system 4, development 144, ML 2 + 1 (COT) + 3 (these).

## Result (2026-10-06) — no variant passes; v1 continues alone

| Variant | Trades | Hit | Mean net | PF | P(mean>0) | 2010-17 / 2018+ | Markets + | Verdict |
|---|---|---|---|---|---|---|---|---|
| v2 reference | 2528 | 52.1% | +0.038% | 1.07 | 0.86 | -0.010% / +0.140% | 8/10 | — |
| A 20-day horizon | 778 | 49.5% | +0.077% | 1.08 | 0.76 | -0.145% / +0.229% | 5/10 | FAIL |
| B sizing | 2528 | 52.1% | (weighted) | 1.04 | 0.72 | negative / positive | 6/10 | FAIL |
| **C regularised** | 2421 | 52.4% | **+0.045%** | **1.08** | **0.90** | **-0.001% / +0.146%** | 7/10 | FAIL |

- C is the best FX ML result so far: 2010-2017 is now about break-even and
  P reaches 0.90, but it misses the stricter 0.95 bar (three trials at once) and
  the profit-factor bar of 1.2.
- B: weighting by confidence did not help; the model's confidence is not well
  calibrated to the size of the move.
- A: a longer horizon makes the edge larger per trade but less consistent
  (2010-2017 clearly negative, half the markets negative).

The v2 reference was re-run before the variants and reproduced exactly.

## Deviation, recorded openly (2026-10-06)

At the owner's request, variant C (`fx_logistic_cot_c01`: v2 + COT, C = 0.1)
also runs on the MT5 **demo**, next to v1, although it did not pass its gate.
This is a forward comparison only: both models are judged by the same forward
criteria in `docs/ML_STUDY.md`, each on its own journal rows (model version),
and neither may trade real money on the strength of this study.


## Rerun on corrected data (2026-10-06)

After the loader fix (docs/ML_STUDY.md, "Data fix"): A -0.263% (P 0.00), B PF 0.91
(P 0.06), C -0.015% (PF 0.97, P 0.33). All still fail; C's earlier P 0.90 was
an artefact of dropped bars.
