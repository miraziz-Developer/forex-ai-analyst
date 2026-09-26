# Donchian 4h with a partial take-profit — pre-registration

Written and committed **before** the test is run.

**Question.** The owner wants a higher win rate. Taking half the position off
at a fixed target raises the share of winning trades; does it do so without
giving up profit?

- Base: live Donchian (100/20/3 ATR, long) on the ten live markets, 2021-2026,
  lab engine defaults (taker fee + slippage, historical funding).
- Variant: the same entries; half the position exits at +2R (target =
  2 × stop distance), the other half follows the normal Donchian exit. A trade's
  R is the average of its two halves. No other change, one variant only.

**The variant replaces the live exit only if all hold:**
1. Win rate (share of trades with R > 0) >= 50%.
2. Mean R >= 90% of the base mean R.
3. Day-clustered bootstrap P(mean R > 0) >= 0.90.

Otherwise the live exit stays as it is, and the result is recorded here.

## Result (2026-09-26) — FAIL, live exit unchanged

| | Trades | Win rate | Mean R | Profit factor | Total R |
|---|---|---|---|---|---|
| Base (live exit) | 575 | 34.1% | +0.67 | 2.18 | +384 |
| Half off at +2R | 575 | 37.4% | +0.38 | 1.69 | +217 |

Only 31.5% of trades ever reach +2R, so the target barely moves the win rate,
while it cuts the large trend winners that produce most of the profit: total R
falls 44%. Criteria 1 (win rate >= 50%) and 2 (mean R >= 90% of base) fail.

**Why a 64% win rate is the wrong goal for this strategy.** Profit per trade is
win rate × average win - loss rate × average loss. The live exit wins 34% of
the time with an average winner several times the average loser. To reach a
60%+ win rate the exit has to take profit much earlier, which shrinks the
average winner faster than it raises the win rate, as this test shows.
