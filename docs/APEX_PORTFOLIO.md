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

## Result (2026-10-31) — C adopted

| | Period | CAGR | Max drawdown | Sharpe | Worst month | Losing months |
|---|---|---|---|---|---|---|
| A current | dev 2022-23 | 16.0% | -10.6% | 0.88 | -9.7% | 10/24 |
| B inverse vol | dev | 12.7% | -3.5% | 1.63 | -3.5% | 7/24 |
| **C Apex** | dev | 15.0% | **-2.5%** | **1.88** | -2.0% | 7/24 |
| A current | **holdout 2024-26** | 45.0% | -9.6% | 1.79 | -7.6% | 12/32 |
| B inverse vol | holdout | 12.8% | -4.0% | 2.02 | -1.8% | 11/32 |
| **C Apex** | **holdout** | 26.4% | **-7.7%** | **2.02** | -3.4% | 11/32 |
| A / B / C | full 2022-26 | 31.8% / 12.8% / 21.4% | -10.6% / -4.0% / -7.7% | 1.42 / 1.82 / **1.91** | | |

By the rule fixed in advance C replaces A: higher holdout Sharpe (2.02 vs 1.79) with a smaller drawdown
(-7.7% vs -9.6%) and half the worst month. It earns less than A in the holdout (26% vs 45% a year): A put
fixed, larger risk on the engines that happened to run hottest in 2024-2026 (crypto), C spreads risk by
volatility and steadies the whole book. Per unit of risk C is the better system; the 12% volatility target is
the dial between return and drawdown. Backtest numbers, not a promise.
