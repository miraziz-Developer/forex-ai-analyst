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

## Result (2026-10-06)

**A — PASS; the live list grows to thirty markets.** 563 trades, 32.5% win,
mean +0.33R, PF 1.58, bootstrap P 0.989, 9 of 10 markets positive, largest
share of positive R 19%. Per market (total R): ZEC +37.7, RLC -15.1, YFI +6.0,
DASH +15.3, SAND +14.2, XMR +18.2, TRB +4.1, AXS +37.1, CRV +36.8, ALGO +32.5.
Quantity precision and minimums are BingX's own contract values.

**B — FAIL on the multiple-testing bar only.** 1553 trades (23 a month), 52%
win, mean +0.10R, PF 1.23, Sharpe 0.60 (2021-23 0.48, 2024-26 0.83), 16 of 20
markets positive, correlation with Donchian 0.01 — but deflated Sharpe 0.003
with 181 trials. Not adopted; it remains the best candidate for a later
paper forward test as an independent second crypto engine.

## Replication of B on the ten unseen markets — pre-registration (2026-10-07)

B failed only the multiple-testing bar (deflated Sharpe with 181 trials). The honest way past that bar is
data the rule has never seen: B was run on the twenty older live markets only, so the ten markets added in
part A (ZEC, RLC, YFI, DASH, SAND, XMR, TRB, AXS, CRV, ALGO) are unseen. Rule, costs (taker 0.05% + 0.02%
slippage per side, real funding) and period (2021-01..2026-09) unchanged; code
`src/forex_ai_analyst/lab/rebound_replication.py`, committed before it is run.

**Passes only if:** mean R > 0 with bootstrap P(mean > 0) >= 0.95; profit factor >= 1.15; at least 6 of 10
markets positive; mean R > 0 in 2021-2023 and in 2024-2026. A pass makes B a paper forward-test candidate
as the bot's second engine (BingX VST demo), never live money without a separate decision.
