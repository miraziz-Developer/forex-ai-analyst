# Multi-Strategy Crypto Paper Bot

One `main` branch and one Render web service. The bot is deliberately **paper-only**:

```text
Binance Futures public closed OHLCV
  → 15m regime + 5m strategy agents
  → deterministic coordinator and risk manager
  → Turso paper-signal/outcome ledger + Telegram notification
```

It does not import or call the BingX execution client. If `AUTO_EXECUTE_TRADES=true`, startup fails before it can scan or place an order.

## Current strategy and controls

- `trend_pullback`: 15-minute trend regime with a confirmed 5-minute pullback/reclaim.
- Binance USD-M Futures public REST klines; no Binance account or API key is required.
- Only fully closed, validated, deduplicated candles are used.
- A candidate must have valid directional levels, score at least 65, and R:R at least 1.3.
- Same-candle duplicates are persisted and rejected.
- Maximum one open paper position, three accepted paper trades per UTC day, `$0.75` stop-risk per trade, and `$2.00` daily realized-loss limit by default.
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

The app creates its own `signal_candidates` and `daily_risk_state` tables in that database. Existing legacy `signals` rows are not changed.

## Configuration

See [`.env.example`](.env.example). The production-safe defaults are:

```env
AUTO_EXECUTE_TRADES=false
MULTI_STRATEGY_PROVIDER=binance_futures
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

Expected essentials: `"service":"multi-strategy-paper"`, `"paper_only":true`, and `"auto_execute_trades":false`.

## Validation

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q .
python3 -m pip check
git diff --check
```