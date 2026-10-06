# FX ML H1 — universal intraday model on real bid/ask data — pre-registration

Written and committed, with its code (`src/forex_ai_analyst/forex/h1_ml_study.py`),
**before** it is run.

**Why.** The daily models (v1/v2/v2c/v3) have no edge on 5-day moves. Short
horizons carry more structure (order-flow pressure, session effects, short-term
reversal) but costs weigh much more, so they can only be judged on data with
the real spread. Dukascopy's hourly bid and ask candles (2010-2026) give that.

**Markets (ten, no picking afterwards).** EURUSD, GBPUSD, USDJPY, AUDUSD,
USDCAD, USDCHF, NZDUSD, EURJPY, GBPJPY, EURGBP.

**Decisions.** At the close of every 4th hourly bar (00, 04, 08, 12, 16, 20 UTC
closes) on weekdays. Each decision uses only bars that have closed.

**Features** (per market and decision): log mid returns over 1, 4, 12, 24, 72
and 120 hours; realised volatility over 24 and 120 hours and their ratio;
position of the close in the last 24 hours' range; distance from the 24- and
120-hour EMAs in units of 24-hour volatility; 14-hour RSI; the current spread
relative to its 120-hour median; a dollar-index move (average signed USD-leg
return of the seven USD pairs) over 4 and 24 hours; hour-of-day and weekday
indicators; market indicator.

**Label.** Direction of the mid price from the next bar's open to the open four
hours later.

**Trading.** P(up) >= 0.55 buys at the ask, <= 0.45 sells at the bid, at the
next bar's open; exit at the open four hours later at the opposite side of the
quote (the real spread both ways) plus 0.3 basis points of retail markup. A
decision whose exit would cross a gap in the data (weekend, missing hours) is
skipped.

**Models (two trials):** logistic regression (C = 0.1) and gradient boosting
(depth 3, 200 trees, learning rate 0.05). Walk-forward: retrained every
January on all rows whose exit lies before that year; test years 2014-2026.

**A model passes only if:**
1. at least 1000 trades;
2. mean net > 0 with day-block bootstrap P(mean > 0) >= **0.95**;
3. profit factor >= 1.2;
4. mean net > 0 in 2014-2019 and in 2020-2026;
5. at least 6 of the 10 markets with a positive total.

**Diagnostic (not a gate):** hit rate and mean net by confidence band
(0.55-0.57, 0.57-0.60, 0.60-0.65, 0.65+), to see whether the model can tell a
strong signal from a weak one.

A passing model becomes an MT5 demo candidate with retraining built in. If
neither passes, the result is recorded.
