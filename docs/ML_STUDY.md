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

## Data correction rerun (2026-10-06)

Yahoo stamps daily FX bars at London midnight (23:00 UTC in summer); the loader
had labelled each such bar with the previous calendar day. This shifted dates
(weekday, month boundaries, cross-asset alignment) but never let a rule see
future data. After fixing it (`load_yahoo`, cache key `v2`) every daily FX study
was rerun; hourly data was not affected.

logistic: 2209 trades, hit 52.6%, +0.028%/trade, PF 1.05, P 0.76, halves -0.011% / +0.146%, 8/10 markets; gradient boosting: +0.010%, PF 1.02, P 0.62, 6/10. Verdicts unchanged. The forward-test model is trained on the corrected data.

## Forward (paper) test — pre-registration (2026-10-06)

The logistic model is not proven, so it is followed forward with paper trades
only (`src/forex_ai_analyst/forex/ml_shadow.py`, daily job at 00:45 UTC in the
live service). No order is ever sent.

**Update (2026-10-06):** at the owner's request the forward test runs on an MT5
**demo** account on a Windows VM instead of on Render (`forex/mt5_bot.py`,
`docs/MT5_DEMO_SETUP.md`); same decisions, executed with demo orders, a 3 ATR
emergency stop and 0.5% risk. The Render job and `ml_shadow.py` were removed. The
evaluation criteria below are unchanged and are measured on the demo journal.

- Model `fx_logistic_2026`: trained on every sample whose label ended before
  2026-01-01 (corrected dates), exported to JSON and applied with the same
  feature code as in training (pure Python; identical to scikit-learn to 1e-16).
- Each Monday close: score all ten markets, record P(up) >= 0.55 as BUY and
  <= 0.45 as SELL. Entry = next open, exit = close five trading days later,
  same cost model; results and running totals are sent to Telegram.
- Every January the model is retrained the same way (`python3 -m
  forex_ai_analyst.forex.ml_model <year>`) and committed; nothing else changes.

**Evaluation, fixed now:** after at least 26 weeks and 60 closed paper trades,
the model graduates to an MT5 demo only if mean net > 0 with bootstrap
P(mean > 0) >= 0.90 and profit factor >= 1.2. Otherwise it stays on paper or is
dropped. First signal (decision day 2026-10-05): XAUUSD BUY, P = 0.60.
