# Range and trend module development (with SMC/ICT filters) — pre-registration

Written and committed, with its code (`src/forex_ai_analyst/forex/development.py`)
and look-ahead tests for all 144 configurations, **before** the study is run.

**Why.** The regime study's range module won 63% of trades but lost slightly
because its winners were small; the owner asked to fix the weaknesses and add
SMC/ICT ideas. Optimising on all history would only fit noise, so the data is
split and each part has one job.

| Period (D1, 9 currency pairs + gold) | Use |
|---|---|
| Discovery 2004-01-01 .. 2014-12-31 | choose parameters (and SMC/ICT filters) |
| Validation 2015-01-01 .. 2020-12-31 | the chosen configuration must pass here |
| Holdout 2021-01-01 .. now | read once, only if validation passes |

**Search space (declared in full):**
- Range module, 72 configurations: ADX max {20, 25}; RSI(2) threshold {10, 5};
  target {middle band, far band, 1.5R}; stop {1.0, 1.5, 2.0} ATR beyond the
  two-bar extreme; SMC filter {none, liquidity sweep: bar i or i-1 traded
  through the last confirmed swing low/high and closed back}. 15-bar limit.
- Trend module, 72 configurations: ADX min {20, 25}; pullback reach {0.5, 1.0}
  ATR from EMA20; stop {1.5, 2.0, 3.0} ATR; exit {3 ATR trail, 20-bar channel,
  2R target}; ICT filter {none, fair value gap in the trend direction formed in
  the last 5 bars and revisited by bar i}.

**Selection.** For each module, the configuration with the highest t-statistic
of R on discovery (at least 60 discovery trades). No other choice is made.

**Validation gate:** at least 30 trades, bootstrap P(mean R > 0) >= 0.90,
profit factor >= 1.15.

**Holdout gate (read once, only after validation passes):** at least 20 trades,
mean R > 0 and profit factor >= 1.10.

144 trials are searched; selection bias on discovery is expected, which is why
only validation and holdout count. A module that passes both is eligible for an
MT5 demo trader.

## Result (2026-10-06) — both modules fail validation; holdout not opened

| Module | Chosen on discovery | Discovery 2004-2014 | Validation 2015-2020 | Verdict |
|---|---|---|---|---|
| Range | ADX < 25, RSI(2) 5/95, far-band target, 1.0 ATR stop, no SMC filter | 132 trades, win 46%, +0.08R, PF 1.17 | 74 trades, win 47%, -0.06R, PF 0.88, P 0.32 | FAIL |
| Trend | ADX > 20, pullback 1.0 ATR, 3.0 ATR stop, 2R target, no ICT filter | 164 trades, win 45%, **+0.30R, PF 1.51, +49R** | 78 trades, win 32%, **-0.06R, PF 0.92** | FAIL |

- This is what optimisation on history looks like: the best of 72 trend
  configurations made +49R with PF 1.51 in 2004-2014 and lost in the next six
  years. Had all 22 years been optimised at once, the same luck would have
  looked like a finished strategy.
- Neither the SMC liquidity-sweep nor the ICT fair-value-gap filter was
  selected: on discovery they did not improve the modules.
- The holdout (2021 onward) stays unread and can still be used for a future,
  separately pre-registered idea.
