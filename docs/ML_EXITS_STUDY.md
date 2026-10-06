# FX ML v2c — take-profit and trailing exits — pre-registration

Written and committed, with its code (`ml_exits.py`), **before** it is run.

The owner asked why the demo bot has no take-profit: a trade can be well in
profit in mid-week and give it all back by the 5-day exit. The question is
answered with data, not by intuition, on the walk-forward trades of the model
that runs on the demo as v2c (v1 features + COT, logistic C = 0.1, 2010-2026,
yearly retraining, label purge, same costs).

Each trade is replayed on daily bars from the entry open to the close of the
5th trading day. Daily bars do not show the order of high and low, so a day
that touches both the stop and the target counts as the stop (pessimistic). A
gap through a level fills at the open.

- **S — reference.** 3 ATR(14) stop, time exit after 5 days: what the demo bot
  actually does.
- **T15 — take-profit 1.5 ATR** plus the S stop.
- **T3 — take-profit 3 ATR** (1:1 with the stop) plus the S stop.
- **TR — trailing.** S stop; after a close at least 1 ATR in profit the stop
  moves to entry; from then on it trails 1.5 ATR behind the best close.

**Decision.** A variant replaces S on the demo only if, over the same trades,
its mean net per trade beats S with paired day-block bootstrap
P(variant - S > 0) >= **0.95** (three trials at once), and it does not make
2010-2017 or 2018+ worse. Otherwise the bot keeps the time exit. The S row also
reports how much the 3 ATR stop itself changes the original study result.
