# Multi-Strategy Crypto Paper Bot

One `main` branch and one Render web service:

```text
BingX public perpetual-swap closed OHLCV
  → 15m regime + 5m strategy agents
  → deterministic coordinator and risk manager
  → Turso paper-signal/outcome ledger + Telegram notification
```

By default this is paper-only. When `AUTO_EXECUTE_TRADES=true`, `BINGX_API_KEY`, and `BINGX_SECRET` are all configured, accepted signals also place a BingX **VST/virtual-money demo** market order with exchange-side TP/SL. The execution client is hardcoded to `open-api-vst.bingx.com`; it has no live-money endpoint configuration.

## Current strategies and controls

- `trend_pullback`: 15-minute trend regime with a confirmed 5-minute pullback/reclaim.
- `support_resistance_rejection`: closed 5-minute rejection at an ATR-width confirmed pivot zone; it does not counter-trade a classified trend.
- `breakout_retest`: a `BREAKOUT_READY` regime, volume-confirmed structure break, then a closed retest candle. It never enters on the initial breakout candle.
- Regime classification is fail-closed: `TRENDING_UP`, `TRENDING_DOWN`, `RANGING`, `BREAKOUT_READY`, `HIGH_VOLATILITY`, or `UNCERTAIN`. High-volatility and uncertain regimes do not produce new entries.
- Trend, momentum, volatility, volume, structure, and candle action are scored as separate groups; correlated trend indicators are not counted as independent confirmations.
- BingX perpetual-swap public REST klines; no BingX account or API key is required. This is market data only; VST order execution remains separately restricted to the demo endpoint.
- Only fully closed, validated, deduplicated candles are used. The provider caches one pair/timeframe result until the next closed-candle boundary to avoid duplicate REST requests during a scan/resolution cycle.
- A candidate must have valid directional levels, score at least 65, and R:R at least 1.3.
- Same-candle duplicates are persisted and rejected.
- Open-position and accepted-trade-count limits are disabled by default (`MAX_OPEN_POSITIONS=0`, `MAX_DAILY_TRADES=0`); every independently accepted signal is recorded. `$0.75` stop-risk per trade and the `$2.00` daily realized-loss protection remain by default.
- Outcome resolution is candle-based and conservative: when a candle touches both stop and target, it records `LOSS`.

## Setup

```bash
cp .env.example .env
# Set TURSO_DATABASE_URL and TURSO_AUTH_TOKEN.
# Telegram credentials are optional but required for paper alerts.
python3 -m pip install -r requirements.txt
python3 app.py
```

Required Turso variables:

```env
TURSO_DATABASE_URL=libsql://your-database.turso.io
TURSO_AUTH_TOKEN=your-token
```

The app creates and migrates its own `signal_candidates`, `signal_decisions`, `daily_risk_state`, and `market_snapshots` tables in that database. Existing legacy `signals` rows are not changed.

## Deterministic multi-strategy replay

`multi_strategy_backtest.py` provides the reusable closed-bar replay engine for each agent. Its evaluator receives history only through the signal candle, enters at the next candle open, applies configurable round-trip fee/slippage/funding costs, enforces the central daily/open-position limits, expires stale trades, and resolves an OHLC candle touching both levels as `LOSS`.

Use `metrics(trades)` to report trade count, win rate, after-cost net P&L, expectancy, profit factor, maximum drawdown, and average holding time. Review results independently by `strategy`, `pair`, and `regime`; no strategy may be promoted beyond paper operation solely from an in-sample win rate.

## Configuration

See [`.env.example`](.env.example). The production-safe defaults are:

```env
# Effective VST execution also requires both BingX demo API credentials.
AUTO_EXECUTE_TRADES=true
MULTI_STRATEGY_PROVIDER=bingx
MULTI_STRATEGY_PAIRS=BTC-USDT,ETH-USDT,SOL-USDT,XRP-USDT,BNB-USDT
MULTI_STRATEGY_SCAN_INTERVAL_SECONDS=300
```

Do not set the scan interval below 300 seconds. The scheduler scans pairs serially and prevents overlapping runs.

## HTTP endpoints

- `GET /health` — service/provider/paper-only status; used by Render.
- `GET /api/signals?limit=100` — recent paper candidates and outcomes. If `DASHBOARD_TOKEN` is configured, provide it as `?token=...` or `X-Dashboard-Token`.

## Render deployment

`render.yaml` defines the single `forex-ai-analyst` web service and starts `python app.py`. Configure the Turso credentials and optional Telegram credentials in Render. Deploy this repository’s `main` branch only.

After deploy, verify:

```bash
curl https://YOUR-RENDER-SERVICE.onrender.com/health
```

Expected essentials: `"service":"multi-strategy-paper"`, `"paper_only":true`, `"auto_execute_trades":false`, and `"auto_execute_trades_configured":true`.

## Validation

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q .
python3 -m pip check
git diff --check
```