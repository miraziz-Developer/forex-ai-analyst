# Six documented ideas not yet tested here — pre-registration

Written and committed, with `src/forex_ai_analyst/forex/ideas_study.py`, **before** any of them is run.
The owner asked to test every remaining documented idea. Each rule is taken from its source as closely as
the data allows; nothing is tuned, so each full sample is one out-of-sample test, also shown in halves.
Six more trials (the running count in the other studies stays visible).

| # | Idea (source) | Market, data | Rule | Passes only if |
|---|---|---|---|---|
| 1 | **Pre-FOMC drift** (Lucca and Moench 2015) | US500, Dukascopy H1 bid/ask, 2013-2026 | buy at 14:00 New York the weekday before a scheduled FOMC statement, sell at 13:59 on statement day (before the 14:00 release). Scheduled = Tue-Thu releases minus 2019-10-11, 2020-03-03/15/23/31 and 2020-08-27 | >= 80 events, mean > 0, bootstrap P >= 0.95, mean > 0 in 2013-2019 and 2020-2026; the same window on other Tue-Thu shown as the baseline |
| 2 | **Crypto cross-sectional momentum, market neutral** (Liu, Tsyvinski and Wu 2022) | 62 coins (the bot's rebound list), Binance 1h, 2021-2026 | every Monday 00:00 UTC rank by 28-day return; long the top fifth, short the bottom fifth, equal weight, one week. (The long-only top-3-of-10 rotation in COMBINATION_STUDY.md was a different rule.) | >= 100 weeks, annual Sharpe >= 0.5, P >= 0.95, mean > 0 in 2021-2023 and 2024-2026 |
| 3 | **Funding carry** (crypto carry literature) | same | rank by funding paid over the last 7 days; long the lowest fifth, short the highest fifth, one week, funding counted | as 2 |
| 4 | **Overnight drift** (equity returns accrue overnight) | US500 H1 | buy at the cash close (close of the 15:00 bar), sell at the next weekday's first bar from 09:00 New York | >= 1000 nights, Sharpe >= 0.5, P >= 0.95, both halves > 0, **at a swap of 0.02% a night** (0.01% and 0.04% shown) |
| 5 | **5-minute opening-range breakout** (Zarattini and Aziz 2023) | NAS100, Dukascopy M1 bid/ask, 2024-10..2026-09 (two years: the minute data that can be fetched in reasonable time) | first 5-minute candle from 09:30 New York: up -> buy at 09:35, stop at its low; down -> sell, stop at its high; target 10R; out at 16:00 | >= 300 trades, mean R > 0, P >= 0.95, PF >= 1.15, both halves > 0 |
| 6 | **Ratio reversion** (classic pairs) | (a) ETH/BTC 4h, 2021-2026; (b) gold/silver daily, 2010-2026 | z of ln(ratio) vs the previous 180 bars (a) / 60 days (b): beyond +-2 sell the rich leg and buy the cheap one, out at z = 0, at |z| > 4 or after 10 / 20 days | each: >= 50 trades, mean > 0, P >= 0.95, PF >= 1.2, both halves > 0 |

**Costs.** Index: real spread + 0.02% a round trip + 0.02% swap a night. Crypto: 0.07% per side (taker +
slippage) on every leg, full weekly turnover assumed, real funding. Gold/silver: 0.07% a round trip on the
pair plus swap on both legs.

A pass becomes a demo engine (MT5 for 1, 4, 5 and gold/silver; BingX for 2, 3 and ETH/BTC, which would
need short positions: a separate decision).
