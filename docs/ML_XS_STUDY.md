# FX ML v3 — cross-sectional currency ranking — pre-registration

Written and committed, with its code (`src/forex_ai_analyst/forex/ml_xs.py`),
**before** it is run.

**Why.** v1/v2 ask, pair by pair, "will this pair rise over 5 days?". Most of a
pair's weekly move is the common dollar move, which is close to unpredictable,
so the signal drowns in it (after the data fix neither model has an edge).
Currency funds instead rank currencies **against each other** and hold the
strongest against the weakest; the common dollar move cancels. v3 is the same
weekly ML system rebuilt that way.

**Universe.** Eight currencies: USD, EUR, GBP, JPY, AUD, NZD, CAD, CHF, built from
the seven USD pairs (corrected Yahoo daily loader, 2004-2026). Each currency's
log value against the equal-weight basket of all eight is its "relative price".

**Features** (decision at the Monday close; everything known by then;
each feature z-scored across the eight currencies on that day):
- relative momentum over 5, 20, 60, 120 and 250 trading days;
- distance of the relative price from its 200-day average, in units of
  20-day volatility;
- 20-day volatility of the relative price;
- carry: 3-month rate minus the average of the eight (previous month's rates);
- CFTC speculative positioning: net / open interest, its 52-week percentile and
  its 4-week change, reports at least 6 days old (USD: minus the average of the
  others).

**Target.** The currency's relative return from the next open to the close five
trading days later, divided by its 20-day volatility.

**Models (two trials):** XS1 ridge regression (alpha 10, standardised inputs);
XS2 gradient boosting (depth 3, 200 trees, learning rate 0.05). Walk-forward:
retrained every January on all rows whose target ended before that year,
used for that year only, first test year 2010.

**Portfolio.** Each Monday: long the two currencies with the highest predicted
score, short the two lowest, 0.5 weight each, held from the next open to the
close five trading days later. Every non-USD leg is traded through its USD pair
and pays that pair's round-trip cost (`data.MARKETS`) times its weight; a USD
leg costs nothing. Swap is charged at 1% a year on gross exposure.

**A model passes only if (weekly portfolio returns, 2010-2026):**
1. at least 600 weeks;
2. mean weekly net return > 0 with week-block bootstrap P(mean > 0) >= **0.95**;
3. weekly profit factor >= 1.2;
4. annualised Sharpe >= 0.5;
5. mean > 0 in 2010-2017 and in 2018-2026;
6. at least 5 of the 8 currencies contributing a positive total.

A passing model becomes model family `fx_xs` on the MT5 demo next to v1/v2c
(after an independent re-implementation check). If neither passes, the result
is recorded and v1/v2c continue unchanged.

## Result (2026-10-06) — neither model passes

| Model | Weeks | Hit | Gross / week | Net / week | PF | Sharpe | P(mean>0) | 2010-17 / 2018+ | Ccys + | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| XS1 ridge | 716 | 45% | -0.006% | -0.074% | 0.81 | -0.58 | 0.02 | -0.104% / -0.057% | 4/8 | FAIL |
| XS2 boosting | 716 | 46% | +0.014% | -0.052% | 0.84 | -0.45 | 0.05 | -0.112% / -0.018% | 4/8 | FAIL |

Before costs the ranking earns nothing (within ±0.015% a week); four legs of
spread and swap (about 0.07% a week) make it lose. Positioning, carry and
momentum, combined by a model and with the common dollar move removed, still
do not predict the next week's relative currency returns on this data. v1/v2c
continue unchanged on the demo.
