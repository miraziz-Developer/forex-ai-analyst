# Fund-style FX strategies: dollar carry and macro momentum — pre-registration

Written and committed, with its code (`src/forex_ai_analyst/forex/fund_factors.py`),
**before** it is run.

Two strategies institutional currency managers use and retail traders rarely
do. Both are monthly, both trade the eight major currencies (USD, EUR, GBP,
JPY, AUD, NZD, CAD, CHF) through the seven USD pairs, and both reuse the
machinery of docs/FX_FACTOR_STUDY.md: month-end Yahoo closes (corrected loader),
OECD 3-month interbank rates (only the previous month's rate is used), excess
returns = spot change + interest differential, 1%/year swap markup on gross
exposure, 0.02% per unit of turnover, positions scaled to 10% annual volatility
using only the trailing 24 months.

- **DC — dollar carry** (Lustig, Roussanov and Verdelhan, "Countercyclical
  currency risk premia", JFE 2014). Each month-end: if the average 3-month
  rate of the seven foreign currencies is above the US rate, hold an equal
  long basket of the seven against USD; otherwise hold the equal short basket.
  It times the dollar as a whole and is a different bet from the
  high-minus-low carry rank that failed in FX_FACTOR_STUDY.

- **MM — macro momentum, market-based** (after Brooks, "A Half Century of Macro
  Momentum", AQR 2017). Each currency's score is the sum of two cross-sectional
  z-scores over the eight currencies: the 12-month change in its 3-month rate
  (monetary-policy trend) and the 12-month return of its stock index in local
  currency (risk-sentiment / growth trend). Long the three highest, short the
  three lowest, equal weights (USD's weight is held implicitly). Indices:
  ^GSPC, ^GDAXI (EUR), ^FTSE, ^N225, ^AXJO, ^NZ50, ^GSPTSE, ^SSMI.
  The business-cycle theme of the AQR paper (GDP forecast revisions) is left
  out on purpose: the only free proxy, the OECD composite leading indicator,
  is heavily revised after the fact, and its current vintage would leak future
  information into a backtest.

**A strategy passes only if (vol-scaled monthly returns):**
1. annualised Sharpe >= 0.4;
2. 12-month block bootstrap P(mean > 0) >= **0.95**;
3. mean > 0 in both halves of the sample;
4. maximum drawdown no worse than -35%.

The equal-weight combination of DC and MM is reported for information. A
passing strategy becomes a monthly MT5 demo rebalancer; if neither passes,
the result is recorded.

## Result (2026-10-06) — both fail

| Strategy | From | Sharpe | CAGR (10% vol) | Max DD | Halves (annual) | P(mean>0) | Verdict |
|---|---|---|---|---|---|---|---|
| DC dollar carry | 2007-06 | -0.17 | -2.5% | -49% | -4.4% / +0.5% | 0.25 | FAIL |
| MM macro momentum | 2008-07 | -0.24 | -2.9% | -50% | +1.3% / -6.2% | 0.29 | FAIL |
| combined (info) | 2009-07 | -0.17 | -2.4% | -49% | -1.0% / -2.6% | 0.20 | — |

Diagnostic without swap markup and turnover cost: DC Sharpe -0.04, MM +0.11.
The signals themselves earned about nothing after 2007, so this is not a cost
problem. Both papers' samples end before or around 2010-2013; after
publication and after the post-2008 era of near-zero, converging rates, these
fund factors have not paid on the major currencies.
