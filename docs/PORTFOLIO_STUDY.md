# How the MT5 engines combine, and the default risk per engine

Analysis only (`src/forex_ai_analyst/forex/portfolio_study.py`): each engine's
own backtest trades, summed in R per calendar month, January 2021 - August 2026
(68 months, the window every engine has data for).

**Correlation of monthly results** is close to zero for every pair of engines
(-0.11 to +0.12): crypto 4h, gold 4h, commodity daily trend, index pullback and
the month-end fix lose in different months, so together they are smoother than
any one of them.

**Single engines at 0.5% risk per trade:** crypto CAGR +33% / max DD -17% /
Sharpe 1.02; gold 4h +2.8% / -2.6% / 0.70; commodity +4.0% / -3.7% / 0.51;
index +2.0% / -3.3% / 0.89; fix +0.9% / -2.8% / 0.36.

| Allocation (risk per trade) | CAGR | Max DD | Sharpe | Worst month | Losing months |
|---|---|---|---|---|---|
| all 0.5% | +46% | -18.0% | 1.29 | -6.1% | 34/68 |
| crypto 0.5, small engines 1.0 | +58% | -18.9% | 1.42 | -8.8% | 31/68 |
| crypto 0.3, others 1.0 | +42% | -13.3% | 1.47 | -6.9% | 25/68 |
| **crypto 0.3, gold 1.0, index 1.0, commodity 0.5, fix 0.5 (default)** | **+38%** | **-15.7%** | **1.51** | **-6.9%** | **25/68** |
| without crypto (1.0 / 0.5) | +19% | -9.5% | 1.01 | -5.1% | 29/68 |

The default keeps commodity at 0.5% because over 2012-2026 its MT5 basket fell
33% at 1% risk (docs/COMMODITY_TREND_STUDY.md). Year by year with the default:
2021 +76%, 2022 -3%, 2023 +36%, 2024 +49%, 2025 +65%, 2026 (to August) +7%.

**Read with care.** These are backtests, crypto dominates them, and 2021-2026 is
the window on which the crypto rule was chosen; the simulation also ignores the
bot's caps (total open risk 6%, stress 15%), which will hold crypto below ten
simultaneous positions. Live results should be expected to be clearly lower;
the forward test and adaptive allocation decide from here.
