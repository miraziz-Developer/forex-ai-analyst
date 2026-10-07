# Quiet-market range rule on the MT5 indices and metals (H1) — pre-registration

Written and committed, with `src/forex_ai_analyst/forex/quiet_range_study.py`, **before** it is run.

**Why.** The owner wants trades in quiet weeks, when the trend engines (Donchian) and the crash rebound
wait. Indices are the one MT5 market where buying dips has passed a test (IDX1, daily, PF 1.34, 68% win).
This asks whether the same dip-buying works on hourly bars in calm markets, where prices tend to swing
around their average. (On FX pairs every fade failed, docs/SCALP_MULTI_STUDY.md; indices and metals are
tested here, not FX.)

**Data and costs.** Dukascopy hourly bid/ask, 2013-01..2026-09: US500, NAS100, US30, GER40, UK100, JP225,
XAUUSD, XAGUSD. Signals on mid prices; buys at the ask, sells at the bid; inside an hour the stop is checked
first. Extra cost 0.02% per round trip on top of the real spread (silver 0.05%). Results in R (loss at the
stop). Overnight swap is not charged (trades last at most 24 hours; stated).

**Rule QR1.** Daily context from completed UTC days: the previous day's close above its 200-day average
(uptrend) and the previous day's 20-day realized volatility at or below the median of the 250 values before
it (quiet regime). In that state, an hourly close below the 20-hour, 2.5-SD Bollinger band buys at the next
open; out at the next open after a close back at the middle band, or after 24 hours; stop 3 ATR(14) below
the entry. Long only, one position per market.
**Rule QR2.** QR1 without the quiet-regime filter (to see whether calm matters).

**Protocol.** Development = 2013-2019: both run; the one with the higher t-statistic of mean R is chosen,
and the holdout is spent only if it has t >= 2.0 and at least 300 trades. Holdout = 2020-01..2026-09, once.
**Passes only if:** at least 300 trades; mean R > 0 with month-bootstrap P >= 0.95; PF >= 1.2; at least 5
of 8 markets positive; mean R > 0 in 2020-01..2023-03 and in 2023-04..2026-09. A pass becomes an MT5 demo
engine; trades per month are reported.
