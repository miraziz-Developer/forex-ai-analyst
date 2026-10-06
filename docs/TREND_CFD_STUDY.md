# Trend following on metals, indices and crypto CFDs — pre-registration

Written and committed, with its code (`src/forex_ai_analyst/forex/trend_study.py`),
**before** the study is run.

**Why.** The forex study found no edge on currency pairs, but daily Donchian made
money on gold and BTC. The question is whether that is a real trend premium in
strongly trending markets or luck on two markets.

**Rule.** Donchian on daily bars, exactly the rule already tested in the forex
study: 55-day breakout entry, 20-day opposite-channel exit, 2 ATR stop. Two
variants only: both directions, and long only.

**Markets.**
- Never used with this rule (the test): silver (XAGUSD), US500, USTEC (Nasdaq
  100), US30, DE40, JP225, UK100.
- Already seen (reported, not used for the verdict): XAUUSD, BTCUSD; ETHUSD
  (crypto, studied on 4h before).

**Data and costs.** Yahoo Finance daily bars from 2004 (BTC 2014, ETH 2017).
Round trip: indices 0.02%, gold 0.02%, silver 0.06%, BTC 0.10%, ETH 0.15% of
price. Overnight swap charged on notional for every day held, in both
directions (conservative): indices 6%/year, metals 5%/year, crypto 20%/year.

**A variant passes only if, pooled over the seven unseen markets:**
1. at least 100 trades;
2. mean R > 0 with day-clustered bootstrap P(mean R > 0) >= 0.90;
3. profit factor >= 1.2;
4. mean R > 0 in both chronological halves;
5. at least 5 of the 7 unseen markets with positive total R.

If both variants pass, the one with the higher unseen mean R is adopted. The
adopted variant is traded on **all ten markets** (no picking of individual
markets) and written to `research_output/approved_strategies.json`, which is
the only list the MT5 trader may trade. If neither passes, nothing is approved.

## Result

(pending)
