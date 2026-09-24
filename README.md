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

## Quick start on your own server (Docker)

Requirements: a Linux server (Docker is installed for you if missing) and, for Telegram buttons/commands, a domain whose DNS `A` record points at the server.

```bash
git clone https://github.com/miraziz-Developer/forex-ai-analyst.git
cd forex-ai-analyst
cp .env.example .env      # fill in TURSO_*, BINGX_*, OpenAI/Azure, TELEGRAM_*, and DOMAIN
./deploy.sh
```

`deploy.sh` validates `.env`, generates `TELEGRAM_WEBHOOK_SECRET` and `DASHBOARD_TOKEN` if empty, derives `PUBLIC_BASE_URL` from `DOMAIN`, builds the image, starts the app (plus a Caddy reverse proxy with automatic Let's Encrypt HTTPS when `DOMAIN` is set), and waits for `/health`. Other commands: `./deploy.sh update` (git pull + rebuild), `logs`, `status`, `stop`. Without `DOMAIN` the app binds to `127.0.0.1` only.

## Project layout

```text
app.py                      process entrypoint (Render / Docker)
src/forex_ai_analyst/
  interfaces/               Flask HTTP API, Telegram bot + chat
  trading/
    application/            AI decision engine, scheduler, outcome learning
    domain/                 models, indicators, regime, risk/quality, strategies (research)
    infrastructure/         BingX broker, market data, signal repository, intelligence feeds
  operations/               incidents/alerts, runtime controls
  knowledge/                PDF knowledge base
  research/                 backtest / walk-forward tooling (not on the live path)
  shared/                   Turso client, Telegram notifier
tests/                      unit tests (python3 -m unittest discover -s tests)
docs/                       research notes
Dockerfile, docker-compose.yml, deploy.sh, deploy/Caddyfile   self-hosting
render.yaml                 Render blueprint
```

## Contextual AI controls

- The model receives closed 5m/15m/1h candles, regime features, funding/open-interest/order-book context, retrieved PDF excerpts, earlier outcome reviews, and a bounded VST account snapshot (equity, available margin, unrealized PnL, strategy exposure and daily strategy PnL).
- It may select `SKIP`, `WATCH`, or `PROPOSE_TRADE`; for a proposal it dynamically chooses direction, entry, stop, target, risk, leverage and cooldown.
- Risk is balance-relative, rather than a fixed USDT cap: the accepted risk is capped by current VST equity × `risk_per_trade_pct`, remaining daily loss budget (`max_daily_loss_pct`), and available-margin utilization (`max_margin_utilization_pct`). There is no application-level leverage cap below BingX's 125x technical validation and no minimum cooldown; the AI selects both per trade.
- A valid fresh VST balance snapshot is required before a proposal can be journaled or executed. If it cannot be fetched, the result is `SKIP`; the system never guesses account equity.
- Model output is JSON-validated and invalid/API-unavailable output always becomes `SKIP` (no trade).
- A code-level (non-LLM) gate independently blocks a proposal whose direction contradicts a determined 1h/4h/1d EMA20/EMA50 trend bias, whose 15m regime is `HIGH_VOLATILITY`/`UNCERTAIN`, or whose stated confidence is below `AI_MIN_TRADE_CONFIDENCE` (default 70) — the AI's own rationale cannot argue past this. Further code-level gates prevent order floods: a pre-flight check rejects a trade whose reward:risk at the *current* price is already below 1.3 (so it is not opened and instantly closed), only one position per pair may be open, at most `MAX_CONCURRENT_POSITIONS` (default 3) overall, and each pair rests `TRADE_COOLDOWN_MINUTES` (default 60) after a close. Routine rejections are logged, not alerted. See [`docs/RESEARCH_FINDINGS.md`](docs/RESEARCH_FINDINGS.md).
- BingX perpetual-swap public REST klines; no BingX account or API key is required. This is market data only; VST order execution remains separately restricted to the demo endpoint.
- Only fully closed candles are used. Same-candle fingerprints are persisted and rejected.
- Technical safety remains: allowed-pair whitelist, 1–125x leverage validation, structurally valid trade levels, exchange-side TP/SL, `KILL_SWITCH`, idempotency and fail-closed broker/data errors. Create BingX keys with **no withdrawal permission**.
- Outcome resolution is candle-based and conservative: when a candle touches both stop and target, it records `LOSS`.
- Telegram's **Foyda / zarar** button shows the local candle-based AI journal separately from BingX VST's API-reported account income. BingX income is account-scoped (manual/other-bot activity can be included), so it is never falsely attributed to an individual AI journal order.
- Each VST time-close retains the immutable entry and close order IDs plus confirmed fills. On every scheduler start/run, closed VST rows are retried for order-ID reconciliation. A row becomes `VERIFIED` only if BingX's matching order response explicitly returns its P&L and commission fields; otherwise it remains `UNAVAILABLE` and an operational alert is retained. Funding and account-level income are never guessed or allocated to a signal.
- Failed time closes, absent broker positions, reconciliation failures, and unavailable order-level values create durable, deduplicated VST incidents and Telegram alerts (when Telegram credentials are configured). `/health` exposes the current incident diagnostic without failing the web-service liveness check.

## Setup

```bash
cp .env.example .env
# Set TURSO_DATABASE_URL and TURSO_AUTH_TOKEN.
# Telegram credentials are optional but required for paper alerts.
python3 -m pip install .
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

Free text is also an Uzbek read-only AI chat: ask about signals, skipped trades, regime, P&L, learning, RSS/news, reconciliation, or current system state. The chat receives only a bounded operational snapshot and cannot place an order or write settings itself.

Authorized users can request strictly allowlisted runtime controls with explicit text: `STOP`, `START DEMO`, `BLOCK BTC-USDT`, `UNBLOCK BTC-USDT`, `RISK PCT 1.5`, `DAILY LOSS PCT 5`, or `MARGIN PCT 25`. The bot always sends a preview and a random, single-use confirmation code; reply `TASDIQLAYMAN <code>` within 10 minutes to apply it. Every preview, rejection, and applied update is recorded in Turso. Controls apply only to subsequent AI/VST decisions and cannot enable live-money trading, change code/model prompts, credentials, or broker endpoints. There is no fixed-USDT risk cap or leverage cap below BingX's 1–125x technical range — risk, leverage and cooldown remain balance-relative and AI-selected per trade, as described above.

Set `PUBLIC_BASE_URL` to the deployed HTTPS URL and `TELEGRAM_WEBHOOK_SECRET`; the service registers its webhook, callback updates, and command menu automatically on startup. If automatic setup is unavailable, register it manually once:

```bash
curl -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
  -d "url=https://YOUR-RENDER-SERVICE.onrender.com/telegram/webhook" \
  -d "secret_token=${TELEGRAM_WEBHOOK_SECRET}" \
  -d 'allowed_updates=["message","channel_post","callback_query"]'
```

The endpoint verifies Telegram's `X-Telegram-Bot-Api-Secret-Token` header. `callback_query` is required for the button panel. PDF metadata and extracted text are persisted; binary PDFs are not written to Render's ephemeral filesystem.

## Legacy deterministic replay (research-only)

The `forex_ai_analyst.research.backtest` and `forex_ai_analyst.research.production_backtest` modules remain available for historical deterministic-strategy research. They are not the deployed contextual AI/VST decision path and their fixed research limits do not override an AI trade decision. Their evaluator receives history only through the signal candle, enters at the next candle open, applies configurable round-trip fee/slippage/funding costs, records stale trades as `TIME_EXIT`, and resolves an OHLC candle touching both levels as `LOSS`.

Use `metrics(trades)` to report trade count, win rate, after-cost net P&L, expectancy, profit factor, maximum drawdown, and average holding time. Review results independently by `strategy`, `pair`, and `regime`; no strategy may be promoted beyond paper operation solely from an in-sample win rate.

For independent chronological validation, split a fixed period into equal account resets rather than relying on its aggregate result:

```bash
python3 -m forex_ai_analyst.research.production_backtest --start 2026-06-01 --days 90 \
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

- `GET /health` — service/provider/VST-only status plus non-fatal execution-incident diagnostics; used by Render. `trade_readiness.blockers` reports safe configuration/runtime reasons that prevent new VST orders (for example disabled auto-execution, missing credentials/provider, or a kill switch). `vst_account` contains only safe account-query diagnostics (`available`, check time, failure category, HTTP status and BingX code/message), never credentials, signatures or balances.
- `GET /api/signals?limit=100` — recent AI candidates and outcomes. If `DASHBOARD_TOKEN` is configured, provide it as `?token=...` or `X-Dashboard-Token`.
- `POST /telegram/webhook` — authenticated Telegram button/callback/PDF receiver.

## Render deployment

`render.yaml` installs the package and defines the single `forex-ai-analyst` web service, started by the root `app.py` composition root. Configure the Turso credentials and optional Telegram credentials in Render. Deploy this repository’s `main` branch only.

After deploy, verify:

```bash
curl https://YOUR-RENDER-SERVICE.onrender.com/health
```

Expected essentials before enabling demo orders: `"demo_only":true` and `"auto_execute_trades":false`. Upload a PDF and verify `/knowledge` before setting `AUTO_EXECUTE_TRADES=true`. Set `KILL_SWITCH=true` to prevent every new VST order; it does not force-close already-open exchange positions.

When demo execution is enabled, verify `vst_account.available` becomes `true`. If it is `false`, inspect its sanitized `category`, `http_status`, `bingx_code`, and `bingx_msg`: confirm the VST/demo key pair, Swap/Futures read/trade permissions, and any IP allowlist for Render. While this account query is unavailable, one account-level incident is reported and the scheduler deliberately skips all AI calls and new orders for that scan cycle.

## Live-money boundary

This repository deliberately has **no live BingX endpoint, live credential variable, or configuration switch**. It must not be represented as a real-money execution platform. A future live-only service requires an independent security review and release, separate credentials/secrets rotation, explicit multi-step operator approval, hard maximum notional/per-trade/daily-loss limits, audited emergency rollback, and independent monitoring. VST forward testing with realistic fees, slippage, funding, restarts and API failures remains required before that work.

## Validation

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q src tests app.py
python3 -m pip check
git diff --check
```