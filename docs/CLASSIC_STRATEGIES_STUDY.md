# Three "professional" chart strategies (broker academy page) — pre-registration

Written and committed, with `src/forex_ai_analyst/forex/classic_study.py`, **before** it is run.

**Source.** A broker-sponsored academy article the owner shared, "3 Professional Forex Trading Strategies
That Work" (trendlines, support and resistance, pivot point break; September 2021). Its rules are written
for a person drawing lines; here every line and level is built only from bars already closed.

**Data and costs.** Dukascopy hourly bid/ask, 2010-01 to 2026-09; EURUSD, GBPUSD, USDJPY, USDCHF, AUDUSD,
NZDUSD, USDCAD, EURJPY, GBPJPY, EURGBP, XAUUSD, XAGUSD. Signals on mid prices; longs buy the ask and sell
the bid. Real spread plus 0.7 pip per round trip (gold 0.02%, silver 0.05%). Inside an hour the stop is
assumed to come before the target. Results in R (loss at the initial stop).

**1. Pivot point break (the article's table).** Floor pivots from the previous forex day (ending 17:00
New York): PP = (H+L+C)/3, R1 = 2PP-L, S1 = 2PP-H, R2 = PP+(H-L), S2 = PP-(H-L). A day that opens below PP
arms a buy stop 2 pips above PP (gold 0.5, silver 0.02); stop 2 pips below S1; target R2; once R1 trades,
the stop moves to PP (from the next hour). One trade a day, closed at the day's last hour. Mirrored for a
day that opens above PP.

**2. Support and resistance with the trend.** Daily trend = previous forex day's close vs its 50-day
average. Support / resistance = the last known H1 swing low / high (a swing has five lower highs on each
side and is known five hours later). Uptrend: buy a bounce (the hour's low within a quarter ATR(14) of
support and the close above it) or a close breaking above resistance; stop a quarter ATR below support;
target 2:1 (the article's recommendation). Downtrend mirrored. Entry at the next hour's open.

**3. Trendline bounce.** Rising line through the last two known swing lows (newer one higher), valid while
no hourly close has been below it since the newer low. Buy when an hour's low comes within a quarter ATR of
the line and the hour closes above it; stop a quarter ATR below the line; target 2:1. Falling lines through
lower swing highs mirrored. (The breakout of such lines was tested in TRENDLINE_STUDY.md: FAIL.)

One position per market and strategy. Nothing is tuned, so 2010-2026 is one out-of-sample test, also shown
in halves (split 2018-07-01).

**Each strategy passes only if:** at least 300 trades; mean net R > 0 with month-bootstrap P(mean > 0) >=
0.95; profit factor >= 1.2; at least 8 of 12 markets positive; both halves positive.

## Result (2026-10-07) — all three FAIL

| Strategy | Trades | Hit | Gross R / trade | Net R / trade | PF | P(mean>0) | 2010-18 / 2018-26 | Markets + |
|---|---|---|---|---|---|---|---|---|
| Pivot point break | 39,927 | 34.6% | -0.109 | -0.128 | 0.68 | 0.00 | -0.101 / -0.155 | 0/12 |
| Support / resistance with trend | 16,928 | 29.3% | -0.125 | -0.177 | 0.76 | 0.00 | -0.166 / -0.187 | 0/12 |
| Trendline bounce | 37,323 | 27.1% | -0.191 | -0.270 | 0.66 | 0.00 | -0.259 / -0.282 | 0/12 |

**Robustness.** Inside an hour the stop was assumed to come first. With the opposite, optimistic assumption
(target first) all three still lose: pivot -0.125R (PF 0.69), support/resistance -0.145R (PF 0.80),
trendline bounce -0.213R (PF 0.73). The losses are present before costs and in every market and both halves.
Automated, the levels these rules trade are touched and broken far more often than the article's chosen
examples suggest. Nothing is built.
