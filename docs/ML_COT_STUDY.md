# FX ML model v2 with CFTC positioning — pre-registration

Written and committed, with its code (`src/forex_ai_analyst/forex/cot.py`,
`ml_features.py`, `ml_study.py --cot`) and tests, **before** the study is run.

**Idea.** Large speculators (CFTC "non-commercial") crowd into currencies; when
their positioning is extreme or turning, the currency may be pushed or reverse.
This adds information the v1 price-based features do not have.

**Data.** CFTC legacy Commitments of Traders, futures only, 2003 onwards, by
stable contract code: EUR, GBP, JPY, AUD, NZD, CAD, CHF, gold. Reports are as of
Tuesday and published on Friday, so a Monday decision uses only the report
dated six or more days earlier.

**New features (added to all v1 features):** net speculative position / open
interest; its 52-week percentile; its 4-week change. Pairs use the exposure of
the pair: XXXUSD +XXX, USDXXX -XXX, EURJPY / GBPJPY base minus JPY (percentile
averaged), XAUUSD +gold.

**Model and test (one trial):** logistic regression exactly as v1 (same
settings, yearly walk-forward 2010-2026 with label purge, P >= 0.55 / <= 0.45,
same costs). Samples without a year of COT history are dropped.

**Gates (same as v1, absolute):** at least 200 trades; bootstrap P(mean net > 0)
>= 0.90; profit factor >= 1.2; positive in 2010-2017 and in 2018+; at least 60%
of markets positive. If v2 passes, it joins the MT5 demo next to v1 under its own
model version; if not, v1 continues alone.

## Result (2026-10-06) — v2 does not pass; v1 continues alone on the demo

| Model | Samples | Trades | Hit | Mean net / trade | PF | P(mean>0) | 2010-17 / 2018+ | Markets + | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| v1 (reference, corrected data) | 10852 | 2209 | 52.6% | +0.028% | 1.05 | 0.76 | -0.011% / +0.146% | 8/10 | FAIL |
| **v2 = v1 + COT** | 10791 | 2528 | 52.1% | **+0.038%** | **1.07** | **0.86** | -0.010% / +0.140% | 8/10 | **FAIL** |

COT positioning improves the model a little (higher mean, profit factor and
confidence, more confident trades) but not enough: P 0.86 < 0.90, PF 1.07 < 1.2,
and 2010-2017 is still slightly negative. Per the pre-registration, v2 does not
join the demo.
