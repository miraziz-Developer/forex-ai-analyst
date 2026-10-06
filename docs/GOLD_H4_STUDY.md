# Gold on H4 with the proven crypto Donchian rule — pre-registration

Written and committed, with its code (`src/forex_ai_analyst/forex/gold_h4_study.py`),
**before** the data is even downloaded.

**Why.** Gold is the strongest trending market in this project (daily long
trend PF 2.36), and the crypto rule (Donchian 100/20, 3 ATR, long only, on 4h
bars) is the project's proven trend rule. Gold on 4h bars has never been
tested here, so this is a fresh market for a fixed rule, and it trades more
often than the daily commodity engine.

**Data.** Dukascopy hourly bid/ask, 2010-2026, mid prices, complete UTC 4h
bars. Costs: round trip 0.05% (gold) / 0.10% (silver) plus 5%/year swap on
notional per calendar day held.

**Rule.** Unchanged: entry on a close above the previous 100-bar high, exit
on a close below the previous 20-bar low, 3 ATR stop, long only.

**Passes only if:** gold has at least 100 trades, profit factor >= 1.3,
day-clustered P(mean R > 0) >= 0.95 and mean R > 0 in both chronological
halves; and silver (replication) has mean R > 0. A pass adds gold (and silver
if offered) to the crypto-style H4 engine on the MT5 demo.
