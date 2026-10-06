# FOMC statements: language tone and reaction drift — pre-registration

Written and committed, with its code (`src/forex_ai_analyst/forex/fomc_study.py`),
**before** it is run.

**Why.** Funds read central-bank language systematically; a statement that is
more hawkish than the previous one tends to support the currency, and
information in a long text may be absorbed over days rather than minutes.
Two ways to turn FOMC statements (federalreserve.gov, 2010-2026, scheduled and
unscheduled) into a dollar trade:

- **FT1 — tone change.** A fixed dictionary scores each statement, sentence by
  sentence: a hawkish noun group (inflation, prices, wages, growth, activity,
  economy, spending, demand, employment, job gains) next to an "up" word
  (rising, rose, increase, higher, high, elevated, strong, solid, robust,
  accelerating, expanding, firm, picked up, upward), or an unemployment noun
  group (unemployment, slack) next to a "down" word (falling, fell, declined,
  lower, low, weak, soft, slowed, moderated, eased, subdued, contracted,
  downward) counts +1 hawkish; the opposite pairings count +1 dovish. Tone =
  (hawkish - dovish) / (hawkish + dovish). Signal: tone minus the previous
  statement's tone; > 0 long USD, < 0 short USD, 0 no trade. The dictionary is
  fixed here and never tuned.
- **FT2 — reaction drift.** Direction of the USD basket from the 12:00 to the
  16:00 New York hourly open on statement day (covers every release time used
  since 2010); trade the same direction.

**Trade.** An equal-weight basket of the seven USD pairs (EURUSD, GBPUSD,
AUDUSD, NZDUSD, USDCAD, USDCHF, USDJPY), entered at the 16:00 New York hourly
open on statement day and exited at the 16:00 New York open two weekdays
later, at real Dukascopy bid/ask both ways plus 0.3 bp. One result per
statement (the basket's mean return).

**A variant passes only if:**
1. at least 100 statements traded;
2. mean net > 0 with event bootstrap P(mean > 0) >= **0.95**;
3. profit factor over events >= 1.2;
4. mean net > 0 in 2010-2017 and in 2018-2026.

## Result (2026-10-06) — neither variant passes

137 policy statements 2010-2026.

| Variant | Events | Hit | Mean net | PF | P(mean>0) | 2010-17 / 2018-26 | Verdict |
|---|---|---|---|---|---|---|---|
| FT1 tone change | 90 | 52.2% | +0.3 bp | 1.01 | 0.54 | -13.4 / +18.1 bp | FAIL |
| FT2 reaction drift | 136 | 56.6% | +3.7 bp | 1.14 | 0.73 | +11.6 / -3.4 bp | FAIL |

FT1 traded only 90 statements (identical tone to the previous statement, or
no scorable sentence, means no trade). Neither the dictionary tone nor the
first hours' reaction predicts the dollar over the next two days in a way that
survives this test. Nothing is built.
