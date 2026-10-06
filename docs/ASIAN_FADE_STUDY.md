# Asian-session fade on real bid/ask hourly data — pre-registration

Written and committed, with its code (`src/forex_ai_analyst/forex/asian_fade_study.py`),
**before** it is run. It uses the same Dukascopy hourly bid/ask files as
docs/H1_ML_STUDY.md (no extra data).

**Why.** Between the New York close and the London open, liquidity is thin and
there is little news; moves away from the recent average are mostly noise from
small orders and tend to revert. Retail "night scalpers" have traded this for
years, but their published results ignore that spreads widen around the daily
rollover. With the real spread each hour, the effect can be judged honestly.

**Markets.** The ten of the H1 study: EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD,
USDCHF, NZDUSD, EURJPY, GBPJPY, EURGBP. 2010-2026, test from 2014 (no fitting:
the rule has no trained parameters, the first years only warm up indicators).

**Rule.** Bollinger band on hourly mid closes, 20 hours, k standard deviations.
At the close of an hourly bar ending 00:00-04:00 UTC (bars opening 23:00-03:00)
on Monday-Friday mornings:
- close above the upper band: sell at the next bar's open (bid);
- close below the lower band: buy at the next bar's open (ask).
Exit at the next bar's open (opposite side of the quote) after the first hourly
close back on the other side of the 20-hour average, or at the 06:00 UTC bar's
open at the latest. Protective stop: an hourly close beyond entry by 2x the
band's half-width exits at the next open. One position per market at a time.
Costs: the real spread both ways plus 0.3 basis points.

**Two variants (two trials):** AF1 k = 2.0; AF2 k = 2.5.

**A variant passes only if (2014-2026):**
1. at least 500 trades;
2. mean net > 0 with day-block bootstrap P(mean > 0) >= **0.95**;
3. profit factor >= 1.2;
4. mean net > 0 in 2014-2019 and in 2020-2026;
5. at least 6 of the 10 markets positive.
