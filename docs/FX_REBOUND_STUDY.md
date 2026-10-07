# Capitulation rebound on FX and metals (H1) — pre-registration

Written and committed, with `src/forex_ai_analyst/forex/fx_rebound_study.py`, **before** it is run.

**Why.** Every chart-indicator rule tested on FX failed (3-EMA, seven Investopedia rules, 3 SMA, pivots,
support/resistance, trendlines, earlier H1 studies). The one short-term rule that passed anywhere is a
forced-flow rule: the crypto capitulation rebound (docs/CRYPTO_ENGINE2_STUDY.md; PF 1.33 on ten unseen
coins). Forced selling exists in FX and metals too (stop cascades, margin calls, flash moves). This study
carries the rule over with the threshold scaled to each market's own volatility, since a 12% day is not
an FX event.

**Rule (base).** H1 bars, mid prices for signals. At an hour's close, if the move over the last 24 hours is
a fall of at least k = 3 standard deviations of 24-hour moves (estimated from the 720 hours before that
window) and the hour closed green, buy at the next open (ask). Stop: 0.5 ATR(14) below the 24-hour low.
Target: half of the 24-hour fall recovered. Out at the open 24 hours after entry at the latest. A rise of
3 sigma and a red hour sells, mirrored. One position per market.

**Data and costs.** Dukascopy hourly bid/ask; EURUSD, GBPUSD, USDJPY, USDCHF, AUDUSD, NZDUSD, USDCAD,
EURJPY, GBPJPY, EURGBP, XAUUSD, XAGUSD. Real spread plus 0.7 pip per round trip (gold 0.02%, silver
0.05%). Inside an hour the stop is assumed to come before the target. Results in R.

**Protocol.**
- **Development** = 2010-2018. Variants, each with its reason, written now:

| Variant | Change | Why |
|---|---|---|
| `K2.5` | k = 2.5 | more trades, if smaller extremes also revert |
| `K3.5` | k = 3.5 | only the most forced moves |
| `LONG` | buy crashes only | the crypto rule is long only; sell-offs are where margin calls hit |
| `R` | no signal from bars opening 20:00-23:00 UTC | rollover spreads make late-day spikes fake and costly |
| `T38` | target 38.2% of the move | higher win rate |

  Every single change that raises the development t-statistic of mean R over the base is also tried
  together; the variant with the highest development t-statistic is chosen. The holdout is spent only if
  the chosen variant has t >= 2.0 and at least 300 development trades.
- **Holdout** = 2019-01 to 2026-09, run once. **Passes only if:** at least 300 trades; mean R > 0 with
  month-bootstrap P >= 0.95; PF >= 1.15; at least 7 of 12 markets positive; mean R > 0 in 2019-2022 and in
  2023-2026.

## Development result (2026-10-07) — FAIL; the holdout is not spent

| Variant | Trades | Win | Mean R | PF | t | Long / short mean R | Markets + |
|---|---|---|---|---|---|---|---|
| base (k 3) | 862 | 37.2% | -0.145 | 0.76 | -3.42 | -0.157 / -0.132 | 2/12 |
| K2.5 | 1,894 | 36.4% | -0.132 | 0.78 | -4.52 | -0.088 / -0.183 | 0/12 |
| K3.5 | 395 | 43.0% | +0.024 | 1.04 | +0.36 | -0.052 / +0.109 | 5/12 |
| LONG | 451 | 37.3% | -0.157 | 0.75 | -2.65 | -0.157 / - | 3/12 |
| R (no rollover) | 784 | 38.1% | -0.134 | 0.77 | -3.05 | -0.149 / -0.118 | 2/12 |
| T38 | 882 | 39.3% | -0.152 | 0.74 | -3.84 | -0.162 / -0.141 | 2/12 |
| K3.5+LONG+R | 182 | 43.4% | -0.035 | 0.94 | -0.37 | -0.035 / - | 6/12 |

The best variant (K3.5) is indistinguishable from zero (t 0.36), so by the rule fixed in advance the
holdout is not spent. In FX and metals a 3-sigma day tends to keep going rather than snap back: such moves
are mostly repricing on news and policy (information), not the leveraged forced selling that makes crypto
crashes overshoot. The crypto result does not carry over.
