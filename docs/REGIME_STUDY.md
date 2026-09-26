# Regime study — pre-registration

Written and committed, with its code, **before** the study is run.

**Question.** Can Donchian 4h earn more, and trade more, by respecting the
market regime? Regime = BTC's last completed daily close above (bull) or
below (bear) its 200-day simple moving average — one textbook rule, no search.

| Variant | Entries |
|---|---|
| baseline (live) | Donchian long, every regime |
| long_bull_filter | Donchian long only in a bull regime |
| long_bull_short_bear | Donchian long in bull, Donchian short (100-bar low break, 20-bar high exit, 3 ATR stop) in bear |

Exits are unchanged, so the filter never force-closes an open trade. Ten live
markets, 2021-01-01..2026-09-01, lab engine costs and historical funding.

**A variant replaces the live rule only if all hold:**
1. Sharpe higher than the baseline in **both** halves (2021-2023, 2024-2026).
2. Full-period max drawdown not worse than the baseline's by more than 10%.
3. For the short variant: the short trades alone have mean R > 0 with
   day-clustered bootstrap P >= 0.90 and profit factor >= 1.2.

If both pass, the one with the higher full-period Sharpe is adopted. If
neither passes, live stays unchanged. No re-run with other settings.

## Result

(pending)
