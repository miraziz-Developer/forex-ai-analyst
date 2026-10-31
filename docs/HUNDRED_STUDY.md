# The strategy catalogue: 40 rule families x 4 market groups — pre-registration

Written and committed, with `src/forex_ai_analyst/lab/hundred_study.py`, **before** it is run.

**Why.** The owner asked for the best strategies anywhere, 100+ tested, the best ten kept. Sources: the rule
families of Kakushadze and Serur, *151 Trading Strategies* (2018: moving averages, channels, support and
resistance, momentum, mean reversion, FX, commodities, futures), Brock, Lakonishok and LeBaron (1992),
the Turtle rules, Connors' short-term rules, Moskowitz, Ooi and Pedersen's time-series momentum (2012),
calendar anomalies, and the standard indicator rules (MACD, ADX/DI, SuperTrend, Parabolic SAR, Ichimoku,
Aroon, Heikin-Ashi, Keltner, Bollinger, NR7/inside-day, 52-week high). Textbook parameters, no grid.

**Trials.** 40 rules x 4 groups = 160 rule-group trials, on top of the ~50 earlier studies in docs/. With
this many trials several will look good by luck, hence:

**Protocol.**
- Development years: CRYPTO 2021-2023, FXM 2010-2017, IDX and COMM 2004-2013. Holdout: the later years.
- A rule-group qualifies on development if: t-statistic of mean trade R >= 2.0, at least 100 trades,
  PF >= 1.15, at least 60% of its markets positive. At most the 25 best by t-statistic go on.
- Holdout, run once for those: passes if mean R > 0, month-bootstrap P >= 0.95, PF >= 1.15, at least 55%
  of markets positive, at least 50 trades. The top ten passes by holdout t-statistic are the result.

**Costs.** Per side: crypto 0.07% (taker + slippage) plus real funding; FX 0.01%, gold +0.02%, silver
+0.04%; indices 0.03%; commodities 0.05%. Overnight financing on longs: indices 4% a year, commodities 3%.
All entries at the next open; a 3 ATR(14) protective stop unless the rule has its own exit. Indices long
only; calendar rules long only.

## Result (2026-10-31) — none of the 160 passes the holdout

**Development:** 25 of 160 rule-group trials qualified: 18 crypto trend rules, 5 index rules (three-down,
Halloween, double seven, Connors RSI(2), price above SMA200, linear-regression slope), crypto turn-of-month,
commodity Halloween. No FX or metal rule qualified (as in every earlier FX study).

**Holdout (once):** **0 pass** the pre-registered gate (month-bootstrap P >= 0.95 among others).

| Holdout | Trades | Mean R | PF | t | P | Markets + | Development t |
|---|---|---|---|---|---|---|---|
| price > SMA50, crypto | 2,125 | +0.070 | 1.36 | 3.07 | 0.92 | 74% | 2.88 |
| Parabolic SAR, crypto | 2,375 | +0.066 | 1.26 | 3.04 | 0.90 | 69% | 3.42 |
| Aroon, crypto | 2,873 | +0.076 | 1.29 | 2.97 | 0.75 | 71% | 4.80 |
| Bollinger breakout, crypto | 2,208 | +0.129 | 1.44 | 2.95 | 0.81 | 69% | 4.06 |
| Keltner breakout, crypto | 1,138 | +0.194 | 1.57 | 2.49 | 0.79 | 66% | 3.45 |
| Halloween, commodities | 252 | +0.174 | 1.53 | 2.47 | 0.88 | 71% | 5.84 |
| ... 13 more crypto trend rules | | +0.02 to +0.12 | 1.08-1.34 | 1.0-2.4 | 0.6-0.84 | | |
| index RSI(2) / three-down / double seven | ~1,500 each | +0.02 | 1.06-1.12 | 1.2-1.7 | 0.68-0.83 | | 3.4-4.8 |
| Heikin-Ashi, crypto | 8,914 | -0.010 | 0.93 | -1.54 | 0.31 | 31% | 4.19 |
| **turn-of-month, crypto** | 1,984 | **-0.049** | 0.78 | **-3.85** | 0.27 | 18% | **9.21** |

What the holdout says:
- **Crypto trend following is real but not new.** 19 of the 20 crypto trend rules stay positive in
  2024-2026 (PF 1.1-1.6), but each daily variant is weaker than on 2021-2023 and none is significant month by
  month over 32 months. They are versions of the effect the bots already trade (Donchian 4h, PF 1.85 live
  universe), so they add no new engine.
- **The strongest development result was luck.** Crypto turn-of-month (t 9.2 on 2021-2023) loses money in
  2024-2026 (t -3.9): the textbook case for why the holdout exists.
- **Index short-term mean reversion has faded** since 2014: still positive (+0.02R) but small after costs;
  the live IDX1 engine is the stronger version of it (daily, 3 ATR stop, older sample PF 1.34).
- **FX and metals:** no daily rule qualified.

Nothing is added to the bots. Including the earlier studies, about 210 strategies have now been tested; the
ones that pass are the engines already running (crypto Donchian, crypto rebound, gold/silver H4, commodity
trend, index pullback, pre-FOMC, funding carry pending).
