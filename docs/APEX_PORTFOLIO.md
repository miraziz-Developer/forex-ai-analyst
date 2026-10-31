# Apex Portfolio: one risk layer over the proven engines — pre-registration

Written and committed, with `src/forex_ai_analyst/forex/apex_portfolio.py`, **before** it is run.

**Why.** The owner asked for one ideal system from the engines that passed, using what modern funds use.
No new signal (about 210 have been tested); instead the two best-documented portfolio techniques:
inverse-volatility risk budgeting across engines, and portfolio volatility targeting (Moreira and Muir,
"Volatility-Managed Portfolios", Journal of Finance 2017). Machine-learning filters were tested five times
in this repository and failed every holdout, so none is added.

**Engines** (monthly R from each engine's published backtest, 2021-01..2026-08): crypto Donchian H4 CFD,
gold H4, commodity trend D1, index pullback, month-end fix, crypto rebound H1 CFD, pre-FOMC US500.

**Allocations.** A = the bot's current fixed risk per engine. B = inverse volatility (trailing 12 months),
same total risk. C = B scaled to 12% annual portfolio volatility (trailing 12 months, 0.5x-2x). Weights use
only past months. 2021 is the warm-up year.

**Decision (fixed now).** Development 2022-2023, holdout 2024-01..2026-08. C (else B) replaces A only if its
holdout Sharpe is higher than A's and its holdout max drawdown is not worse by more than 2 points.
