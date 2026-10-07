# Trendline breakout (the classic chart-book rule), automated — pre-registration

Written and committed, with its code (`src/forex_ai_analyst/forex/trendline_study.py`),
**before** it is run.

**Why.** The owner showed a popular USDCHF H1 chart: a falling trendline drawn
through lower highs is broken upward -> buy, stop below the swing low, take
profit at the level where the line started (and the mirror image for sells).
Hand-drawn examples are chosen after the fact; here the lines are drawn only
from information available at each bar.

**Data.** Dukascopy hourly bid/ask 2010-2026, ten pairs (EURUSD, GBPUSD,
USDJPY, AUDUSD, USDCAD, USDCHF, NZDUSD, EURJPY, GBPJPY, EURGBP); signals on
mid prices, fills on the real bid/ask plus 0.3 bp.

**Rule (H1).**
- Swing high: a high above the five highs before it and not below the five
  after it; known five bars later (swing lows mirrored).
- Buy line: through the last two known swing highs when the newer is lower
  (falling line). Buy when an hourly close crosses above the line (previous
  close at or below it). Stop: the lowest low since the newer swing high.
  Target: the older swing high's price. Each line trades once.
- Sell: mirrored with the last two rising swing lows.
- Enter at the next bar's open; exit at the stop (checked first), the target,
  or the open 120 bars later. One position per pair.

**Passes only if:** at least 500 trades; day-bootstrap P(mean > 0) >= 0.95;
PF >= 1.2; mean > 0 in 2010-2017 and 2018-2026; at least 6 of 10 pairs
positive.

## Result (2026-10-07) — FAIL on every pair

| Trades | Win | Mean | Mean R | PF | P | 2010-17 / 2018-26 | Pairs + |
|---|---|---|---|---|---|---|---|
| 23476 | 40.1% | -1.3 bp | -0.05 | 0.92 | 0.00 | -1.5 / -1.2 bp | 0/10 |

Per pair (total %): EURUSD -21.5, GBPUSD -21.6, USDJPY -1.6, AUDUSD -35.9,
USDCAD -27.4, USDCHF -29.9, NZDUSD -61.1, EURJPY -19.4, GBPJPY -49.0, EURGBP
-46.7. Drawn without hindsight, trendline breaks win 40% of the time with a
target not far enough to pay for the losers and the spread. The textbook
pictures show the breaks that worked.
