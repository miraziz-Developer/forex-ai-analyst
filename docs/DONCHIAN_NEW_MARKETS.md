# Donchian 4h on unseen markets — pre-registration

Written and committed **before** any backtest on these markets.

**Question.** Does the live configuration hold on markets it was never
selected or tuned on?

- Strategy: `donchian`, 4h bars, `entry_n=100, exit_n=20, stop_atr=3.0`, long
  only — exactly the live parameters. No re-optimisation of any kind.
- Markets: DOGE-USDT, ADA-USDT, LINK-USDT, AVAX-USDT, LTC-USDT (Binance USD-M
  perpetual klines; never used in `docs/LAB_REPORT.md`).
- Period: 2021-01-01..2026-09-01 (whatever each market's history covers).
- Engine and costs: `forex_ai_analyst.lab.engine` defaults (taker 0.05% +
  slippage 0.02% per side, historical funding, 1% risk, 3x notional cap).

**Pass criteria (all required), on the pooled trades of the five markets:**
1. Mean R > 0 with day-clustered bootstrap P(mean R > 0) >= 0.90.
2. Profit factor >= 1.20.
3. At least 3 of the 5 markets have positive total R.
4. No single market contributes more than 50% of total positive R.

**If it passes:** the same parameters may be added to the live pair list
(10 markets). **If it fails:** the live list stays at 5 markets, and the
result counts as evidence against the strategy's generality.

Whichever way the result goes, it is appended below and not re-run with
different settings.

## Result

(pending)
