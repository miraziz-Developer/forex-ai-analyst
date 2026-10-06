# Regime-switching multi-strategy system — pre-registration

Written and committed, with its code (`src/forex_ai_analyst/forex/regime_system.py`,
`regime_system_study.py`) and look-ahead tests, **before** the study is run.

**Idea.** Trend, range and breakout rules each lose when used everywhere. A
classifier decides the regime first, and each module trades only in its own
regime and only when several conditions agree ("sniper" entries).

**Regime (closed bar, four measurements):**
- TREND: ADX(14) > 25 and Kaufman efficiency ratio(20) > 0.30.
- SQUEEZE: ATR(14)/ATR(100) < 0.70 (volatility compressed).
- RANGE: ADX < 20, efficiency ratio < 0.20 and ATR ratio < 1.0.
- otherwise NONE (no new trades).

**Modules:**
- Trend: TREND regime, EMA50/EMA200 and the 10-bar slope of EMA100 agree,
  bar touched within 1 ATR of EMA20 (pullback), close beyond the previous bar's
  high/low in the trend direction. Stop 2 ATR, 3 ATR trailing stop, exit on a
  close through the opposite 20-bar channel.
- Range: RANGE regime, previous bar closed outside Bollinger(20, 2) with RSI(2)
  < 10 / > 90, this bar closes back inside. Target the middle band, stop 1.5
  ATR beyond the two-bar extreme, at most 10 bars.
- Breakout: SQUEEZE on at least 5 of the previous 10 bars, then a close beyond
  the prior 20-bar channel on a bar wider than 1.2 ATR. Stop 2 ATR, 3 ATR
  trail, 20-bar channel exit.

Parameters are textbook defaults chosen before testing; none is tuned.

**Markets and data.** EURUSD, GBPUSD, AUDUSD, NZDUSD, USDCAD, USDCHF, USDJPY,
EURJPY, GBPJPY and XAUUSD with the forex study's costs. D1 from 2004 is the
decision; H4 (2.8 years) is reported.

**The system (all modules pooled, D1) passes only if:** at least 100 trades;
bootstrap P(mean R > 0) >= 0.90; profit factor >= 1.2; mean R > 0 in both
halves; at least 60% of markets with positive total R. Each module is reported
with the same metrics. If it passes, an MT5 trader is built for it.

## Result

(pending)
