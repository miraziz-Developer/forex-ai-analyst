# Research lab report

Period 2021-01-01..2026-09-01, pairs BTC-USDT, ETH-USDT, SOL-USDT, XRP-USDT, BNB-USDT, timeframes [1, 4]h, risk/trade 1.00%, trials searched 108, walk-forward windows 8.

## Out-of-sample results (walk-forward, taker costs + real funding)

DSR deflates against the families compared on OOS data; DSR(all) against every config searched (stricter reference, not used by the gate).

| Family | Sharpe | CAGR % | Max DD % | DSR | DSR(all) | Win windows | Maker Sharpe | Stress Sharpe | Boot p5 CAGR % | Promotable |
|---|---|---|---|---|---|---|---|---|---|---|
| bb_reversion | -0.715 | -2.12 | -13.81 | 0.002 | 0.0 | 25% | -0.407 | -1.221 | -4.25 | no |
| donchian | 1.278 | 9.49 | -6.14 | 0.831 | 0.0 | 75% | 1.329 | 1.192 | 0.73 | no |
| ema_trend | 0.909 | 2.27 | -2.85 | 0.603 | 0.0 | 75% | 0.96 | 0.808 | -0.61 | no |
| tsmom | 0.404 | 2.61 | -9.94 | 0.225 | 0.0 | 75% | 0.705 | -0.188 | -3.48 | no |

**Portfolio (inverse-vol, all families):** Sharpe 0.576, CAGR 1.38%, max DD -4.59%.
**Benchmark (equal-weight buy & hold, same OOS period):** Sharpe 1.1, CAGR 57.75%, max DD -61.68%.
**Average pair correlation:** 0.61 (close to 1 means the pairs are effectively one bet).

## Selections per test window

**bb_reversion**
- 2023-01-01..2023-07-01: `bb_reversion|4h|period=20,k=2.0,adx_max=20,sides=both` -> Sharpe 2.85, return 5.44%
- 2023-07-01..2024-01-01: `bb_reversion|4h|period=20,k=2.0,adx_max=20,sides=both` -> Sharpe -0.27, return -0.47%
- 2024-01-01..2024-07-01: `bb_reversion|4h|period=20,k=2.5,adx_max=20,sides=both` -> Sharpe -3.744, return -4.47%
- 2024-07-01..2025-01-01: `bb_reversion|4h|period=20,k=2.5,adx_max=20,sides=long` -> Sharpe -2.711, return -1.72%
- 2025-01-01..2025-07-01: `bb_reversion|4h|period=20,k=2.0,adx_max=20,sides=long` -> Sharpe -2.762, return -3.53%
- 2025-07-01..2026-01-01: `bb_reversion|4h|period=20,k=2.5,adx_max=25,sides=long` -> Sharpe 0.269, return 0.42%
- 2026-01-01..2026-07-01: `bb_reversion|4h|period=20,k=2.5,adx_max=25,sides=long` -> Sharpe -1.702, return -2.61%
- 2026-07-01..2026-09-01: `bb_reversion|4h|period=20,k=2.5,adx_max=25,sides=both` -> Sharpe -1.087, return -0.59%

**donchian**
- 2023-01-01..2023-07-01: `donchian|4h|entry_n=100,exit_n=10,stop_atr=2.0,sides=long` -> Sharpe 1.922, return 8.2%
- 2023-07-01..2024-01-01: `donchian|4h|entry_n=100,exit_n=10,stop_atr=3.0,sides=long` -> Sharpe 1.383, return 3.03%
- 2024-01-01..2024-07-01: `donchian|4h|entry_n=100,exit_n=20,stop_atr=2.0,sides=long` -> Sharpe 2.054, return 8.81%
- 2024-07-01..2025-01-01: `donchian|4h|entry_n=100,exit_n=20,stop_atr=2.0,sides=long` -> Sharpe 2.131, return 8.27%
- 2025-01-01..2025-07-01: `donchian|4h|entry_n=20,exit_n=20,stop_atr=2.0,sides=long` -> Sharpe -0.409, return -2.12%
- 2025-07-01..2026-01-01: `donchian|4h|entry_n=100,exit_n=20,stop_atr=2.0,sides=long` -> Sharpe 2.112, return 7.4%
- 2026-01-01..2026-07-01: `donchian|4h|entry_n=100,exit_n=20,stop_atr=3.0,sides=long` -> Sharpe -1.304, return -2.52%
- 2026-07-01..2026-09-01: `donchian|4h|entry_n=100,exit_n=20,stop_atr=3.0,sides=long` -> Sharpe 2.666, return 3.63%

**ema_trend**
- 2023-01-01..2023-07-01: `ema_trend|4h|fast=50,slow=200,trail_atr=4.0,sides=long` -> Sharpe 2.141, return 3.53%
- 2023-07-01..2024-01-01: `ema_trend|4h|fast=50,slow=100,trail_atr=4.0,sides=long` -> Sharpe 0.516, return 0.56%
- 2024-01-01..2024-07-01: `ema_trend|4h|fast=50,slow=100,trail_atr=4.0,sides=long` -> Sharpe 1.918, return 2.68%
- 2024-07-01..2025-01-01: `ema_trend|4h|fast=50,slow=200,trail_atr=4.0,sides=long` -> Sharpe -0.227, return -0.18%
- 2025-01-01..2025-07-01: `ema_trend|4h|fast=50,slow=200,trail_atr=4.0,sides=long` -> Sharpe 0.552, return 0.48%
- 2025-07-01..2026-01-01: `ema_trend|4h|fast=50,slow=200,trail_atr=4.0,sides=long` -> Sharpe 0.489, return 0.35%
- 2026-01-01..2026-07-01: `ema_trend|4h|fast=50,slow=200,trail_atr=4.0,sides=long` -> Sharpe -2.442, return -1.54%
- 2026-07-01..2026-09-01: `ema_trend|4h|fast=20,slow=100,trail_atr=3.0,sides=long` -> Sharpe 2.361, return 2.49%

**tsmom**
- 2023-01-01..2023-07-01: `tsmom|4h|lookback_days=30,sides=both` -> Sharpe 0.487, return 1.62%
- 2023-07-01..2024-01-01: `tsmom|4h|lookback_days=30,sides=both` -> Sharpe 1.533, return 6.85%
- 2024-01-01..2024-07-01: `tsmom|4h|lookback_days=30,sides=both` -> Sharpe 0.185, return 0.49%
- 2024-07-01..2025-01-01: `tsmom|4h|lookback_days=30,sides=long` -> Sharpe -0.467, return -1.47%
- 2025-01-01..2025-07-01: `tsmom|4h|lookback_days=14,sides=long` -> Sharpe 0.225, return 0.56%
- 2025-07-01..2026-01-01: `tsmom|4h|lookback_days=14,sides=long` -> Sharpe 0.117, return 0.27%
- 2026-01-01..2026-07-01: `tsmom|4h|lookback_days=14,sides=long` -> Sharpe -0.266, return -0.85%
- 2026-07-01..2026-09-01: `tsmom|4h|lookback_days=7,sides=both` -> Sharpe 1.161, return 2.28%

## Promotion gate

```
{
  "min_oos_sharpe": 0.8,
  "min_dsr": 0.9,
  "max_drawdown_pct": -35.0,
  "min_positive_window_share": 0.6,
  "min_stress_sharpe": 0.3
}
```
