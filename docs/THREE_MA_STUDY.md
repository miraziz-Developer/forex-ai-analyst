# Three moving averages (SMA 9/14/21) on H4 — pre-registration

Written and committed, with `src/forex_ai_analyst/forex/three_ma_study.py`, **before** it is run.

**Source.** A chart the owner shared (USDCHF H4, a broker's education page): SMA 21, 14 and 9 with
"buy/sell" where they cross and "X" exits where they cross back. The chart's lines are set to offsets
-2, -3 and -4: each line is drawn 2-4 bars to the left, so every point uses prices that come 2-4 bars
later. On the chart the crosses therefore sit right at the tops and bottoms; in real time they cannot.
This study computes the averages only from bars already closed.

**Rule.** H4 bars (four trading hours, aligned to 00/04/08/12/16/20 UTC) from Dukascopy hourly bid/ask,
mid closes for the averages. When SMA 9 > SMA 14 > SMA 21 holds at a bar's close and did not at the
previous close, buy at the next bar's open (ask); close at the next open after SMA 9 closes below SMA 14
(bid). Sells mirrored. One position per market; no stop (the chart shows none).

**Markets and period.** EURUSD, GBPUSD, USDJPY, USDCHF, AUDUSD, NZDUSD, USDCAD, EURJPY, GBPJPY, EURGBP,
XAUUSD, XAGUSD; 2010-01 to 2026-09. The rule is taken as given (nothing is tuned), so the whole period is
one out-of-sample test; it is also reported in two halves (split 2018-07-01).

**Costs.** Real spread at entry and exit, plus 0.7 pip per round trip (FX), 0.02% (gold), 0.05% (silver).
Swap is not charged (stated; positions last days).

**Passes only if:** at least 300 trades; mean net > 0 with month-bootstrap P(mean > 0) >= 0.95; profit
factor >= 1.2; at least 8 of 12 markets positive; both halves positive.
