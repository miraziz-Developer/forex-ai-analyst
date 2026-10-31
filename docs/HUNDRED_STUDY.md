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
