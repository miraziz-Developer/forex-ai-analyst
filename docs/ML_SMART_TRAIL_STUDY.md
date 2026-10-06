# FX ML v2c — structure-based "smart" trailing stop — pre-registration

Written and committed, with its code (`ml_exits.py --smart`), **before** it is run.

The owner's idea: a trailing stop that follows market structure instead of a
fixed distance, rises behind the trade as it climbs, and is **not** knocked out
by a false break of a range (a wick that sweeps a low and comes back). The
plain 1.5 ATR trailing stop failed (docs/ML_EXITS_STUDY.md) because ordinary
noise stopped trades out.

Same v2c walk-forward trades (2010-2026), replayed on daily bars. Both variants
keep everything the demo bot now does — 3 ATR intraday hard stop, 3 ATR
take-profit, time exit after 5 days — and add a **soft stop**:

- **Structure level.** A swing low is a daily low lower than the two lows before
  and the two after it; it is known only after those two later bars have
  closed. The level is the most recent known swing low minus 0.5 ATR(14) (swing
  high plus 0.5 ATR for shorts), used only while it is below the current close
  (above for shorts). The soft stop only ever moves in the trade's favour.
- **Close-confirmed.** The soft stop fires only on a daily **close** beyond it,
  and the trade exits at that close. A wick through the level that closes back
  inside (a liquidity sweep / false break) does not exit.

- **SM1 — structure trail from entry.**
- **SM2 — profit lock.** Inactive until a daily close is at least 1 ATR in
  profit; from then the soft stop is the better of entry (break-even) and the
  structure level.

**Decision.** The reference is T3 (what v2c runs now). A variant replaces it only
if its mean net per trade beats T3 with paired day-block bootstrap
P(variant - T3 > 0) >= **0.95**, and neither 2010-2017 nor 2018+ gets worse.
Hit rate is reported but is not a criterion: a higher win rate with a lower
mean is a worse strategy. Trials on this data: 2 more.

## Result (2026-10-06) — neither variant passes; v2c keeps T3

| Variant | Hit | Mean net | PF | P(mean>0) | 2010-17 / 2018+ | P(better than T3) | Verdict |
|---|---|---|---|---|---|---|---|
| T3 (reference) | 52.3% | +0.054% | 1.10 | 0.95 | +0.019% / +0.131% | — | — |
| SM1 structure trail | 49.9% | +0.055% | 1.11 | 0.96 | +0.024% / +0.125% | 0.57 | FAIL |
| SM2 profit lock | 52.0% | +0.053% | 1.10 | 0.94 | +0.018% / +0.129% | 0.23 | FAIL |

- Ignoring wicks works as intended, but over a 5-day hold structure exits mostly
  swap one outcome for another: SM1 cuts some losers early yet also exits
  trades that would have recovered, so the hit rate falls to 49.9% and 2018+
  gets worse; the mean difference is noise (P 0.57).
- The break-even lock (SM2) did not raise the win rate: trades that touch +1 ATR
  and come back to entry would mostly have closed in profit at day 5.
- With a 5-day time exit and a 3 ATR take-profit already in place, there is
  little left for a trailing stop to capture.
