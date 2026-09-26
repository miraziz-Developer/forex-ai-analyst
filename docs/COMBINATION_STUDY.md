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

## Result (2026-09-26) — no candidate passes; live strategy unchanged

| Strategy | Sharpe | CAGR | Max DD | 2021-23 | 2024-26 | Trades/mo | Win rate | Mean R | PF | Markets + | Corr. w/ Donchian |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **donchian_4h (live)** | **1.32** | 6.7% | **-4.9%** | 1.52 | 1.06 | 8.5 | 34% | **+0.67** | **2.18** | 9/10 | 1.00 |
| pullback_rsi2 | -0.63 | -1.3% | -9.4% | -0.29 | -1.04 | 30.9 | **62%** | -0.03 | 0.84 | 1/10 | 0.34 |
| structure_breakout | 0.68 | 4.2% | -11.0% | 1.01 | 0.30 | 22.2 | 30% | +0.17 | 1.31 | 8/10 | 0.71 |
| liquidity_sweep | -0.12 | -0.4% | -10.7% | 0.22 | -0.48 | 18.6 | 36% | -0.02 | 0.98 | 4/10 | 0.40 |
| xs_momentum | 0.76 | 33.6% | **-65.8%** | 0.99 | 0.43 | weekly | — | — | — | — | 0.58 |

(Trade strategies: 1% risk per market sleeve, so CAGR and drawdown scale with
risk; momentum rotation is fully invested, 1x.)

- **pullback_rsi2** has the high win rate the owner asked about (62%) and still
  loses money: small wins, larger losses, and costs on ~31 trades a month.
- **structure_breakout** (zigzag / SMC break of structure) makes money but is
  a weaker copy of Donchian (correlation 0.71, Sharpe 0.68 vs 1.32), so it adds
  trades without adding a new edge.
- **liquidity_sweep** (ICT / SMC) is break-even before its stop/target
  asymmetry and negative after costs.
- **xs_momentum** earns 34% a year but with a 66% drawdown: it is mostly
  leveraged exposure to the crypto market, not a separate edge.

**Deflated Sharpe.** With N = 179 trials and the wide spread of Sharpe ratios
across these very different rules, the DSR gate rejects every stream,
including Donchian itself (0.08). By that standard nothing tested on this data
is distinguishable from the best of 179 lucky draws. Donchian's support rests
instead on the pre-registered test on five unseen markets
(`docs/DONCHIAN_NEW_MARKETS.md`), which it passed. The gate was fixed in
advance and is reported as is.

**Portfolio.** No candidate passed, so the portfolio is Donchian alone; the
live service does not change.
