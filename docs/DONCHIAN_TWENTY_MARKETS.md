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

## Result (2026-09-26) — PASS

| Market | Trades | Win rate | Total R |
|---|---|---|---|
| DOT | 59 | 24% | +0.8 |
| TRX | 63 | 33% | +24.6 |
| BCH | 60 | 23% | +2.3 |
| UNI | 59 | 25% | -9.5 |
| NEAR | 56 | 41% | +18.8 |
| ATOM | 58 | 24% | +2.2 |
| ETC | 51 | 35% | +73.6 |
| FIL | 47 | 36% | +25.1 |
| AAVE | 66 | 26% | -6.3 |
| XLM | 53 | 21% | +65.5 |

Pooled: 572 trades, mean +0.35R, bootstrap P(mean R > 0) = 0.932, profit
factor 1.56, 8 of 10 markets positive, largest share of positive R 19%. All
criteria pass, with a weaker edge than on the first ten markets.

**Action:** the live list becomes twenty markets. On all twenty (1147 trades,
2021-2026): win rate 31%, mean +0.51R, worst losing run 26, worst cumulative
drawdown -64R. The degradation alarm is re-anchored to this real history.

## One-account simulation (after the test; for sizing, not selection)

Twenty markets traded from one account: each trade risks a fixed share of the
equity at entry, at most N positions open, realized equity only (open-trade
swings are not marked, so real drawdowns are somewhat deeper).

| Risk / trade | Max open | Trades / month | CAGR | Max DD |
|---|---|---|---|---|
| 0.3% | 6 | 9.5 | 11.3% | -9.4% |
| 0.3% | 8 | 11.3 | 16.2% | -11.2% |
| **0.3%** | **12** | **14.2** | **24.4%** | **-15.0%** |
| 0.3% | 20 | 16.9 | 27.3% | -18.3% |
| 0.5% | 12 | 14.2 | 38.4% | -24.1% |
| 1.0% | 8 | 11.3 | 45.8% | -33.0% |

Year by year at 0.3% / 12: strongly positive in 2021, 2023 and 2024, small
losses in 2022 and 2025, roughly flat in 2026 so far — most of the return comes
from a few strong trend years. The live default cap is set to 12, the best
CAGR-to-drawdown ratio in this table; this choice was made after seeing these
numbers and is disclosed as such.
