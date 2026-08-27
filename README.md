# Crypto AI Analyst Bot

A personal bot: it watches BTC-USDT and ETH-USDT perpetual futures itself,
24/7, no exchange dashboard needed. Every `POLL_INTERVAL_MINUTES`, the AI does
a full technical + fundamental read on each pair — there's no mechanical
indicator gating it, the model decides every single time. It only messages
you when its own verdict is a genuine TRADE WATCH. Every signal is logged to
a database and automatically checked against real price history (or the real
broker outcome, if execution is on), so you get a measured win rate, not a
guess. **By default it's analysis/alerting only — it does not place orders
unless you explicitly turn on `AUTO_EXECUTE_TRADES`, and even then only on a
BingX demo (VST / virtual USDT) account. See section 5.**

## What's included
- `app.py` — Flask server (`/health`, `/stats`) and the shared analysis pipeline
- `scheduler.py` — runs the AI check on a timer, resolves open signals, sends a daily digest
- `market_data.py` — pulls OHLC bars from BingX's free public kline endpoint (no key needed)
- `notifier.py` — sends messages to Telegram
- `storage.py` — Turso (libSQL)-backed signal log (entry/target/stop, outcome, broker order id/qty)
- `broker.py` — optional BingX demo execution (only used if `AUTO_EXECUTE_TRADES=true`)
- `system_prompt.txt` — the analyst's instructions (auto-loaded by app.py)
- `requirements.txt`, `.env.example`

## Why crypto, not forex
This started as a forex bot. Every forex-broker demo API path we tried for
your region hit a real wall: XM has no API at all, OANDA routes your region to
an MT5-only entity, Deriv's newer API had a broken self-serve app-registration
flow, cTrader needs a 1-2 day manual approval. BingX's demo (VST) trading API
is instant, free, and fully self-serve — so the whole pipeline (analysis +
execution + tracking) now runs on crypto perpetuals instead. Same
architecture, different market.

## 1. Create your accounts (one-time)

### Azure AI Foundry (model API)
Already set up — put your endpoint, deployment name, and API key in `.env` as
`AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT`, `AZURE_OPENAI_API_KEY`.
Called via the OpenAI SDK's Responses API with the `web_search_preview` tool
for the fundamental-analysis step.

### Telegram bot
1. In Telegram, message **@BotFather** → `/newbot` → follow the prompts → it gives you a token
2. Put that token in `.env` as `TELEGRAM_BOT_TOKEN`
3. Message your new bot anything (so it has a chat with you), then visit
   `https://api.telegram.org/bot<your-token>/getUpdates` — find `"chat":{"id":...}`
   and put that number in `.env` as `TELEGRAM_CHAT_ID`

### Turso (free database — the signal track record)
Render's own free tier has no persistent disk (wiped on every redeploy), so
the signal log lives in a small external database instead:
1. Sign up at [turso.tech](https://turso.tech) (free tier, no credit card)
2. Create a database (dashboard → **Create Database**)
3. On the database's page: click **Connect** for the `libsql://...` URL, and
   **Create Token** for an auth token
4. Put both in `.env` as `TURSO_DATABASE_URL` and `TURSO_AUTH_TOKEN`

The table is created automatically on first run (`storage.init_db()`) via
Turso's HTTP API — no native database driver needed.

### BingX (demo execution — optional, see section 5)
1. Sign up at [bingx.com](https://bingx.com), no KYC needed for demo trading
2. **Account → API Management** → create a key with **Read** + **Perpetual
   Futures Trading** permissions (leave **Withdraw** unchecked)
3. Put the key/secret in `.env` as `BINGX_API_KEY` / `BINGX_SECRET`

**Critical fact about BingX**: the same key/secret works on both the demo
(VST) and real-money accounts — the only thing that separates them is which
API domain a request goes to. `broker.py` hardcodes the demo domain
(`open-api-vst.bingx.com`) with no config flag or env var that can change it
to the live domain (`open-api.bingx.com`). Switching to real money would mean
deliberately editing that source file, never a `.env` change.

## 2. Local setup
```bash
cd forex-ai-analyst
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# fill in the keys/IDs above
```
Run it:
```bash
export $(cat .env | xargs)
python app.py
```
This starts the Flask server, the AI-check scheduler, and the signal
resolver. Test it:
```bash
curl http://localhost:5000/health
curl http://localhost:5000/stats
```

## 3. Deploy (so it keeps running without your computer on)
- **Render.com** (recommended) — free tier, connects to a GitHub repo, auto-deploys on push
- A small VPS (DigitalOcean, Linode, ~$5/mo) if you want more control

Push this folder to a GitHub repo, connect it on Render, set all the `.env`
variables in Render's dashboard (never commit your real `.env`). If
`BINGX_API_KEY`/`BINGX_SECRET` aren't set, the bot just runs analysis-only —
it won't crash, it simply can't execute.

Render's free tier sleeps after 15 minutes idle — keep it awake with a free
[UptimeRobot](https://uptimerobot.com) monitor pinging `/health` every 5 minutes.

## 4. What happens every cycle
1. Every `POLL_INTERVAL_MINUTES`, for each pair in `WATCH_PAIRS`: check the
   trading window (`.env`, defaults to no restriction — crypto trades 24/7)
2. Fetch 4H/1H/15min/5min bars for that pair from BingX
3. Call the model with the system prompt + those bars + the configured
   target/stop percentages; it reads the technicals, searches the web for
   fundamental/macro context, and states its own TRADE WATCH / SKIP verdict
   and (if TRADE WATCH) direction
4. Only on a **new** TRADE WATCH (not a repeat of the same ongoing setup):
   compute entry/target/stop, place a BingX demo order if
   `AUTO_EXECUTE_TRADES=true`, send the verdict + execution status to
   Telegram, and log a row to the database
5. Separately, every `RESOLVER_INTERVAL_MINUTES`: every open signal is
   checked against real price bars since it was logged (first touch of
   target = WIN, stop = LOSS); unresolved past `SIGNAL_EXPIRY_HOURS` = EXPIRED
   (force-closing the real BingX position first, if one exists)
6. Once a day, a short win-rate digest goes to Telegram

## 5. Execution details (BingX demo)
With `AUTO_EXECUTE_TRADES=true`:
- Leverage is set to `LEVERAGE` (default 3x) before each order
- Position size is `POSITION_SIZE_USDT` (default 100) worth of margin,
  converted to a contract quantity at the current price
- Capped at `MAX_OPEN_POSITIONS` (default 3) concurrent positions
- Take-profit/stop-loss are attached to the order atomically at open —
  BingX's own engine executes them, not this app polling
- Every execution attempt (success or failure) is included in the Telegram message

## 6. Checking the track record
`GET /stats` returns open-signal count and win/loss/expired counts + win rate,
both all-time and last 30 days. This is the actual answer to "is this
profitable" — not a guess, a measured result that gets more meaningful the
longer it runs.

## 7. Tuning things later (no code changes needed)
- **Change the analyst's behavior/wording**: edit `system_prompt.txt`, redeploy
- **Change which pairs are watched**: edit `WATCH_PAIRS` in `.env` (BingX symbol format, e.g. `SOL-USDT`)
- **Change check frequency**: `POLL_INTERVAL_MINUTES`, `RESOLVER_INTERVAL_MINUTES`
- **Change trading window/days**: `TRADING_DAYS`, `TRADING_WINDOW_START/END` in `.env` (all UTC)
- **Change target/stop**: `TARGET_PCT_STOP_PCT` in `.env` (e.g. `2.0/0.8`)
- **Change how long a signal stays open**: `SIGNAL_EXPIRY_HOURS`
- **Change position size / leverage / concurrent trade cap**: `POSITION_SIZE_USDT`, `LEVERAGE`, `MAX_OPEN_POSITIONS`
- **Add a pair**: add it to `WATCH_PAIRS` and to `QUANTITY_PRECISION` in `broker.py`

## 8. Reliability notes
- BingX, Azure OpenAI, or Turso API failures are caught, logged, and skip that step without crashing
- If a signal's direction can't be parsed from the model's response, the Telegram
  message still sends, but the row isn't logged (logged as a warning instead)
- If execution is enabled but the order fails (rejected, connection error, etc.),
  the signal is still logged (without a broker order id) and the Telegram message says so
- All activity is logged to `bot.log` (and stdout) for daily review

## Known limitations (be honest with yourself about these)
- The model reads OHLC numbers, not rendered charts — pattern names like "pin
  bar" are the model's numeric interpretation, not a visual read
- Outcome resolution (for signals without a broker order) uses 1-hour bar
  highs/lows, not tick data — a bar that contains both the target and the
  stop is resolved conservatively as a loss
- Position sizing is a fixed `POSITION_SIZE_USDT`, not risk-based on account balance
- Leverage amplifies both gains and losses — 3x default is conservative but not zero-risk, even on demo
- This is demo/virtual-money trading. None of this is investment advice or a
  guarantee of anything — treat `/stats` as a forward-test log, not a promise
# deploy trigger 1787851195
