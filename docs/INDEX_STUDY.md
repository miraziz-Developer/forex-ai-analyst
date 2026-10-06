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

## Result (2026-10-06) — IDX1 passes, IDX2 fails

| Variant | Trades | Win | Mean net | PF | P(mean>0) | 2004-14 / 2015-26 | Indices + | Net/day vs drift | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| **IDX1 RSI(2)** | 1092 | 68% | **+0.199%** | **1.34** | **0.997** | +0.188% / +0.208% | **6/6** | 0.049% vs 0.012% (4.2x) | **PASS** |
| IDX2 IBS | 2590 | 67% | +0.068% | 1.11 | 0.93 | +0.138% / +0.010% | 5/6 | 0.016% vs 0.012% | FAIL |

Gold (information only): IDX1 +0.011% per trade over 155 trades, i.e. nothing.

**Checks after the verdict (none changes the rule):**
- *Independent re-implementation* reproduces 1092 trades, +0.199%, PF 1.34.
- *Data.* No bar with a close outside its range. Yahoo's UK100 opens equal the
  previous close (fake opens), and so do 798 US500 bars before 2022. On the three
  indices with real opens (USTEC, DE40, JP225): next-open entry +0.181% (PF 1.25),
  entry at the signal close +0.234% (PF 1.33); all six at the close +0.236%
  (PF 1.42). The edge does not come from the fake opens; entering near the
  close is, if anything, better.
- *Neighbouring parameters* (not used for selection): RSI(2) < 5 / 15 / 20,
  exit SMA 3 / 10, trend SMA 100 / 150: all positive on 6/6 indices, PF
  1.22-1.54. Not a knife-edge.
- *No decay after publication:* 2015-2026 is as good as 2004-2014.

**Economics — real but small.** About 48 trades a year across six indices, each
held about 4 days. Compounded, chronological, after costs and swaps:

| Sizing | CAGR | Max drawdown | Losing years |
|---|---|---|---|
| risk 1% per trade (3 ATR stop) | 2.6% | -15% | 7 of 23 |
| risk 2% per trade | 5.2% | -28% | 7 of 23 |
| 50% of equity notional per trade | 4.7% | -20% | 7 of 23 |
| 100% of equity notional per trade | 9.1% | -37% | 7 of 23 |

Return / drawdown is about 0.24 whatever the sizing: a genuine edge, but alone
it is a weak system (the crypto Donchian backtest is about 1.6). Its value is
as a second, independent return stream next to crypto.

**Owner decision (2026-10-06): shelved.** The edge is real but the return is too
small to be worth a separate bot for now; no MT5 bot is built. Kept in reserve
as a possible second return stream next to crypto.

## Reactivated as a forward test (2026-10-06, owner's request for more trades)

`index_live.py` runs IDX1 unchanged inside the MT5 demo bot on the broker's
index CFDs (US500, US30, NAS100, GER40, UK100, JP225, whichever exist): buy on a
completed D1 close above the 200-day average with RSI(2) < 10, 3 ATR stop, exit
after a close above the 5-day average or 10 bars. Risk 0.5% per trade
(`FX_INDEX_RISK_PCT`) with adaptive allocation and all guards; stress moves for
indices are the worst documented one-day falls (12-16%). Journalled as
`index_pullback_v1`. Graduation, fixed now: 60 closed forward trades, mean > 0,
bootstrap P >= 0.90, PF >= 1.2.
