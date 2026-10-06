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

## Result (2026-10-06) — the system fails on D1 and H4

| TF | Module | Trades | Per month | Win | Mean R | PF | P | Markets + | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| D1 | trend | 255 | 1.0 | 33% | -0.08 | 0.85 | 0.20 | 4/10 | FAIL |
| D1 | range | 297 | 1.1 | **63%** | -0.01 | 0.97 | 0.40 | 5/10 | FAIL |
| D1 | breakout | 16 | 0.1 | 25% | -0.25 | 0.56 | 0.14 | 0/4 | FAIL |
| D1 | **combined** | 568 | 2.2 | 48% | **-0.05** | **0.88** | 0.15 | 5/10 | **FAIL** |
| H4 | combined | 538 | 16.9 | 42% | -0.05 | 0.89 | 0.19 | 4/10 | FAIL |

- The range module is the "sniper" the owner asked for: about 63% of its
  trades win. It still loses slightly, because its winners (back to the middle
  band) are smaller than its losers. Precision alone does not make money.
- The regime filter did not turn losing modules into winning ones: trend
  entries filtered by ADX/efficiency still lose on currencies, and breakouts
  after compression are rare and negative.

**Decision.** Not approved; no MT5 trader is built. Tuning thresholds until a
backtest looks good is exactly what this study design forbids.
