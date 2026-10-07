# "3 EMA scalping" (ForexFactory) — pre-registration and protocol

Written and committed, with its code (`src/forex_ai_analyst/forex/scalp_3ema_study.py`),
**before** any result is seen.

**Source rule (forum thread "Most profitable scalping strategy I found in 10
years", claimed 95% win rate; coders there reported PF 1.24 GBPUSD, 1.07 USDCHF,
"not working on EURUSD"):** EMA 8/13/21. H1 trend = EMAs stacked (8 > 13 > 21
up, reversed down, otherwise no trade). On M5, in the H1 direction, a pending
stop order 3 pips beyond the highest high (buy) / lowest low (sell) of the last
five M5 candles; stop loss 3 pips beyond the opposite extreme of those five
candles; take profit 1:1; the pending order expires after 60 minutes.

**Data.** Dukascopy one-minute bid/ask, EURUSD, GBPUSD, USDCHF, 2024-10-01 to
2026-09-30. M5 and H1 bars are built from them (mid prices for signals).
Fills: a buy stop fills when the ask reaches the trigger (at the trigger, or at
the open if it gaps through); exits on the bid (sells mirrored). Stop checked
first within a minute. 0.3 bp markup per round trip on top of the real spread.
One order or position per pair at a time.

**Protocol (so that "find the weakness, fix it, retest" stays honest):**
1. **Development** = 2024-10-01 .. 2025-09-30. The base rule is run here; its
   weaknesses are diagnosed here; improvements are built and compared here.
2. **Holdout** = 2025-10-01 .. 2026-09-30, untouched until a single final
   version is chosen on development data. That version is run on the holdout
   once.
3. **The final version passes only if, on the holdout:** at least 300 trades;
   mean net > 0 with day-bootstrap P(mean > 0) >= 0.95; profit factor >= 1.2;
   at least 2 of 3 pairs positive. Every version tried on development data is
   listed in the result, so the number of attempts is visible.

## Development result (2026-10-08) — FAIL; the holdout is not spent

EURUSD, GBPUSD, USDCHF, 2024-10..2025-09 (zero-volume filler minutes dropped, see SCALP_MULTI_STUDY.md):
2,805 trades, hit rate 43.2%, mean -0.145R (-2.5 bp), PF 0.76, P(mean > 0) 0.00, **0 of 3 pairs positive**
(total EURUSD -11.5%, GBPUSD -16.0%, USDCHF -42.9% of notional summed). The improvements tried for this rule
are part of SCALP_MULTI_STUDY.md (rule r8 inside the portfolio versions); none qualified there either.
The forum's claimed 95% win rate is 43% on real bid/ask data.
