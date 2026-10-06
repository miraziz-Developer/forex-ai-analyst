# Index pullbacks in an uptrend (MT5 CFDs) — pre-registration

Written and committed, with its code (`src/forex_ai_analyst/forex/index_study.py`),
**before** it is run.

**Why indices, not currency pairs.** 17+ approaches failed on currency pairs
(docs/*_STUDY.md; after the 2026-10-06 data fix the FX ML models have no edge
either). A currency pair has no built-in drift. Stock indices do (the equity
risk premium), and short, sharp sell-offs inside an uptrend are partly forced
or emotional selling that tends to be bought back within days. This
"buy the dip in an uptrend" effect has been published since about 2008
(Connors, RSI(2); internal bar strength), so it may have decayed: the test
says whether anything is left after realistic CFD costs.

**Markets (all six, no picking):** US500 (^GSPC), USTEC (^NDX), US30 (^DJI),
DE40 (^GDAXI), UK100 (^FTSE), JP225 (^N225); Yahoo daily bars 2004-2026.
Gold (GC=F) is reported for information only, not used for the verdict.

**Two variants (two trials), long only, one position per market:**
- **IDX1 — RSI(2).** Signal at the close when close > SMA(200) and RSI(2) < 10.
  Exit signal: close > SMA(5).
- **IDX2 — internal bar strength.** Signal when close > SMA(200) and
  IBS = (close - low) / (high - low) < 0.2 and the close is below the previous
  close. Exit signal: close > the previous day's high.

Both: enter at the next open; exit at the next open after the exit signal, or
after 10 trading days; 3 ATR(14) protective stop from the entry, checked
intraday (fills at the stop, or at the open if it gaps through).

**Costs.** Round trip 0.03% of price (FBS-like index CFD spreads, conservative)
plus overnight swap of 6%/year on notional for every calendar day held.

**Drift baseline.** Indices rise on average, so any long trade looks good. The
baseline is "long every day the close is above SMA(200)" (next open to next
open, swap per day held, no spread: a continuous hold pays it once). A variant must earn clearly more per day in
the market than that, or it adds nothing over just holding the index.

**A variant passes only if, pooled over the six indices:**
1. at least 300 trades;
2. mean net return per trade > 0 with entry-day block bootstrap
   P(mean > 0) >= **0.95** (two trials);
3. profit factor >= 1.3;
4. mean net > 0 in 2004-2014 and in 2015-2026;
5. at least 5 of 6 indices with a positive total;
6. mean net return per day held >= 2 x the drift baseline's.

If both pass, the one with the higher mean net per day held is adopted and an
MT5 bot is built for it on the demo, with a forward test before any real money.
If neither passes, nothing is built.
