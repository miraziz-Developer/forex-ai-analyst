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

**Data fix (2026-10-06) — the edge does not survive it.** Yahoo's EURJPY bar for
Monday 2026-10-05 had its close 0.013 above its high; the loader dropped such
bars, so the bot never evaluated EURJPY that week. A count showed the loader had
dropped **2027 daily bars (3.5%)**: on the currency pairs the close was only
about 2% of the bar's range outside it (harmless rounding), on gold 386 bars of
2004-2011 were broken. Bars are now kept with the range widened to include open
and close, and a market never evaluated on a decision day is retried.

Rerun on the corrected data (same code, same walk-forward):

| Model | Trades | Hit | Mean net | PF | P(mean>0) | 2010-17 / 2018+ | Markets + |
|---|---|---|---|---|---|---|---|
| v1 logistic | 2510 | 51.5% | +0.004% | 1.01 | 0.55 | -0.025% / +0.063% | 4/10 |
| v1 gradient boosting | 3037 | 51.1% | +0.011% | 1.02 | 0.65 | +0.025% / -0.017% | 4/10 |
| v2 (COT) | 2840 | 51.1% | -0.013% | 0.98 | 0.34 | -0.036% / +0.027% | 4/10 |
| v2c (COT, C = 0.1) | 2764 | 51.0% | -0.015% | 0.97 | 0.33 | -0.041% / +0.033% | 3/10 |

Gold went from the largest contributor (+44% / +61%) to slightly negative, and
USDJPY from +19% to -14% for v2c. A result that flips when 3.5% of bars are
handled differently is not an edge: **the FX ML models have no demonstrated
edge.** The 2026 models were retrained on the corrected data so live features
and training match; the MT5 demo keeps running only as a free forward
experiment, with the graduation criteria above unchanged.

All earlier forex studies were rerun on the corrected loader the same day
(forex rules, CFD trend, FX factors, forced flows, regime system, development):
every verdict is unchanged (all FAIL).

**Root cause found the same day: Yahoo's FX daily bars are malformed in some
years.** Cross-checking against Dukascopy hourly bid/ask: in many Yahoo FX daily
bars the open equals the close, and both are a snapshot of the price at London
midnight (the start of the bar), while high and low cover the following day.
Share of bars with open == close (EURUSD=X; USDJPY=X is the same): about 0-3%
before 2013, 7-20% in 2013-2019, **78% in 2022, 98% in 2024, 86% in 2025**, 3% in
2026. GC=F has the same defect in 2004-2010; ^GSPC never. So a "close outside
the range" was not a rounding error but a close from a different moment, and in
those years every close-based feature was one day stale (no look-ahead: stale,
not future). The earlier "widen the range" fix treated a symptom only.

Consequence: Yahoo is not a reliable source for daily FX bars. Next step (a
data correction, not a new model): rebuild daily FX bars from Dukascopy hourly
bid/ask with the standard New York 17:00 day, rerun v1/v2c unchanged on them, and
have the live bot compute features from the broker's own MT5 daily bars.

**Rerun on clean Dukascopy daily bars (2026-10-06): no edge.** Same code, same
walk-forward, FX bars from Dukascopy's own day candles (real open and close):

| Model | Trades | Hit | Mean net | PF | P(mean>0) | 2010-17 / 2018+ | Markets + |
|---|---|---|---|---|---|---|---|
| v1 logistic | 3033 | 50.2% | -0.024% | 0.96 | 0.21 | -0.061% / +0.059% | 4/10 |
| v1 gradient boosting | 3139 | 49.4% | -0.030% | 0.95 | 0.15 | -0.009% / -0.103% | 5/10 |
| v2 (COT) | 3285 | 50.6% | -0.019% | 0.97 | 0.25 | -0.050% / +0.045% | 4/10 |
| v2c (COT, C = 0.1) | 3166 | 50.5% | -0.026% | 0.95 | 0.18 | -0.059% / +0.044% | 3/10 |

With clean data every daily model loses slightly after costs. The daily FX ML
line is closed: v1/v2c stay on the demo only as a free experiment, no further
daily-model variants are tried on this data.
