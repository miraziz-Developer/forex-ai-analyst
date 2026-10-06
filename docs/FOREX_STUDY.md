# Forex strategy study — pre-registration

Written and committed, with the code (`src/forex_ai_analyst/forex/`), **before**
the study is run.

**Question.** Which rules make money on forex, gold and BTC CFDs after costs?
The owner's three MT5 bots win about 30% of trades; they are tested as written
next to two trend-following rules that work on crypto.

| Rule | Timeframe | Source |
|---|---|---|
| ema_pullback_h1 | H1 | owner's bot 1 (TrendPro): EMA50/200 alignment, close within 0.9 ATR of EMA50, SL 1.5 ATR, TP 3 ATR |
| pinbar_snr_h1 | H1 | owner's bot 2 (SnR Pro): pin bar at 38-bar high/low (0.1%), SL 25 points beyond wick, TP 2.5R |
| ict_fvg_h1 | H1 | owner's bot 3 (ICT Pro): price back inside a fair value gap of the last 5 bars, SL 30 points beyond 6-bar extreme, TP 3R |
| donchian_h4 | H4 | live crypto rule, both directions: 100-bar breakout, 20-bar exit, 3 ATR stop |
| donchian_d1 | D1 | turtle system 2: 55-day breakout, 20-day exit, 2 ATR stop |
| ema_cross_d1 | D1 | EMA50/200 cross, 3 ATR trailing stop |

The original bots ran EUR/GBP on M15; Yahoo offers only 60 days of M15, so all
three are tested on H1 here. The analyzer can rerun them on M15 from MT5 data
on the VM.

**Data and costs.** Yahoo Finance: H1 about 2.8 years (2023-12..2026-10), D1
from 2004. Markets: EURUSD, GBPUSD, AUDUSD, NZDUSD, USDCAD, USDCHF, USDJPY,
EURJPY, GBPJPY, XAUUSD (gold futures as proxy), BTCUSD. Round-trip costs
(spread + commission): 1.2-2.0 pips majors, 1.4-3.0 pips JPY pairs, $0.40 gold,
0.10% BTC. Swaps are not modelled (relevant only for multi-day holds). Entry at
the next bar's open, stop first when stop and target touch in the same bar.

**A rule passes only if, pooled over all markets:**
1. at least 100 trades;
2. mean R > 0 with day-clustered bootstrap P(mean R > 0) >= 0.90;
3. profit factor >= 1.2;
4. mean R > 0 in both halves of its trades (chronological);
5. at least 60% of markets with positive total R.

Passing rules, with their positive markets, go to
`research_output/approved_strategies.json`; the MT5 trader trades nothing else.
No parameter is tuned after seeing results; any change is a new study.

## Result

(pending)
