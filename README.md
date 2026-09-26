# BingX VST Donchian Trend Trader

A rule-based crypto trend follower for the BingX **VST (virtual-money demo)** exchange, plus a research lab that decides what is allowed to trade. One `main` branch, one web service:

```text
BingX public perpetual-swap closed 4h OHLCV
  → Donchian 4h breakout signal (long only)
  → position / cooldown / runtime-control gates
  → balance-relative sizing from the live VST account
  → BingX VST market order with exchange-side stop + Turso journal + Telegram notification
```

There is **no AI/LLM anywhere in the service**: every entry, stop, size and exit is a deterministic rule, so live behaviour is exactly what was backtested and there is no model API cost. The execution client is hardcoded to `open-api-vst.bingx.com`; there is no live-money endpoint or configuration switch.

## Quick start on your own server (Docker)

Requirements: a Linux server (Docker is installed for you if missing) and, for Telegram buttons/commands, a domain whose DNS `A` record points at the server.

```bash
git clone https://github.com/miraziz-Developer/forex-ai-analyst.git
cd forex-ai-analyst
cp .env.example .env      # fill in TURSO_*, BINGX_*, TELEGRAM_*, and DOMAIN
./deploy.sh
```

`deploy.sh` validates `.env`, generates `TELEGRAM_WEBHOOK_SECRET` and `DASHBOARD_TOKEN` if empty, derives `PUBLIC_BASE_URL` from `DOMAIN`, builds the image, starts the app (plus a Caddy reverse proxy with automatic Let's Encrypt HTTPS when `DOMAIN` is set), and waits for `/health`. Other commands: `./deploy.sh update` (git pull + rebuild), `logs`, `status`, `stop`. Without `DOMAIN` the app binds to `127.0.0.1` only.

## Project layout

```text
app.py                      process entrypoint (Render / Docker)
src/forex_ai_analyst/
  interfaces/               Flask HTTP API, Telegram button bot
  trading/
    application/            Donchian trend engine, scheduler (scan, exits, reconciliation)
    domain/                 models, indicators, regime; research-only risk/quality/strategies
    infrastructure/         BingX VST broker, market data, Turso signal repository
  operations/               incidents/alerts, runtime controls
  knowledge/                PDF knowledge base (Telegram upload + search)
  lab/                      walk-forward strategy lab (docs/LAB_REPORT.md)
  research/
    edge_lab/               pre-registered hypothesis lab (see below)
    backtest.py, production_backtest.py   legacy deterministic replays
  shared/                   Turso client, Telegram notifier
tests/                      unit tests
docs/                       lab report and research notes
research_output/edge_lab/   sealed hypothesis manifests, data-source manifest, append-only ledger
Dockerfile, docker-compose.yml, deploy.sh, deploy/Caddyfile   self-hosting
render.yaml                 Render blueprint
```

## Strategy: Donchian 4h long

The live signal uses the exact function the lab backtested (`forex_ai_analyst.lab.strategies.donchian`):

- **Entry:** a closed 4h candle closes above the prior `DONCHIAN_ENTRY_N` (100) bar high. Long only; shorts lost money in research.
- **Stop:** exchange-side stop-market at `DONCHIAN_STOP_ATR` (3) × ATR below the entry close. If the market order fills at or through the stop, the position is closed immediately and alerted.
- **Exit:** the stop, or a closed 4h candle below the `DONCHIAN_EXIT_N` (20) bar low (closed at market). There is no fixed take-profit; a 60-day maximum hold is a safety net only.
- **Markets:** BTC, ETH, SOL, XRP, BNB (lab) plus DOGE, ADA, LINK, AVAX, LTC, which passed a pre-registered out-of-sample test with unchanged parameters ([`docs/DONCHIAN_NEW_MARKETS.md`](docs/DONCHIAN_NEW_MARKETS.md)). About 100 trades a year across the ten.
- **Gates:** one position per pair, at most `MAX_CONCURRENT_POSITIONS` (6) open, `TRADE_COOLDOWN_MINUTES` (60) per pair after a close, each 4h breakout traded at most once, runtime kill switch and blocked pairs.
- **Sizing:** risk per trade = live VST equity × `risk_per_trade_pct`, capped by the remaining daily-loss budget and by available margin × `max_margin_utilization_pct`. A fresh VST balance is required; if it cannot be fetched, the scan is skipped (the system never guesses equity).

Evidence and its limits are in [`docs/LAB_REPORT.md`](docs/LAB_REPORT.md): out-of-sample 2023-2026 Sharpe 1.28, CAGR 9.5%, max drawdown -6.1% at about 0.2% of total equity risked per trade, but a deflated Sharpe of 0.83 misses the 0.90 promotion gate. This is a **VST forward test**, not a proven edge. Runtime `RISK PCT 1` risks about 5× the research sizing per trade; `RISK PCT 0.2`–`0.5` stays near the researched drawdown. Leverage does not change the risk per trade (the stop distance and quantity do); it only changes the margin used.

## Edge lab (pre-registered research)

`forex_ai_analyst.research.edge_lab` tests new trading ideas without being able to fool itself. It never imports live execution code (enforced by a test).

- Every hypothesis is an immutable manifest (thesis, markets, periods, parameter grid, cost model, gates) registered with a content hash before any data is examined. Changing anything is a new version; the old one is superseded in the append-only ledger.
- Data comes from Binance public archives (perpetual/spot/premium klines, funding, 5-minute open interest) with a point-in-time contract: every value carries the time it became available, and features only use data available at the decision bar close.
- Stages are gated in order: event study → trading simulation → walk-forward with purge/embargo, deflated Sharpe and cost stress → a single-use holdout → VST shadow. The best possible verdict is `ELIGIBLE_FOR_SHADOW`; the lab can never approve live money.

```bash
forex-edge-lab register    --hypothesis crowding_exhaustion_v2
forex-edge-lab feasibility --hypothesis crowding_exhaustion_v2
forex-edge-lab events      --hypothesis crowding_exhaustion_v2   # one-shot; refuses a re-run
```

Results are written under `research_output/edge_lab/`; manifests and the ledger are committed as audit evidence.

## Setup

```bash
cp .env.example .env
# Set TURSO_DATABASE_URL and TURSO_AUTH_TOKEN; Telegram and BingX VST keys are optional.
python3 -m pip install .
python3 app.py
```

The app creates and migrates its own tables (signal journal, decisions, daily risk state, incidents, runtime controls, knowledge documents) in Turso. Render's disk is ephemeral, so all state lives in Turso.

## Telegram

Only the chat IDs in `TELEGRAM_CHAT_ID` are authorized. Send `/start` to open the button panel: status, recent signals, open VST positions, closed orders, P&L, knowledge base and PDF upload. **Foyda / zarar** shows the local journal separately from BingX's API-reported account income; account income can include manual activity, so it is never attributed to individual journal orders.

Runtime controls are strictly allowlisted: `STOP`, `START DEMO`, `BLOCK BTC-USDT`, `UNBLOCK BTC-USDT`, `RISK PCT 0.5`, `DAILY LOSS PCT 5`, `MARGIN PCT 25`. The bot replies with a preview and a random single-use code; send `TASDIQLAYMAN <code>` within 10 minutes to apply it. Every preview and change is audited in Turso. Controls cannot enable live-money trading or change code, credentials or broker endpoints.

Set `PUBLIC_BASE_URL` (HTTPS) and `TELEGRAM_WEBHOOK_SECRET`; the webhook and command menu are registered automatically at startup. Manual fallback:

```bash
curl -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" -d "url=https://YOUR-SERVICE/telegram/webhook" -d "secret_token=${TELEGRAM_WEBHOOK_SECRET}" -d 'allowed_updates=["message","channel_post","callback_query"]'
```

## Operations and safety

- Only fully closed candles are used. Market data is BingX's public REST API; no account is needed for data.
- Every open VST row is reconciled against the broker on each run: a missing position is closed in the journal only when a single, fully matching close fill exists in BingX order history; anything ambiguous stays open and raises a CRITICAL incident.
- A position still open past its planned expiry plus `VST_OPEN_SLA_MINUTES` raises a warning.
- **Degradation alarm:** every hour, closed Donchian trades are measured in R (P&L / risk taken) and compared with the backtest of the same configuration (575 trades on the ten markets: 34% wins, mean +0.67R, worst losing run 18, worst drawdown -34R). Eleven losses in a row or -14R from the peak raises a WARNING; 15 in a row or -17R raises a CRITICAL alert recommending `STOP`. It alerts only; the operator decides.
- Closed VST rows are reconciled by order ID; P&L, fees and funding are stored only when BingX reports them for that order, never estimated. BingX VST often omits order-level values; such rows are marked `UNAVAILABLE` and the journal P&L is used, without raising an incident.
- Incidents are durable, deduplicated and sent to Telegram; `/health` shows the open count and a breakdown by type without failing liveness. A housekeeping job (startup and every 6 hours) closes per-row incidents whose journal row has already closed; `stale-stop` incidents always need a manual check on BingX.
- `KILL_SWITCH=true` (or Telegram `STOP`) blocks every new order; it does not close open positions. Create BingX keys with **no withdrawal permission**.

## HTTP endpoints

- `GET /health` — service status, `trade_readiness` (blockers such as disabled auto-execution, missing BingX keys, or a kill switch), incidents, and safe VST account diagnostics (never credentials or balances).
- `GET /api/signals?limit=100` — recent journal rows. With `DASHBOARD_TOKEN` set, pass `?token=...` or `X-Dashboard-Token`.
- `GET /api/reconciliation` — broker reconciliation status (same token rule).
- `POST /telegram/webhook` — authenticated Telegram receiver.

## Render deployment

`render.yaml` defines the single `forex-ai-analyst` web service (`pip install .`, `python app.py`, health check `/health`, auto-deploy on `main`). Set `TURSO_*`, `TELEGRAM_*`, `PUBLIC_BASE_URL`, and, to place VST demo orders, `AUTO_EXECUTE_TRADES=true`, `BINGX_API_KEY`, `BINGX_SECRET`. After deploy, `/health` should show `"demo_only": true` and `trade_readiness.ready: true` with no blockers. If `vst_account.available` is `false`, check its sanitized `category`, `http_status` and `bingx_code`: the VST key pair, swap read/trade permission, and any IP allowlist.

## Live-money boundary

This repository deliberately has **no live BingX endpoint, live credential variable, or configuration switch**, and must not be represented as a real-money platform. A live service would need its own security review, separate credentials, hard notional and loss limits, audited rollback, independent monitoring, and a strategy that has passed the edge lab and a VST forward test with realistic costs. Nothing here is financial advice or a promise of profit.

## Legacy research replays

`forex_ai_analyst.research.backtest` and `production_backtest` replay the older deterministic 5m/15m strategies (entry at next open, round-trip costs, `LOSS` when a candle touches both levels). They are not on the live path.

## Validation

```bash
python3 -m unittest discover -s tests
python3 -m compileall -q src tests app.py
python3 -m pip check
```
