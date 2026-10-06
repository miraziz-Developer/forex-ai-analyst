# Walk-forward machine-learning FX study — pre-registration

Written and committed, with its code (`src/forex_ai_analyst/forex/ml_study.py`)
and a purge test, **before** the study is run. Requires `pip install .[research]`.

**Idea (owner's request).** One "universal" model that learns from 22 years of
data and keeps adapting: it is retrained every January on everything known
before that January and trades that year only, 2010-2026. This is how an
adaptive model would actually have performed live.

**Samples.** Every 5th trading day per market (9 currency pairs + gold, Yahoo
daily). Label: did the market rise from the next day's open to the close 5
trading days later.

**Features (known at the decision close):** log returns over 1/5/20/60/120 days,
20-day volatility, ATR(14)/ATR(100), ADX, efficiency ratio, RSI(14), RSI(2),
Bollinger z-score, distance to EMA20/50/200 in ATR, SMC liquidity sweep
(low/high), ICT fair value gap revisited (bull/bear), distance to the last
confirmed swing high/low, weekday, month end within 5 business days (calendar),
interest-rate differential (OECD, previous month), S&P 500 20-day return and
VIX level and 5-day change (one-day lag), 20-day USD momentum across the
USD pairs, and a market identifier.

**Models (fixed settings, two trials):** logistic regression (standardised,
C = 1) and gradient boosting (depth 3, learning rate 0.05, 200 iterations).
Training excludes samples whose 5-day label ends on or after the test year
starts.

**Trading rule ("sniper"):** long if P(up) >= 0.55, short if <= 0.45, else no
trade; enter at the next open, exit 5 trading days later; per-market round-trip
cost as in the forex study.

**A model passes only if, over 2010-2026 out of sample:** at least 200 trades;
bootstrap P(mean net > 0) >= 0.90; profit factor >= 1.2; positive in both
2010-2017 and 2018-2026; at least 60% of markets positive. A passing model is
eligible for an MT5 demo trader that retrains the same way.

## Result (2026-10-06) — neither model passes

10,852 samples, 38 features, 51.7% of 5-day windows were up.

| Model | Trades | Per year | Hit rate | Mean net / trade | PF | P(mean>0) | 2010-17 / 2018+ | Markets + | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| logistic | 2123 | 126 | 52.2% | +0.028% | 1.05 | 0.75 | -0.019% / +0.159% | 9/10 | FAIL |
| gradient boosting | 2991 | 178 | 51.3% | -0.015% | 0.97 | 0.30 | -0.030% / +0.011% | 4/10 | FAIL |

- The adaptive logistic model is the best result of all the forex work: small
  positive mean, nine of ten markets positive, clearly positive after 2018. It
  still fails the pre-registered bar (P 0.75, PF 1.05, negative 2010-2017), so
  it is not a proven edge: a 52% hit rate on 5-day moves is close to a coin
  flip after costs.
- The more flexible gradient boosting model did worse out of sample: more
  capacity fitted more noise.

**Decision.** No MT5 trader is built. The logistic model's 2018+ behaviour is a
candidate for a forward test (paper only), not for money.

A deprecation-only change (`np.timedelta64(1, "D")` instead of `+ 1` in the
calendar feature) was made after the run; it does not change any value.
