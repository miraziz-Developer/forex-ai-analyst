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
