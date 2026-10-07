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

### Replication result (2026-10-07) — PASS

| Sample | Trades | Win | Mean R | PF | Avg win / loss |
|---|---|---|---|---|---|
| Ten unseen markets, 2021-01..2026-09 | 1,183 | 50.2% | **+0.145** | **1.33** | +1.17 / -0.89 R |
| 2021-2023 | 795 | 50.3% | +0.141 | 1.32 | |
| 2024-2026 | 388 | 50.0% | +0.153 | 1.34 | |

Bootstrap P(mean > 0) = 0.987; **10 of 10 markets positive** (total R: ALGO +29.6, RLC +23.9, CRV +20.8,
SAND +19.3, AXS +18.6, DASH +17.3, TRB +13.4, ZEC +12.3, YFI +10.6, XMR +5.6). Costs and funding included.
On data it had never seen, the rule did slightly better than on the twenty markets it was first tested
on (+0.10R, PF 1.23), so the earlier result was not a product of the many trials. With the original twenty
it covers all thirty live markets: about 2,700 trades over 5.7 years, roughly 40 a month. Next: paper
forward test as the crypto bot's second engine on the BingX VST demo.

## Improving B (more profit and a higher win rate) — pre-registration (2026-10-07)

Owner's request after the replication pass. Tuning on data that has already been seen produces a better
backtest and a worse live result, so: **changes are chosen on the twenty original markets only**, and the
one chosen version is run **once** on the ten replication markets, where the unchanged rule made +0.145R,
PF 1.33. Code `src/forex_ai_analyst/lab/rebound_improve.py`, committed before it is run.

Each candidate change has a reason written before any number is seen:

| Variant | Change | Why it could help |
|---|---|---|
| `F` | only when the last known funding rate is <= 0 | after the crash shorts are crowded and paying: fuel for a squeeze |
| `M` | only when BTC is also down >= 5% in the same 24 h | a market-wide liquidation is forced selling; a single-coin crash may be news (hack, unlock) that does not come back |
| `V` | only when some hour of the 24 h had >= 3x the prior week's hourly volume | a selling climax marks forced liquidation |
| `T38` | take profit at 38.2% of the drop instead of 50% | more trades reach the target: higher win rate (smaller wins) |
| `S1` | stop 1.0 ATR below the 24 h low instead of 0.5 | fewer stop-outs on a last flush: higher win rate (larger losses) |

**Choice (fixed now):** the single changes that raise the development t-statistic of mean R over the base
rule are also tried together; the variant with the highest development t-statistic is chosen. The
t-statistic, not mean R, so that a filter cannot win merely by trading less.

**The chosen variant is adopted only if, on the ten replication markets:** mean R > +0.145 (beats the
unchanged rule), bootstrap P >= 0.95, PF >= 1.15, at least 6 of 10 markets positive, both halves positive.
Otherwise the unchanged rule stays.

### Improvement result (2026-10-07) — the unchanged rule stays

Twenty original markets, 2021-01..2026-09 (the base row skips each market's first eight days, which the
volume filter needs as history, so it differs slightly from the original B row):

| Variant | Trades | Win | Mean R | PF | t-stat | Halves | Markets + |
|---|---|---|---|---|---|---|---|
| **base** | 1,545 | 51.8% | **+0.097** | **1.22** | **3.38** | +0.067 / +0.174 | 16/20 |
| F funding <= 0 | 690 | 49.7% | +0.075 | 1.17 | 1.77 | +0.078 / +0.069 | 14/20 |
| M BTC also down 5% | 1,221 | 52.7% | +0.095 | 1.22 | 3.00 | +0.044 / +0.242 | 16/20 |
| V volume climax | 1,189 | 53.5% | +0.096 | 1.23 | 3.04 | +0.046 / +0.196 | 15/20 |
| T38 target 38.2% | 1,621 | 55.0% | +0.061 | 1.15 | 2.48 | +0.044 / +0.105 | 15/20 |
| S1 stop 1.0 ATR | 1,448 | 56.2% | +0.074 | 1.20 | 3.04 | +0.059 / +0.114 | 17/20 |

No change raised the t-statistic, so by the rule fixed in advance the base rule is chosen and the ten
replication markets are not spent again (the base rule already passed there). The filters keep about the
same edge per trade on fewer trades; the exit changes raise the win rate (55-56%) but cut the size of the
wins more than they cut the losses, so profit falls. The rule as first written is already the best of these.

## Live: second engine of the BingX bot (VST demo), 2026-10-07

`trading/application/rebound_engine.py` takes its signal from the lab function above, unchanged. Every
scan (5 minutes) checks the newest closed 1h bar of each of the thirty pairs. A signal is re-anchored to the
live price with the backtest's stop and target distances, sent with both the stop and the take-profit on
BingX, and skipped if price has already run more than half a stop distance past the signal close. After 24
hours the scheduler closes the position at market and cancels the leftover stop and target. It shares the
risk controls (RISK PCT), the 12-position cap and the one-position-per-pair gate with Donchian, so it does
not trade a coin that Donchian holds. `REBOUND_ENABLED=false` switches it off; `REBOUND_LEVERAGE` (3).
