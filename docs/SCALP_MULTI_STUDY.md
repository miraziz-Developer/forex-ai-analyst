# Multi-rule scalping (Investopedia's seven rules + the ForexFactory 3-EMA rule) — pre-registration

Written and committed, with `src/forex_ai_analyst/forex/scalp_multi_study.py`, **before** any result is seen.

**Source.** Investopedia, "Top Trading Strategies for Scalping" (A. Farley, updated 2026-10-03): seven
indicator rules written for stocks on charts of 15 minutes or less; plus the ForexFactory 3-EMA rule already
registered in [SCALP_3EMA_STUDY.md](SCALP_3EMA_STUDY.md). The owner asked to combine them into one
multi-rule scalping strategy and test it.

**Rules as coded (signals on completed mid-price bars; the article's own numbers where it gives them):**

| # | Rule | Bars | Entry | Exit |
|---|---|---|---|---|
| 1 | 5-8-13 SMA ribbon | M2 | ribbon realigns (5>8>13) with all three rising; mirrored | close through the 13 SMA |
| 2 | Ribbon + 5-3-3 stochastic + 13-bar 3-SD Bollinger | M2 | ribbon aligned and %K crosses up through 20 (down through 80) | high into the band, or %K crosses %D against |
| 3 | MACD(12,26,9) + RSI(14) momentum | M5 | MACD cross with RSI on the right side of 50, or RSI leaving 30/70 with MACD agreeing | MACD cross against, or RSI leaving 70 (long) / 30 (short) |
| 4 | Pivot reversal (TradingView, 4 left / 2 right) | M5 | buy stop 0.1 pip above the last pivot high, sell stop below the last pivot low; stop and reverse | the opposite stop |
| 5 | RMI(14,5) + SuperTrend(10,3) | M5 | RMI crosses up through 30 above SuperTrend; mirrored at 70 | close through SuperTrend, or RMI turning back |
| 6 | Linear regression slope(20) + Bollinger(20,2) | M5 | close at the lower band with a rising slope; mirrored | close back at the middle band |
| 7 | EMA 9/21 + RSI(14) | M5 | close > EMA9 > EMA21 and RSI leaving 30, or EMA9 crossing EMA21 with RSI > 50; mirrored | the opposite EMA cross |
| 8 | ForexFactory 3-EMA | M5/H1 | as registered | as registered (1:1 target) |

Rules 1–7 share one risk frame, because the article gives no stops: a protective stop of 2 ATR(14) of
the rule's bars, at most 24 bars in a trade, an opposite signal closes (and may reverse) the trade.

**Combinations (the two candidate "multi" strategies):**
- `portfolio`: all eight rules trade independently, each trade risking the same amount (1R).
- `confluence`: on M5, enter when at least two different rules opened trades in the same direction
  during the last three M5 bars and none in the other direction; stop 1.5 ATR(14), target 1.5 ATR, at
  most 24 bars.

**Execution.** Dukascopy one-minute bid/ask, EURUSD, GBPUSD, USDCHF. Market orders fill at the open of
the first minute after the signal bar (buy at the ask, sell at the bid). Stop entries fill when the ask
(bid) reaches the level, at the level or at the open if it gaps through. Within a minute the protective
stop is checked before the target. Positions are closed before any pause in the data longer than 30
minutes (weekends).

**Cost.** The real Dukascopy spread **plus 0.7 pip per round trip** (Dukascopy's own commission,
$35 per million per side). Results are also reported at 0 and at 1.2 pips extra (a retail account
without commission but wider spreads). Everything is measured in R, the loss at the protective stop.

**Protocol.** Identical to the 3-EMA study:
- **Development** = 2024-10-01 .. 2025-09-30. All rules and both combinations are run here, weaknesses
  are found here, and any improvement is built here. Every version tried is listed with the result.
- **Holdout** = 2025-10-01 .. 2026-09-30, run once, for one final version.
- **The final version passes only if, on the holdout, at the 0.7 pip cost:** at least 300 trades; mean
  net R > 0 with day-bootstrap P(mean > 0) >= 0.95; profit factor >= 1.2; at least 2 of 3 pairs positive.

## Fixes for the known weaknesses of scalping (fixed before any development result)

Scalping loses mainly to three things: costs that are large against a stop of a few pips, quiet hours
with wide spreads and random noise, and trading against the larger trend. Each fix answers one of them and
is written down now, so it cannot be tuned to the data:

| Variant | Change |
|---|---|
| `base` | the rules exactly as above |
| `S` | entries only 07:00–16:59 UTC (London open to the end of the London/New York overlap) |
| `T` | entries only in the direction of the H1 EMA 8/13/21 stack (the forum rule's filter, for every rule) |
| `W` | a protective stop narrower than 6 pips is widened to 6 pips (target widened in proportion), so the 0.7 pip cost stays near 10% of R |
| `STW` | all three |

For each variant: every single rule, `portfolio`, `confluence`, and `selected` (the portfolio of only the
rules whose development mean R is positive). The development run also reports where each version loses
(session, stop size, side, pair).

**Choosing the one version for the holdout (fixed now):** among the 15 combined versions (5 variants x
portfolio / confluence / selected), the one with the highest development mean R at the 0.7 pip cost, among
those with at least 300 development trades, development P(mean > 0) >= 0.95 and profit factor >= 1.2.
If no version qualifies, the holdout is not spent and the verdict is FAIL on development data.

## Data fix found during the first development run (2026-10-07)

The pivot rule produced absurd results (losses of billions of R). Cause: on holidays (e.g. 2025-01-01) the
feed fills minutes without ticks with flat candles at the last price (volume 0). They have no range, so the
ATR, and with it the protective stop, collapsed to almost nothing. Minutes with zero tick volume are now
dropped in `scalp_3ema_study.minutes` (shared by both studies). On a normal day this removes about 2 of
1,440 minutes. This is a correction of the data, not a change of any rule.

## Amendment after the first development look (2026-10-07, before USDCHF arrived)

**What the development data showed (EURUSD + GBPUSD, 2024-10..2025-09):** every rule that buys strength
and sells weakness lost **before** any added cost: per trade at zero extra cost, ribbon -0.23R, pivot
reversal -0.59R, MACD/RSI -0.14R, EMA/RSI -0.13R, 3-EMA forum rule -0.10R. The simulator was checked on a
zero-cost random walk, where every rule comes out near 0R (-0.25 to +0.14R, within noise for the trade
counts), so this is the market, not the code: on 2- and 5-minute bars these majors mean-revert, and a
breakout more often comes back than runs. The fixes registered above (session, trend, wider stop) only
reduce the damage; none of the 15 combined versions is positive on these two pairs.

**Added versions (written before running them on any data):** the opposite trade of rules 1–7 (`fade`):
- buy signals become sells, exits swap;
- a stop entry becomes a limit order on the other side (sell limit at the pivot high instead of a buy
  stop there). A limit order fills only when price trades 0.2 pip through it, at the limit price, never
  better unless the minute opens beyond it.

Rule 8 (3-EMA) is not faded. Variants `F` (fade, all hours) and `FS` (fade, 07–17 UTC).

**Holdout choice, amended:** the same rule over 21 combined versions (7 variants × portfolio / confluence /
selected). Since the fade idea came from the development data, the development numbers of the fade versions
are in-sample; only the holdout can show whether it is real.

## Amendment 2: bigger bars (2026-10-07, on the owner's question, before running it on any data)

On M2/M5 the protective stop is 5–8 pips, so the spread and the 0.7 pip cost take 10–30% of R and the
entry timing noise is of the same size as the stop. On bigger bars the same cost is a small part of R.
Variants `M15`, `H1`, `H4`: rules 1–7 unchanged except that they run on 15-minute, 1-hour or 4-hour bars
(ATR, stop, the 24-bar time limit and the confluence window all scale with the bar). Rule 8 is left out of
these variants. Positions are still closed before weekends. The holdout choice now runs over 30 combined
versions (10 variants × portfolio / confluence / selected), with the same thresholds.
