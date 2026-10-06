# Month-end London 4pm fix reversal — pre-registration

Written and committed, with its code (`src/forex_ai_analyst/forex/fix_study.py`),
**before** it is run. Data: Dukascopy one-minute bid/ask candles
(`dukascopy.py`), a new source with the real spread minute by minute.

**Why.** At each month end, global funds rebalance their currency hedges at the
WM/Reuters 4pm London fix. The flow is forced and predictable in timing, it
pushes prices in the hour before the fix, and prices partly revert afterwards.
Evans (2016) reports that on month-end days about 72% of the move in the hour
before the fix is reversed by noon the next day; Melvin and Prins (2015) tie the
pre-fix move to equity-hedge rebalancing. The February 2015 reform widened the
fix window to five minutes, so only data from 2016 on is used: the question is
whether the reversal still pays after the reform and after real spreads.
(Our daily month-end test, docs/FORCED_FLOWS_STUDY.md, could not see this
intraday pattern.)

**Pairs.** EURUSD, GBPUSD, USDJPY, AUDUSD — the most liquid, chosen in advance
because the datafeed is slow (about 24 s a file).

**Days.** The last weekday of every month, January 2016 to September 2026, on
which the pair has minute data at the needed times; exit on the next weekday.
London times use Europe/London (daylight saving handled).

**Rule.** Pre-fix move m = mid price at 15:55 London / mid at 15:00 - 1
(mid = average of bid and ask candle opens). Enter against it at the open of
the 16:03 candle (sell at the bid if m > 0, buy at the ask if m < 0); exit at
the open of the next weekday's 12:00 candle at the opposite side of the
quote. An extra 0.5 basis point per round trip covers a retail markup over
Dukascopy's ECN spread.
- **FIX1** — every month end.
- **FIX2** — only when |m| >= 0.10% (a large hedging flow).

**A variant passes only if:**
1. at least 150 trades;
2. mean net > 0 with month-block bootstrap P(mean > 0) >= **0.95**;
3. profit factor >= 1.2;
4. mean net > 0 in 2016-2020 and in 2021-2026;
5. at least 3 of the 4 pairs with a positive total.

If one passes, it becomes a candidate for the MT5 demo (one trade per pair per
month). If neither passes, the idea is recorded as failed.

## Result (2026-10-06) — neither variant passes

All 129 month ends x 4 pairs had data (516 trades), real bid/ask both ways.

| Variant | Trades | Hit | Mean net | PF | P(mean>0) | 2016-20 / 2021-26 | Pairs + | Verdict |
|---|---|---|---|---|---|---|---|---|
| FIX1 every month end | 516 | 50.8% | +1.4 bp | 1.09 | 0.67 | -0.7 / +3.3 bp | 4/4 | FAIL |
| FIX2 large pre-fix move | 278 | 52.2% | +3.9 bp | 1.24 | 0.81 | -3.2 / +9.1 bp | 3/4 | FAIL |

The direction is the one the literature predicts and every pair but USDJPY in
FIX2 is positive, but the evidence is far from the 0.95 bar and 2016-2020 lost
money. Not adopted. Because the recent half is positive, this is the one
idea worth re-testing later on fresh data (from October 2026 on), as a new
pre-registered trial with these exact rules — not by tuning them now.

## Forward test on the MT5 demo (from October 2026)

`fix_live.py` runs the exact rule above inside the demo bot (checked every
minute): samples at 15:00 and 15:55 London on the last weekday of the month,
enters against the move at 16:03, exits at 12:00 London on the next weekday,
journalled as `fix_month_end_v1` (the pre-fix move is stored, so FIX2 is the
subset with |move| >= 0.10%). Sizing: a 1% emergency stop costing 0.25% of
equity (`FX_FIX_RISK_PCT`), plus the portfolio, stress and drawdown guards.
About 48 trades a year on four pairs.

**Evaluation, fixed now:** after 24 month ends (96 trades), pooled with
nothing from the backtest: FIX1 or FIX2 graduates only with mean net > 0,
month-block bootstrap P(mean > 0) >= 0.90 and profit factor >= 1.2.
