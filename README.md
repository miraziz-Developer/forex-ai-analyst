# Context-Aware BingX VST AI Trader

One `main` branch and one Render web service:

```text
BingX public perpetual-swap closed OHLCV
  → 15m regime + positioning / liquidity context
  → PDF knowledge excerpts + prior VST trade reviews
  → structured AI decision (SKIP, WATCH, PROPOSE_TRADE)
  → validated BingX VST order + Turso journal + Telegram notification
```

By default auto-execution is off. When either `OPENAI_API_KEY` or the complete Azure OpenAI configuration (`AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT`) is present alongside `AUTO_EXECUTE_TRADES=true`, `BINGX_API_KEY`, and `BINGX_SECRET`, a valid AI trade proposal places a BingX **VST/virtual-money demo** market order with exchange-side TP/SL. The execution client is hardcoded to `open-api-vst.bingx.com`; it has no live-money endpoint configuration.

## Contextual AI controls

- The model receives closed 5m/15m/1h candles, regime features, funding/open-interest/order-book context, retrieved PDF excerpts and earlier outcome reviews.
- It may select `SKIP`, `WATCH`, or `PROPOSE_TRADE`; for a proposal it dynamically chooses direction, entry, stop, target, risk, leverage and cooldown.
- Model output is JSON-validated and invalid/API-unavailable output always becomes `SKIP` (no trade).
- BingX perpetual-swap public REST klines; no BingX account or API key is required. This is market data only; VST order execution remains separately restricted to the demo endpoint.
- Only fully closed candles are used. Same-candle fingerprints are persisted and rejected.
- Technical safety remains: allowed-pair whitelist, 1–125x leverage validation, structurally valid trade levels, exchange-side TP/SL, `KILL_SWITCH`, idempotency and fail-closed broker/data errors. Create BingX keys with **no withdrawal permission**.
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

The app creates and migrates its own signals, snapshots, AI reviews, knowledge documents and knowledge chunks in that database. Existing legacy `signals` rows are not changed.

## Telegram PDF knowledge and webhook

Only the chat IDs in `TELEGRAM_CHAT_ID` are authorized. Send `/start` once to open the button-based control panel: **Holat**, **So‘nggi signallar**, **Ochiq VST pozitsiyalar**, **Bilim bazasi**, **Bilimdan qidirish**, and **PDF yuklash**. Send a text-based PDF to the bot and it will extract/chunk the text into Turso. Scanned PDFs need OCR before upload. Legacy `/knowledge` and `/knowledge_search <query>` remain available for compatibility.

Set `PUBLIC_BASE_URL` to the deployed HTTPS URL and `TELEGRAM_WEBHOOK_SECRET`; the service registers its webhook, callback updates, and command menu automatically on startup. If automatic setup is unavailable, register it manually once:

```bash
curl -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
  -d "url=https://YOUR-RENDER-SERVICE.onrender.com/telegram/webhook" \
  -d "secret_token=${TELEGRAM_WEBHOOK_SECRET}" \
  -d 'allowed_updates=["message","channel_post","callback_query"]'
```

The endpoint verifies Telegram's `X-Telegram-Bot-Api-Secret-Token` header. `callback_query` is required for the button panel. PDF metadata and extracted text are persisted; binary PDFs are not written to Render's ephemeral filesystem.

## Legacy deterministic replay (research-only)

`multi_strategy_backtest.py` and `production_backtest.py` remain available for historical deterministic-strategy research. They are not the deployed contextual AI/VST decision path and their fixed research limits do not override an AI trade decision. Their evaluator receives history only through the signal candle, enters at the next candle open, applies configurable round-trip fee/slippage/funding costs, expires stale trades, and resolves an OHLC candle touching both levels as `LOSS`.

Use `metrics(trades)` to report trade count, win rate, after-cost net P&L, expectancy, profit factor, maximum drawdown, and average holding time. Review results independently by `strategy`, `pair`, and `regime`; no strategy may be promoted beyond paper operation solely from an in-sample win rate.

For independent chronological validation, split a fixed period into equal account resets rather than relying on its aggregate result:

```bash
python3 production_backtest.py --start 2026-06-01 --days 90 \
  --pairs BTC-USDT,ETH-USDT,SOL-USDT,XRP-USDT,BNB-USDT \
  --risk-usdt 0.5 --max-daily-loss-usdt 1.5 --max-daily-trades 4 \
  --max-open-positions 1 --max-notional-usdt 75 --max-fee-to-risk-ratio 0.15 \
  --fee-pct-round-trip 0.10 --slippage-pct-round-trip 0.02 \
  --starting-equity-usdt 100 --min-score 75 --min-stop-atr-multiple 0.8 \
  --blocked-pairs XRP-USDT --walk-forward-windows 3 \
  --output production_walk_forward_90d.json
```

Each `walk_forward_windows` item is a separate `$100` replay with its own risk state. `promotion_eligible` is only a mechanical screen requiring every window to have positive after-cost P&L and profit factor above one; it is **not** permission to enable a pair or live trading. Require adequate independent sample size and stable paper/VST results before changing pair defaults.

## Configuration

See [`.env.example`](.env.example). The production-safe defaults are:

```env
# Effective VST execution also requires OpenAI and both BingX demo credentials.
OPENAI_API_KEY=
# Or use Azure OpenAI. Deployment is passed to the SDK as the model name.
AZURE_OPENAI_API_KEY=
AZURE_OPENAI_ENDPOINT=https://your-resource.services.ai.azure.com/openai/v1
AZURE_OPENAI_DEPLOYMENT=your-chat-deployment
AUTO_EXECUTE_TRADES=false
KILL_SWITCH=false
MULTI_STRATEGY_PROVIDER=bingx
MULTI_STRATEGY_PAIRS=BTC-USDT,ETH-USDT,SOL-USDT,XRP-USDT,BNB-USDT
MULTI_STRATEGY_SCAN_INTERVAL_SECONDS=300
```

Do not set the scan interval below 300 seconds. The scheduler scans pairs serially and prevents overlapping runs.

## HTTP endpoints

- `GET /health` — service/provider/paper-only status; used by Render.
- `GET /api/signals?limit=100` — recent AI candidates and outcomes. If `DASHBOARD_TOKEN` is configured, provide it as `?token=...` or `X-Dashboard-Token`.
- `POST /telegram/webhook` — authenticated Telegram button/callback/PDF receiver.

## Render deployment

`render.yaml` defines the single `forex-ai-analyst` web service and starts `python app.py`. Configure the Turso credentials and optional Telegram credentials in Render. Deploy this repository’s `main` branch only.

After deploy, verify:

```bash
curl https://YOUR-RENDER-SERVICE.onrender.com/health
```

Expected essentials before enabling demo orders: `"demo_only":true` and `"auto_execute_trades":false`. Upload a PDF and verify `/knowledge` before setting `AUTO_EXECUTE_TRADES=true`. Set `KILL_SWITCH=true` to prevent every new VST order; it does not force-close already-open exchange positions.

## Validation

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q .
python3 -m pip check
git diff --check
```