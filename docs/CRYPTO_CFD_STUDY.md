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
