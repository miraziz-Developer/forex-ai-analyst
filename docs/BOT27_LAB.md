# The owner's BOT 27 v3 "Quantum Scalper" in the lab — pre-registration

Written and committed, with `src/forex_ai_analyst/forex/bot27_lab.py`, **before** it is run.

**Why.** The bot runs on the FBS demo since 2026-10-22: 14 trades to 2026-10-30, 9 won, +3.7R (+0.26R a
trade, PF 1.69), 12 of 14 sells. Fourteen trades cannot tell skill from luck; two years of minute data can.

**Rule.** The bot's code, unchanged (entry, stop, spread filter, 0.7R breakeven to +0.08R, 60% closed at
1.0R, 2.2R target, EMA20 trail from 1.15R with 0.45R locked, Friday close, cooldown, per-setup veto).
Approximations, stated: EMAs run over the whole history (the bot recomputes them over its last 220-250 bars);
the bot checks every 2 seconds, the replay every minute with the stop checked before the target, breakeven
and the partial close; the 3-trade and per-currency caps do not bind with one position per pair.

**Data.** Dukascopy M1 bid/ask, EURUSD and GBPUSD, 2024-10-01..2026-09-30 (the only two of the bot's six
markets with minute data here; USDJPY, XAUUSD, AUDUSD and EURJPY could not be downloaded today). Costs: the
real spread, plus 0, 0.5 or 1.0 pip a round trip (FBS standard and cent accounts have no commission but
wider spreads than Dukascopy; **0.5 pip is the reference**).

**Evidence that it works (at 0.5 pip):** mean R > 0 with bootstrap P >= 0.95, PF >= 1.3, at least 100
trades, mean R > 0 in both pairs and in both buys and sells.
