# Turn-of-the-month in stock indices — pre-registration

Written and committed, with its code (`src/forex_ai_analyst/forex/tom_study.py`),
**before** it is run.

**Why.** Much of the equity premium is earned around the turn of the month
(Ariel 1987; Lakonishok and Smidt 1988; McConnell and Xu 2008), when salaries,
pension contributions and savings plans are invested on fixed dates. A forced,
calendar-driven flow, the kind of reason this project requires.

**Rule.** Long the index from the close of the second-to-last trading day of
each month to the close of the third trading day of the next month (four
trading days in the market), every month, no other filter. Close-to-close
(Yahoo opens are unreliable for some indices). Costs: 0.03% round trip plus 6%
a year swap per calendar day held, as in docs/INDEX_STUDY.md.

**Two samples, both must pass.**
- Main: US500, US30, USTEC, DE40, UK100, JP225 (2004-2026).
- Replication: AUS200, HK50, FRA40, EU50, ESP35, NL25, SWI20, CAN60.

**Each sample passes only if:** at least 250 trades; P(mean > 0) >= 0.95 (block
bootstrap by month); profit factor >= 1.3; mean > 0 in 2004-2014 and 2015-2026;
at least two thirds of its indices positive; and mean return per day in the
market at least 2x the index's average daily drift over the same years (so the
rule earns more than simply holding). If both pass, it becomes the fourth
engine on the MT5 demo.

## Result (2026-10-06) — fails in both samples

| Sample | Trades | Win | Mean | PF | P | 2004-14 / 2015-26 | Positive | Net/day vs drift/day |
|---|---|---|---|---|---|---|---|---|
| main (6) | 1638 | 53.8% | +0.062% | 1.07 | 0.70 | +0.088% / +0.038% | 5/6 | 0.016% vs 0.041% |
| replication (8) | 2144 | 53.4% | +0.052% | 1.06 | 0.67 | +0.158% / -0.044% | 5/8 | 0.013% vs 0.024% |

The turn of the month earns less per day than simply holding the indices: the
effect documented up to the 2000s is gone after costs. Not adopted.
