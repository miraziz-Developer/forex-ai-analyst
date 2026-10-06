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
