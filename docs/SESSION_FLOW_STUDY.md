# FX session flows (US-hours dollar weakness) — pre-registration

Written and committed, with its code (`src/forex_ai_analyst/forex/session_study.py`),
**before** it is run.

**Why.** Every daily price pattern on currency pairs failed (docs/*_STUDY.md,
including after the 2026-10-06 data fix). An untested structural idea is
intraday: a currency tends to **depreciate during its own home trading hours**,
when domestic firms and funds buy foreign currency, and recover outside them
(Ranaldo 2009, "Segmentation and time-of-day patterns in foreign exchange
markets"; Breedon and Ranaldo 2013, "Intraday patterns in FX returns and order
flow"). For the dollar this means weakness during US hours and strength outside
them. The effect is small, so the question is whether it survives retail costs.

**Data.** Yahoo hourly bars, 2023-12-20 to 2026-10-06 (about 2.8 years; all that
Yahoo serves), seven USD pairs: EURUSD, GBPUSD, AUDUSD, NZDUSD, USDCAD, USDCHF,
USDJPY. Times are UTC, fixed (no daylight-saving adjustment).

**Two variants (two trials):**
- **SF1 — US hours.** Every weekday, short USD from the open of the 12:00 UTC bar
  to the open of the 20:00 UTC bar (8 hours).
- **SF2 — both sides.** SF1 plus long USD from the open of the 20:00 UTC bar to
  the open of the next weekday's 12:00 UTC bar (none over the weekend). These
  trades cross the daily rollover, when spreads widen, so they pay 1.5x cost.

Short USD = sell XXXUSD or buy USDXXX. A day is skipped if either bar is missing.

**Costs.** The round-trip costs used in all forex studies (`data.MARKETS`,
e.g. EURUSD 1.2 pips, USDJPY 1.4 pips); no swap (SF1 is intraday; SF2 is
charged the extra 0.5x instead).

**A variant passes only if, pooled over the seven pairs:**
1. at least 1000 trades;
2. mean net return per trade > 0 with day-block bootstrap P(mean > 0) >= **0.95**;
3. profit factor >= 1.2;
4. mean net > 0 in both halves (before / from 2025-05-01);
5. at least 5 of 7 pairs with a positive total.

If one passes, the next step is validation on longer broker history with real
spreads (`mt5_export.py` on the VM) before any bot. If neither passes, the idea
is recorded as failed.
