# Research findings — candidate accuracy improvements (not yet implemented)

Collected 2026-09-02, after 12 resolved trades at a 16.7% win rate (-$3.61).
Each item below is evidence-backed rather than folklore, with the source and
the honest caveat attached. **Nothing here is implemented yet** — the plan is
to review the whole list, pick, then apply in one batch.

Two items were already implemented on 2026-09-02 and are NOT in this list:
volume confirmation (≥1.5x 20-bar average) and the CHoCH downgrade rule
(60-70% of CHoCH events resolve as pullbacks, cap confidence at ORTA without a
confirming BOS).

---

## 1. ATR percentile volatility-regime filter — mechanical, code-level
**What**: rank current ATR against the last ~100 periods. Skip new entries when
the percentile is below ~20 (market asleep → sideways chop → false signals) or
above ~90 (erratic news/crash spikes → unpredictable).

**Why it matters here**: this is a *mechanical* filter, not an LLM judgment
call, so it can't be argued away by a confident-sounding rationalization. We
already compute ATR in `indicators.py`, so the incremental work is small
(percentile rank + a gate in `analyze_and_notify`).

**Evidence**: [quantmonitor.net](https://quantmonitor.net/how-to-identify-market-regimes-and-filter-strategies-by-trend-and-volatility/),
[coinquant.ai](https://www.coinquant.ai/blog/how-to-use-atr-in-a-crypto-trading-strategy-with-backtest) —
crypto specifically suffers extended low-volatility periods that generate many
false signals; a simple ATR floor removes most of them.

**Honest tradeoff**: this *reduces* signal count, which is already the user's
complaint. It is the single strongest filter on this list, but it makes the
"too few signals" problem worse before it makes it better.

---

## 2. Multi-timeframe alignment as a hard requirement — biggest measured effect
**What**: require the trade direction to agree with the trend on daily AND 4H
AND 1H (3/3), rather than the current soft rule ("if 4H and 1H disagree, lean
toward SKIP unless the 5-min pattern is exceptionally clean" — an escape hatch
that has been used repeatedly).

**Evidence**: Journal of Technical Analysis (2021), via
[quantum-algo.com](https://www.quantum-algo.com/blog/multi-timeframe-analysis-guide/)
and [medium.com](https://medium.com/@contentorybaxter/multi-timeframe-alignment-success-rates-the-47-point-win-rate-gap-between-trading-with-bb-lb-f01a525f512a):
trades aligned on ≥2 timeframes showed 67% success vs 49% for single-timeframe,
holding across equities, forex and futures. 3/3 alignment ~55-65% vs 2/3
~40-50%. **Signals taken against the higher-timeframe bias collapsed to 38.1%.**
Typical cost: 20-30% fewer trades for a 5-15% win-rate gain.

**Why it matters here**: this maps directly onto our actual losses. id=8 and
id=14 were both explicitly counter-daily-trend shorts ("1D makro hali bullish
bo'lsa ham…") and both lost. Several BUY losses (9, 11, 12, 13, 16) had a 1H
described as "aralash"/mixed — i.e. 2/3 alignment at best, the ~40-50% bucket.

---

## 3. Funding-rate thresholds — numeric, prompt-level
**What**: give the model actual numbers for what "extreme" funding means
instead of leaving it to interpretation. Roughly: |funding| < 0.01% per 8h is
effectively neutral; 0.05-0.10%+ is genuinely crowded positioning with real
squeeze risk.

**Evidence**: [kraken.com](https://www.kraken.com/learn/futures-trading-funding-rate-strategy),
[quantjourney.substack.com](https://quantjourney.substack.com/p/funding-rates-in-crypto-the-hidden).
Also: rising OI + rising funding = confirmed directional crowding; falling OI
with extreme funding = trend losing participation.

**Why it matters here**: our live signals typically show funding around
+0.0100%, which is *neutral* by this standard — but the prompt's wording
("extreme positive funding = crowd heavily long") has no number attached, so
the model can over-weight a reading that is actually unremarkable.

**Tradeoff**: none meaningful — this sharpens interpretation without reducing
signal count. Cheapest item on the list.

---

## 4. Partial take-profit / scaling out — raises win rate, may lower expectancy
**What**: close ~50% of the position at 1R and let the rest run to the
structural target.

**Evidence**: [quantstrategy.io](https://quantstrategy.io/blog/backtesting-partial-close-strategies-does-scaling-out/),
[tradingheroes.com](https://www.tradingheroes.com/move-stoploss-breakeven/).
Scaling out reliably *raises the nominal win rate* by converting would-be
losers into partial wins — but frequently **costs expectancy** (average profit
per trade). Separately, moving the stop to breakeven too eagerly "heavily cuts
into long-term profitability", especially for trend-following.

**Read this one carefully**: it is the item that most directly targets the
stated goal ("2 out of 3 should win") — and it is also the item most likely to
make the *win rate* look better while making *actual profit* worse. If we take
it, take it with eyes open: it is cosmetic-favourable, economically neutral to
negative. Also note it triples transaction costs on the exit side, which our
fee accounting would need to reflect.

---

## Ranking, if we implement in one batch
1. **#3 funding thresholds** — free, no downside, do it regardless.
2. **#2 multi-timeframe hard requirement** — best measured win-rate effect and
   it matches our own loss pattern. Main cost: fewer signals.
3. **#1 ATR regime filter** — strongest mechanical filter, but compounds the
   "few signals" problem. Best paired with accepting a lower trade frequency.
4. **#4 partial TP** — only if the goal really is a prettier win rate rather
   than more profit. I would not recommend it on expectancy grounds.

**Combined effect on frequency**: #1 + #2 together could plausibly cut signal
count by half or more. That is the honest cost of a higher win rate — the
trades being removed are precisely the marginal ones we have been losing on.
