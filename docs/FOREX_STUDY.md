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

## Result (2026-10-06) — no rule passes; approved list is empty

| Rule | Trades | Per month | Win rate | Avg win | Avg loss | Mean R | PF | Markets + | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| ema_pullback_h1 (bot 1) | 6693 | 202 | 32% | +1.91R | -1.10R | **-0.13** | 0.82 | 0/11 | FAIL |
| pinbar_snr_h1 (bot 2) | 10143 | 302 | 28% | +2.30R | -1.22R | **-0.23** | 0.74 | 0/11 | FAIL |
| ict_fvg_h1 (bot 3) | 3080 | 92 | 26% | +2.91R | -1.09R | -0.04 | 0.95 | 3/11 | FAIL |
| donchian_h4 | 494 | 15 | 33% | | | -0.08 | 0.86 | 3/11 | FAIL |
| donchian_d1 | 828 | 3.1 | 28% | +3.13R | -0.91R | +0.23 | 1.35 | 6/11 | FAIL (breadth 55% < 60%) |
| ema_cross_d1 | 381 | 1.4 | 34% | | | -0.02 | 0.94 | 4/11 | FAIL |

- The owner's three bots lose on every one of the eleven markets (bot 1 and 2)
  or nearly all (bot 3). Their ~30% live win rate matches the backtest; the
  wins are not large enough to pay for the losses and the costs of 90-300
  trades a month. Losses are not an M15-vs-H1 artefact: costs weigh even more
  on M15.
- donchian_d1 misses only the breadth gate, but its profit is concentrated:
  BTC +148.7R and gold +42.8R; the nine currency pairs together total about
  -4R (USDJPY +19, GBPUSD +8, EURUSD +4.5, USDCAD +4; NZD, CHF, EURJPY, GBPJPY,
  AUD negative). Daily trend following on currency pairs is about break-even
  after costs in 2004-2026; gold and BTC trend.

**Decision.** Nothing is approved; the MT5 trader has nothing to trade. Any
follow-up (for example trend following on gold, indices and crypto CFDs) is a
new pre-registered study that also pays for these six trials.

## Addendum (2026-10-06): the live crypto rule, long only — pre-registration

Written before running. The owner asked to test the exact live BingX rule
(Donchian 4h, 100/20, 3 ATR stop, **long only**) on the same eleven markets,
same data, costs and gates as above. Going long a currency pair has no
economic reason to differ from going short, so this is a seventh trial, not a
fix of donchian_h4.

### Result — FAIL

269 trades (8.2/month), win 35%, mean -0.04R, PF 0.92, P(mean > 0) 0.31, halves
-0.09 / +0.01, 4 of 11 markets positive. Currency pairs: GBPJPY -12.0R,
GBPUSD -6.2, NZDUSD -5.9, USDJPY -4.1, AUDUSD -3.8, EURJPY -3.1, USDCHF -2.7,
EURUSD +0.4, USDCAD +9.2. Gold +16.3R (17 trades), BTC +0.6R (2.8 years of H1
data). The rule that works on crypto does not work on currency pairs.
