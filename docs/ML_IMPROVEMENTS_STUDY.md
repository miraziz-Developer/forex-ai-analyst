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

## Result

(pending)
