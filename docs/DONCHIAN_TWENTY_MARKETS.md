# Donchian 4h on ten more markets — pre-registration

Written and committed **before** any backtest on these markets. Motivation:
the owner wants more trades; more independent-enough markets is the only way
to add trades without changing the tested rule.

- Strategy: live Donchian (100/20/3 ATR, long), unchanged.
- Markets: DOT, TRX, BCH, UNI, NEAR, ATOM, ETC, FIL, AAVE, XLM (USDT
  perpetuals, all listed on BingX; Binance history from 2021-01). None has been
  used in any earlier test.
- Period 2021-01-01..2026-09-01, lab engine defaults (taker + slippage,
  historical funding).

**Pass criteria (all required), pooled over the ten markets:**
1. Mean R > 0 with day-clustered bootstrap P(mean R > 0) >= 0.90.
2. Profit factor >= 1.20.
3. At least 6 of the 10 markets have positive total R.
4. No single market contributes more than 30% of total positive R.

**If it passes:** these markets join the live list (20 in total). **If it
fails:** the list stays at ten, and the result is recorded here unchanged.

## Result

(pending)
