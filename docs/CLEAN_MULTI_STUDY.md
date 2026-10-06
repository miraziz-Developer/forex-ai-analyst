# Multi-strategy and the owner's rules on clean bid/ask data — pre-registration

Written and committed, with its code (`src/forex_ai_analyst/forex/clean_multi_study.py`),
**before** it is run.

**Why.** The owner asked for a forex system that reads the situation and lets
the fitting rule trade: trend following in trends, range / support-resistance
fades in ranges, breakouts after compression, with SMC/ICT patterns. Those rules
already exist and were tested once (docs/FOREX_STUDY.md: EMA pullback, pin bar
at support/resistance, ICT fair-value gap, Donchian; docs/REGIME_SYSTEM_STUDY.md:
regime classifier with trend, range and breakout modules) — but on Yahoo data:
2.8 years of hourly bars and daily bars now known to be malformed. This reruns
the same rules, unchanged, on clean data. It is a data correction, not a search.

**Data.** Dukascopy hourly bid/ask candles, 2010-2026, mid prices; H4 bars are
complete UTC 4-hour groups of them; D1 bars are Dukascopy day candles. Markets:
EURUSD, GBPUSD, AUDUSD, NZDUSD, USDCAD, USDCHF, USDJPY, EURJPY, GBPJPY, EURGBP
(gold is not in the hourly set). Costs: the conservative round trips of the
original studies (`data.MARKETS`; EURGBP 1.2 pips).

**Rules (unchanged code):**
- the owner's / textbook rules: ema_pullback_h1, pinbar_snr_h1, ict_fvg_h1,
  donchian_h4, donchian_h4_long, donchian_d1, ema_cross_d1;
- the regime-switching system on H4 and D1: trend, range and breakout modules
  and the combined system.

**Gate (stricter than the original, because about two hundred trials have now
been run on FX):** at least 100 trades; day-clustered bootstrap
P(mean R > 0) >= **0.95**; profit factor >= 1.2; mean R > 0 in both
chronological halves; at least 60% of markets positive. A passing rule goes to
the demo as a forward test with its own graduation bar; nothing passing means
the regime / SMC / ICT approach is closed on FX.

## Result (2026-10-06) — nothing passes

| Rule | Trades | Win | Mean R | PF | P | Halves | Markets + |
|---|---|---|---|---|---|---|---|
| ema_pullback_h1 | 37989 | 32.8% | -0.10 | 0.87 | 0.00 | -0.08 / -0.11 | 0/10 |
| pinbar_snr_h1 | 57423 | 27.9% | -0.21 | 0.75 | 0.00 | -0.19 / -0.23 | 0/10 |
| ict_fvg_h1 | 17685 | 25.2% | -0.07 | 0.92 | 0.00 | -0.06 / -0.08 | 0/10 |
| donchian_h4 | 2764 | 32.9% | -0.05 | 0.90 | 0.06 | -0.00 / -0.11 | 3/10 |
| donchian_h4_long | 1399 | 33.4% | -0.03 | 0.94 | 0.22 | +0.01 / -0.07 | 4/10 |
| donchian_d1 | 836 | 26.3% | -0.10 | 0.84 | 0.09 | -0.00 / -0.21 | 2/10 |
| ema_cross_d1 | 310 | 34.2% | -0.03 | 0.92 | 0.32 | +0.05 / -0.10 | 3/10 |
| regime H4 trend | 2371 | 32.8% | -0.06 | 0.89 | 0.02 | -0.04 / -0.07 | 2/10 |
| regime H4 range | 867 | 61.5% | -0.05 | 0.83 | 0.01 | -0.03 / -0.07 | 2/10 |
| regime H4 breakout | 123 | 40.7% | +0.24 | 1.51 | 0.94 | +0.31 / +0.17 | 7/10 |
| regime H4 combined | 3361 | 40.5% | -0.05 | 0.90 | 0.02 | -0.03 / -0.06 | 2/10 |
| regime D1 trend | 393 | 31.0% | -0.02 | 0.97 | 0.41 | +0.12 / -0.16 | 4/10 |
| regime D1 range | 213 | 63.4% | +0.01 | 1.03 | 0.55 | -0.01 / +0.02 | 5/10 |
| regime D1 breakout | 17 | 35.3% | +0.27 | 1.52 | 0.65 | -0.49 / +0.94 | 2/4 |
| regime D1 combined | 623 | 42.2% | -0.00 | 1.00 | 0.48 | +0.10 / -0.10 | 4/10 |

On sixteen years of clean bid/ask data the owner's H1 rules (EMA pullback, pin
bar at support/resistance, ICT fair-value gap) lose on every one of the ten
markets, and the regime-switching system, combined, breaks even before nothing
is left. The only positive line is the H4 squeeze-breakout module alone (123
trades, P 0.94, both halves positive), which misses the bar and is one line out
of fifteen tested here, so it is not adopted. The regime / SMC / ICT approach is
closed on FX.
