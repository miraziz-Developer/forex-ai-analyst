# Combination study — pre-registration

Written and committed, together with the code, **before** the study is run.

**Question.** The owner asked for the strongest combination of the ideas
discussed: pullback swing, structure breakout (zigzag / SMC break of
structure), liquidity sweep (ICT / SMC / support-resistance false break) and
momentum rotation. Can any of them add to the live Donchian 4h strategy?

**Rules.** One objective, non-repainting rule per idea, textbook parameters,
no parameter search (`src/forex_ai_analyst/lab/candidates.py`). Swing points
are confirmed pivots (known 3 bars after the swing).

| Candidate | Entry | Exit / stop |
|---|---|---|
| pullback_rsi2 | 4h: EMA50 > EMA200, close > EMA200, RSI(2) < 10 | RSI(2) > 70 or 30 bars; stop 2.5 ATR |
| structure_breakout | 4h: close crosses above last confirmed swing high after a higher swing low | close below last confirmed swing low; stop at that low (>= 1 ATR) |
| liquidity_sweep | 4h: close > EMA200, low < last confirmed swing low < close, bullish bar | target 2R, stop under the sweep + 0.25 ATR, 30 bars |
| xs_momentum | daily: every 7 days hold the top 3 of 10 by 30-day return, only if positive | next rebalance; long only, 1x gross |

Baseline: live Donchian 4h (100/20/3 ATR, long).

**Data and costs.** The ten live markets, 2021-01-01..2026-09-01, lab engine
(taker 0.05% + slippage 0.02% per side, historical funding, 1% risk per
market sleeve). The momentum rotation pays 0.07% per side on turnover plus
funding.

**A candidate passes only if all hold:**
1. Sharpe > 0 in both halves (2021-2023 and 2024-2026).
2. Full-period Sharpe >= 0.5.
3. Deflated Sharpe >= 0.90 with N = 179 trials (lab grid 108 + edge lab 64 +
   two Donchian tests + these five).
4. For trade-based rules: profit factor >= 1.2 and >= 6 of 10 markets positive.

**Portfolio.** Donchian plus every passing candidate, inverse-volatility
weights estimated on 2021-2023 only, scaled to Donchian's volatility. It goes
live only if its Sharpe beats Donchian alone in **both** halves.

**No rescue.** A candidate that fails is recorded as failed. Parameters are
not tuned afterwards; any change would be a new pre-registered study that
pays for these trials too.

## Result

(pending)
