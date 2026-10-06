# London-open breakout of the Asian range (EURUSD, GBPUSD) — pre-registration

Written and committed, with its code (`src/forex_ai_analyst/forex/london_breakout_study.py`),
**before** it is run.

**Why.** The most popular intraday rule for EURUSD and GBPUSD: the Asian
session builds a narrow range, and when London opens, large orders push price
out of it. Never tested here. Real Dukascopy hourly bid/ask, 2010-2026.

**Rule (one trade a day at most, weekdays).**
- Asian range: high and low of the mid price over the hourly bars 00:00-06:59 UTC.
- From the 07:00 UTC bar to the 15:00 UTC bar: the first bar whose high (ask)
  reaches range high + 0.5 pip buys at that level (sell symmetric at range
  low - 0.5 pip on the bid). If one bar touches both sides, the day is skipped.
- Stop: the opposite side of the range. Target: entry +/- the range width
  (1:1). If a later bar touches both stop and target, the stop counts.
- Exit at the 16:00 UTC bar's open if neither was hit. Exits pay the real
  spread; 0.3 bp markup per round trip.

**Passes only if:** on EURUSD + GBPUSD pooled, at least 500 trades,
P(mean > 0) >= 0.95 (day bootstrap), PF >= 1.2, mean > 0 in 2010-2017 and in
2018-2026, and both pairs positive; and on the other eight pairs pooled
(replication) the mean is > 0 with at least 5 of 8 positive.

## Result (2026-10-06) — FAIL

| Sample | Trades | Win | Mean | Mean R | PF | P | 2010-17 / 2018-26 | Positive |
|---|---|---|---|---|---|---|---|---|
| EURUSD + GBPUSD | 8305 | 51.4% | +0.23 bp | +0.03 | 1.02 | 0.78 | -0.10 / +0.54 bp | 2/2 |
| other eight | 32891 | 50.0% | -0.17 bp | +0.01 | 0.99 | 0.24 | -0.24 / -0.10 bp | 4/8 |

The breakout wins about as often as it loses at 1:1, so after the real spread
it is a coin flip on EURUSD and GBPUSD (+0.23 bp a trade) and slightly negative
on the other pairs. Not adopted.
