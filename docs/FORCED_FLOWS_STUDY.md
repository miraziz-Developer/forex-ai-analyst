# Forced-flow FX study — pre-registration

Written and committed, with its code (`src/forex_ai_analyst/forex/forced_flows.py`),
**before** any of these tests is run.

**Idea.** Instead of reading charts, find moments when someone must trade
regardless of price, and measure what the currency does then. Three flows with
an economic reason; each is tested alone first. Only flows that pass may later
be combined into a multi-factor score.

**1. Gotobi (USDJPY).** Japanese importers settle on the 5th, 10th, 15th, 20th,
25th and last day of the month (weekends roll back to Friday) and buy USD at the
Tokyo 09:55 fix. Trade: long USDJPY from the open of the 23:00 UTC bar (08:00
JST) to the close of the 00:00 UTC bar (10:00 JST). Yahoo H1, about 2.8 years.
Cost 1.4 pips round trip. Control: the same window on all other weekdays.

**2. Month-end hedge rebalancing (USD basket).** When US stocks beat foreign
stocks (average of DAX, FTSE, Nikkei in local currency) from the previous month
end to three trading days before this month end, foreign investors' USD hedges
must grow, so they sell USD into the month-end fix. Trade: sell USD against an
equal-weight basket of EUR, GBP, AUD, NZD, JPY, CAD, CHF from the close three
trading days before month end to the last close of the month; buy USD when US
stocks lagged. Yahoo daily from 2004. Cost 0.015% round trip.

**3. Risk-off carry unwind (AUDJPY).** A VIX rise of at least 20% in one day
forces leveraged carry positions to close. Trade: short AUDJPY from the next FX
day's open for five FX days, one trade at a time. Yahoo daily from 2004. Cost
0.028% round trip.

**Each flow passes only if, net of cost:**
1. enough events (gotobi >= 100, month end >= 150, risk-off >= 40);
2. bootstrap P(mean net > 0) >= 0.90;
3. mean net return positive in both chronological halves;
4. gotobi only: the gotobi-day mean is above the same window on other days.

Three tests are run; a pass at P >= 0.90 can happen by chance about once in
ten, so a passing flow still needs confirmation on longer MT5 data before any
trading.

## Result (2026-10-06) — no flow passes

| Flow | Events | Mean net / event | Win | P(mean>0) | Halves | Verdict |
|---|---|---|---|---|---|---|
| Gotobi USDJPY | 190 | -0.016% (gross -0.006%; other days +0.008%) | 46% | 0.10 | -0.013% / -0.018% | FAIL |
| Month-end USD | 232 | +0.061% | 53% | 0.88 | -0.053% / +0.175% | FAIL |
| Risk-off AUDJPY | 95 | +0.010% | 45% | 0.51 | -0.043% / +0.062% | FAIL |

- Gotobi: in 2023-2026 USDJPY did not rise into the Tokyo fix on gotobi days
  (it did slightly better on other days); the flow is either absent or traded
  ahead of by banks.
- Month-end: the only promising flow. Positive overall and clearly positive in
  the later half (about 2015-2026), negative in 2004-2015. It misses both the
  P >= 0.90 and the both-halves gate, so it is not proven; it could have
  strengthened as foreign holdings of US stocks and hedge ratios grew, or it
  could be luck.
- Risk-off: no reliable follow-through after VIX spikes; the move happens on
  the spike day itself.

**Decision.** Nothing is tradable. The month-end flow is a candidate only for a
new, pre-registered test on data not used here (for example a forward test from
2026-11, about 12 events a year).
