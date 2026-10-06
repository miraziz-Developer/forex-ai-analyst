# Carry + trend + value currency portfolio — pre-registration

Written and committed, with its code (`src/forex_ai_analyst/forex/fx_factors.py`)
and look-ahead tests, **before** the study is run.

**Why.** The three currency premia with the longest academic and industry
record are carry, trend (time-series momentum) and value; funds combine them
because they tend to fail at different times. The owner chose this portfolio.

**Universe.** USD, EUR, GBP, JPY, AUD, NZD, CAD, CHF; each position is held
against USD through the matching major pair. Monthly rebalancing.

**Signals (decided at month end, using spot closes through that month and
interest rates through the previous month):**
- Carry: rank the eight currencies by 3-month interbank rate (OECD); long the
  three highest-yielding, short the three lowest, 1/3 each (dollar neutral).
- Trend: each of the seven currencies, long if its 12-month excess return
  versus USD (spot plus interest differential) is positive, else short; 1/7 each.
- Value: rank by 5-year spot change (nominal proxy for real-rate value); long
  the three that fell most, short the three that rose most, 1/3 each.
- Portfolio: each strategy scaled to 10% annual volatility using only its
  trailing 24 months; the three averaged and scaled again to 10%.

**Returns and costs.** Monthly excess return = spot change + interest
differential (Yahoo month-end closes, OECD rates from 2003). Retail swap markup
of 1.0%/year against every open position, long or short; 0.02% per unit of
weight traded.

**The portfolio passes only if, after costs:**
1. annualised Sharpe >= 0.4;
2. 12-month block bootstrap P(mean > 0) >= 0.90;
3. mean return positive in both halves of its history;
4. maximum drawdown no worse than -35% at 10% volatility.

The three components are reported with the same metrics. Only the combined
portfolio is the decision; if it passes, an MT5 rebalancer is built for it.

## Result

(pending)
