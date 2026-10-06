# Commodity trend on unseen MT5 markets — pre-registration

Written and committed, with its code (`src/forex_ai_analyst/forex/commodity_trend_study.py`),
**before** it is run.

**Why.** On clean data, daily trend following loses on the nine currency pairs
but earns on gold (+48R) and BTC (docs/FOREX_STUDY.md), and long-only Donchian
earned on silver when silver was unseen (docs/TREND_CFD_STUDY.md). Trend in
commodities is the oldest documented CTA premium (time-series momentum,
Moskowitz, Ooi and Pedersen 2012). Gold and silver have now been looked at many
times, so the test needs commodities never used here.

**Markets.** Unseen (the test): WTI crude (Dukascopy LIGHTCMDUSD, from 2010),
Brent crude (BRENTCMDUSD, late 2010), copper (COPPERCMDUSD, 2012), platinum
(Yahoo PL=F) and palladium (Yahoo PA=F), 2004 on for the Yahoo futures.
Seen, reported only: gold (Dukascopy XAUUSD), silver (XAGUSD).
Natural gas (GASCMDUSD) is excluded before running: its CFD shows 106 opening
gaps above 3%, mostly on contract-roll weekends, which would create false
breakouts. Yahoo's front-month futures roll without adjustment; metals roll in
small steps, which is accepted and stated here.

**Rule.** Exactly the two variants of docs/TREND_CFD_STUDY.md: Donchian on daily
bars, 55-day breakout entry, 20-day opposite-channel exit, 2 ATR stop; both
directions, and long only.

**Costs.** Round trip 0.05% (oil, metals) / 0.10% (copper, palladium) of price,
plus 5%/year swap on notional for every day held (conservative, both sides).

**A variant passes only if, pooled over the five unseen markets:**
1. at least 100 trades;
2. mean R > 0 with day-clustered bootstrap P(mean R > 0) >= **0.95**;
3. profit factor >= 1.2;
4. mean R > 0 in both chronological halves;
5. at least 4 of the 5 unseen markets with positive total R.

If both pass, the higher unseen mean R is adopted and traded on all seven
markets (no picking) on the MT5 demo, next to the other engines.

## Result (2026-10-06) — neither variant passes; long-only is the closest result so far

| Variant | Unseen trades | Win | Mean R | PF | P(mean R>0) | Halves | Unseen + | Verdict |
|---|---|---|---|---|---|---|---|---|
| both directions | 439 | 25.5% | +0.13 | 1.16 | 0.79 | +0.37 / -0.10 | 4/5 | FAIL |
| **long only** | 234 | 27.4% | **+0.27** | **1.35** | 0.87 | **+0.37 / +0.17** | **5/5** | FAIL (P < 0.95) |

Long only, per market (total R / PF): WTI +7.1 / 1.25, Brent +8.4 / 1.78,
copper +0.7 / 1.03, platinum +27.6 / 1.57, palladium +18.8 / 1.31; seen: gold
+46.6 / 2.36, silver +21.9 / 1.59.

Every unseen commodity is positive and both halves are positive, but 234 trades
are not enough evidence for the 0.95 bar set for this many trials. Together
with the earlier unseen silver result and gold, the pattern is consistent:
long trend pays in commodities and crypto, not in currency pairs or stock
indices. Not adopted; the honest next step is a forward test of this exact
rule (long only, 55/20, 2 ATR) on the demo, with the graduation bar fixed in
advance.

## Forward test on the MT5 demo (from October 2026)

`trend_live.py` runs the long-only rule unchanged inside the demo bot (every
15 minutes, on the broker's completed D1 bars) on gold, silver, WTI, Brent,
copper, platinum and palladium — whichever the broker offers (symbol names are
searched; `FX_SYMBOL_MAP=WTI=...` overrides). Risk 0.5% per trade at the 2 ATR
stop (`FX_TREND_RISK_PCT`), with the stress, portfolio, spread and drawdown
guards. Journalled as `commodity_trend_long_v1`.

**Evaluation, fixed now:** after 60 closed forward trades or 24 months,
whichever is later, with nothing from the backtest: graduates only with mean
R > 0, bootstrap P(mean R > 0) >= 0.90 and profit factor >= 1.2.

## Replication on seven more unseen commodities — pre-registration

Written and committed, with its code (`commodity_trend_study.py --replicate`),
**before** it is run.

If long trend in commodities is a real premium, it must also appear in
commodities never used here. Candidates: 17 Yahoo continuous futures. Data
filter, fixed from data-quality counts only (no strategy result was looked
at): keep a market only with at most 90 opening gaps above 3% and at most 400
bars with open == close over 2004-2026 (roll jumps and malformed bars create
false breakouts). Kept: corn ZC=F (56 gaps / 137 flat), wheat ZW=F (86/214),
soybeans ZS=F (34/105), soybean oil ZL=F (52/331), soybean meal ZM=F (78/226),
sugar SB=F (53/96), heating oil HO=F (53/4). Dropped: coffee, cocoa, cotton,
live cattle, feeder cattle, lean hogs, gasoline, oats, orange juice, rough rice
(and lumber, unavailable).

Rule: the long-only variant above, unchanged (55/20, 2 ATR). Costs: 0.05% round
trip, 5%/year swap. **Passes only if**, pooled over the seven: at least 100
trades; P(mean R > 0) >= 0.95; profit factor >= 1.2; mean R > 0 in both halves;
at least 5 of 7 markets positive. A pass supports the commodity-trend engine
already in forward test; it does not add these markets to the bot (most are not
offered by the broker).
