# Crypto bot: ten more markets, and a capitulation-rebound engine — pre-registration

Written and committed, with its code (`src/forex_ai_analyst/lab/engine2_study.py`),
**before** it is run. Period 2021-01-01..2026-09-01, Binance USD-M history,
lab engine defaults (taker fees + slippage, historical funding), as in every
crypto study.

## A — the live Donchian rule on ten more markets
Selection rule, applied before any backtest: the ten most liquid BingX USDT
perpetuals by 24h quote volume today, excluding the live twenty, tokenised
stocks/commodities and stablecoins, that have Binance 1h history from
2021-01: **ZEC, RLC, YFI, DASH, SAND, XMR, TRB, AXS, CRV, ALGO**.
Rule unchanged (4h, 100/20, 3 ATR, long). Pass criteria exactly as in
docs/DONCHIAN_TWENTY_MARKETS.md: pooled mean R > 0 with day-clustered bootstrap
P >= 0.90; profit factor >= 1.20; at least 6 of 10 markets positive; no single
market above 30% of total positive R. A pass adds them to the live list.

## B — capitulation rebound (new, meant to be uncorrelated with trend)
On 1h bars of the twenty live markets: when the close is at least 12% below
the close 24 hours earlier and the bar closes green (close > open), buy at the
next open. Stop: below the lowest low of the last 24 hours by 0.5 ATR(14).
Target: half of the 24-hour drop recovered. Time exit after 24 bars. Long
only, one position per market.
**Passes only if:** full-period Sharpe >= 0.5 with positive Sharpe in
2021-2023 and in 2024-2026; deflated Sharpe >= 0.90 with 181 trials counted;
profit factor >= 1.2; at least 12 of 20 markets positive; correlation of daily
returns with the live Donchian <= 0.5. A pass becomes a second engine of the
live bot (BingX VST demo first).
