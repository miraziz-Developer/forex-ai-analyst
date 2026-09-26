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

## Result

(pending)
