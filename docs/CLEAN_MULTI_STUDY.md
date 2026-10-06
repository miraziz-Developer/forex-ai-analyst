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
