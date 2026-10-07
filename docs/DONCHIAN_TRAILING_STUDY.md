# Live Donchian 4h with an ATR trailing stop — pre-registration

Written and committed, with its code (`src/forex_ai_analyst/lab/trailing_study.py`),
**before** it is run.

**Why.** The owner asked whether a "smart" trailing stop would do better. The
live exit (a 4h close below the 20-bar low) already trails structure; a
chandelier stop trails the highest high by a fixed ATR multiple and reacts
faster. Partial take-profit was rejected before (docs/DONCHIAN_PARTIAL_EXIT.md).

**Variants (two trials)** on the twenty live markets, 2021-01..2026-09, lab
engine defaults (fees, slippage, funding), entry and initial 3 ATR stop
unchanged, 20-bar channel exit kept:
- **T1** chandelier trail at 3 ATR(14) below the highest high since entry;
- **T2** the same at 2 ATR.

**Adopted only if** its daily-return Sharpe beats the live rule in both
2021-2023 and 2024-2026 and over the full period, and its profit factor is not
lower. Otherwise the live exit stays.

## Result (2026-10-06) — neither trail is adopted

| Exit | Sharpe | 2021-23 | 2024-26 | Win | Mean R | PF |
|---|---|---|---|---|---|---|
| **live (20-bar channel)** | **1.13** | **1.29** | **0.94** | 31.4% | **+0.51** | **1.85** |
| T1 + 3 ATR chandelier | 0.54 | 0.64 | 0.45 | 35.5% | +0.08 | 1.21 |
| T2 + 2 ATR chandelier | 0.28 | 0.09 | 0.44 | 34.4% | +0.02 | 1.08 |

A tighter trail raises the hit rate a little but cuts the large trends that
make the strategy: mean R falls from +0.51 to +0.08 and +0.02. The live exit
stays.

## Second round: fixed take-profit and wider trails — pre-registration (2026-10-08)

The owner watches Donchian trades go into profit and come back to a loss before the exit channel closes
them, and asks for a take-profit or a trailing stop. The 2 and 3 ATR trails failed (above). Not yet tested,
on the **thirty** live markets, 2021-01..2026-09, costs and funding as live:
- `TP2R`, `TP3R`, `TP5R`: take profit at 2, 3 or 5 times the 3 ATR stop distance (the exit channel and the
  stop still apply);
- `T4_trail_4atr`, `T5_trail_5atr`: chandelier trail 4 or 5 ATR(14) below the highest high since entry.

Code `src/forex_ai_analyst/lab/exit_study2.py`, committed before it is run. Five more trials.
**A variant replaces the live exit only if** its Sharpe beats the live rule over the full period and in
both halves (2021-2023, 2024-2026) and its profit factor is not lower. Win rate is reported but is not the
criterion: a rule can win more often and earn less.

### Second-round result (2026-10-08) — none adopted; the live exit stays

| Exit | Sharpe | 2021-23 / 2024-26 | Win | Mean R | PF | Avg win / loss | Best trade |
|---|---|---|---|---|---|---|---|
| **live (stop + 20-bar exit)** | **1.10** | 1.08 / 1.14 | 31.8% | **+0.449** | **1.77** | **+3.26** / -0.86 | **+73.4R** |
| TP 2R | 0.69 | 0.66 / 0.72 | 37.5% | +0.111 | 1.21 | +1.75 / -0.87 | +3.7R |
| TP 3R | 0.74 | 0.70 / 0.79 | 33.9% | +0.154 | 1.27 | +2.13 / -0.86 | +5.3R |
| TP 5R | 0.96 | 1.01 / 0.89 | 32.1% | +0.245 | 1.42 | +2.57 / -0.86 | +8.5R |
| trail 4 ATR | 0.67 | 0.64 / 0.71 | 34.0% | +0.156 | 1.33 | +1.87 / -0.73 | +20.5R |
| trail 5 ATR | 0.96 | 0.80 / 1.12 | 31.8% | +0.330 | 1.61 | +2.75 / -0.80 | +84.3R |

Every take-profit and every trail earns less. The strategy lives on a few long trends (average winner
+3.3R, best +73R); a take-profit cuts exactly those, and a trail tight enough to keep the small give-backs
also stops out the big trends on their normal pullbacks. Giving back part of an open profit, and letting
some trades that were up end as small losses, is the price of the trends that pay for everything. Seven
exit variants have now failed against the 20-bar exit (two trails in round one, five here).
