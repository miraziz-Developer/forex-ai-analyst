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
