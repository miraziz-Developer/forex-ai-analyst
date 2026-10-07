# Crypto Donchian 4h on MT5 CFDs (costs and swap) — pre-registration

Written and committed, with its code (`src/forex_ai_analyst/forex/crypto_cfd_study.py`),
**before** it is run.

**Why.** The live crypto rule (Donchian 4h, entry 100, exit 20, 3 ATR stop,
long only) is the project's proven edge on BingX perpetuals. On an MT5 CFD
account the costs differ: a wider spread and, above all, an overnight swap that
on crypto CFDs can reach 20-30% a year, charged every day a position is held.
Donchian holds trends for weeks, so the swap is the weak point to test.

**Data and rule.** Binance USD-M 1h candles resampled to 4h (the lab data),
2021-01 to 2026-09, ten coins: BTC, ETH, SOL, XRP, BNB, DOGE, ADA, LINK, AVAX,
LTC. Rule unchanged. No funding; instead a swap of 10%, 20% or 30% a year on
notional per calendar day held. Round-trip spread 0.15% for BTC/ETH, 0.30% for
the others (conservative CFD spreads).

**Decision (at the 20% swap level):** added to the MT5 demo only with profit
factor >= 1.3, day-clustered P(mean R > 0) >= 0.95 and at least 6 of 10 coins
positive. The 10% and 30% rows show how sensitive the result is to the broker's
actual swap.

## Result (2026-10-06) — passes at every swap level

| Swap / year | Trades | Win | Mean R | PF | P | Coins + | 0.5% risk: CAGR / max DD | 1% risk: CAGR / max DD |
|---|---|---|---|---|---|---|---|---|
| 10% | 575 | 33.9% | +0.69 | 2.19 | 1.00 | 9/10 | +33% / -17% | +61% / -32% |
| **20%** | 575 | 32.7% | **+0.65** | **2.11** | **1.00** | **9/10** | **+31% / -18%** | +56% / -33% |
| 30% | 575 | 31.7% | +0.62 | 2.02 | 1.00 | 9/10 | +28% / -19% | +51% / -35% |

Trades last 7.3 days on average, so even a 30% swap costs little. LTC is the
only losing coin. Caveats: 2021-2026 is the period on which the live rule was
chosen (it then passed out-of-sample market tests, docs/DONCHIAN_*), so live
results should be expected to be weaker; the coins move together, so the bot
adds a 40% one-day stress move for crypto; the Render bot trades the same
signals on BingX, so the two accounts carry correlated risk.
Adopted: a fourth MT5 demo engine (`crypto_live.py`).

## Capitulation rebound on the same CFDs — pre-registration (2026-10-07)

The rebound rule (docs/CRYPTO_ENGINE2_STUDY.md, B; replicated on ten unseen coins with PF 1.33) would add
about 20 short trades a month to the MT5 demo. These ten coins were among the markets B was first tested
on, so this is not a new test of the edge; it checks whether the edge survives CFD costs: the spreads above
(0.15% BTC/ETH, 0.30% others, round trip), no funding, swap 10/20/30% a year on the days held. Rule
unchanged. Code `src/forex_ai_analyst/forex/rebound_cfd_study.py`.
**Added to the MT5 demo only if, at the 20% swap:** mean R > 0, PF >= 1.15, bootstrap P >= 0.90, at least
6 of 10 coins positive.
