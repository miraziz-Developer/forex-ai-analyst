# Forex AI Analyst Bot

A personal bot: it watches EUR/USD, GBP/USD, and USD/JPY itself, 24/5, no
TradingView subscription needed. Every `POLL_INTERVAL_MINUTES`, the AI does a
full technical + fundamental read on each pair — there's no mechanical
indicator gating it, the model decides every single time. It only messages
you when its own verdict is a genuine TRADE WATCH. Every signal is logged to
a database and automatically checked against real price history, so you get
a measured win rate, not a guess. **Analysis and alerting only — it never
places, modifies, or cancels any order.**

## What's included
- `app.py` — Flask server (`/health`, `/stats`, optional webhook) and the shared analysis pipeline
- `scheduler.py` — runs the AI check on a timer, resolves open signals, sends a daily digest
- `market_data.py` — pulls OHLC bars from Twelve Data
- `notifier.py` — sends messages to Telegram
- `storage.py` — Postgres-backed signal log (entry/target/stop, outcome)
- `system_prompt.txt` — the analyst's instructions (auto-loaded by app.py)
- `pine/forex_alert.pine` — optional, only useful if you're on TradingView Essential+ (webhook alerts aren't free); not required
- `requirements.txt`, `.env.example`

## 1. Create your accounts (one-time)

### Azure AI Foundry (model API)
Already set up — put your endpoint, deployment name, and API key in `.env` as
`AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT`, `AZURE_OPENAI_API_KEY`.
Called via the OpenAI SDK's Responses API with the `web_search_preview` tool
for the fundamental-analysis step.

### Twelve Data (market data)
1. Sign up at [twelvedata.com](https://twelvedata.com) (free tier: 800 requests/day)
2. Copy your API key from the dashboard into `.env` as `TWELVEDATA_API_KEY`

### Telegram bot
1. In Telegram, message **@BotFather** → `/newbot` → follow the prompts → it gives you a token
2. Put that token in `.env` as `TELEGRAM_BOT_TOKEN`
3. Message your new bot anything (so it has a chat with you), then visit
   `https://api.telegram.org/bot<your-token>/getUpdates` — find `"chat":{"id":...}`
   and put that number in `.env` as `TELEGRAM_CHAT_ID`

### Neon (free Postgres — the signal track record)
Render's own free tier has no persistent disk (wiped on every redeploy), so
the signal log lives in a small external database instead:
1. Sign up at [neon.tech](https://neon.tech) (free tier, no credit card)
2. Create a project → copy the connection string it gives you (looks like
   `postgresql://user:pass@host/dbname?sslmode=require`)
3. Put it in `.env` as `DATABASE_URL`

The table is created automatically on first run (`storage.init_db()`).

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
variables in Render's dashboard (never commit your real `.env`).

Render's free tier sleeps after 15 minutes idle — keep it awake with a free
[UptimeRobot](https://uptimerobot.com) monitor pinging `/health` every 5 minutes.

## 4. (Optional) TradingView alerts
Only needed if you're on TradingView Essential+ and want alerts there too, in
addition to the bot's own scheduler — see `pine/forex_alert.pine` and the
`/webhook/tradingview` route. Not required for normal operation.

## 5. What happens every cycle
1. Every `POLL_INTERVAL_MINUTES`, for each pair in `WATCH_PAIRS`: check the
   trading window (`.env`) — outside it, skip without spending any API calls
2. Fetch 4H/1H/15min/5min bars for that pair from Twelve Data
3. Call the model with the system prompt + those bars + the configured spread;
   it reads the technicals, searches the web for fundamental context, and
   states its own TRADE WATCH / SKIP verdict and (if TRADE WATCH) direction
4. Only on a **new** TRADE WATCH (not a repeat of the same ongoing setup):
   send the verdict to Telegram, and log a row (pair, direction, entry, target,
   stop) to the database
5. Separately, every `RESOLVER_INTERVAL_MINUTES`: every open signal is checked
   against real price bars since it was logged — first touch of target = WIN,
   stop = LOSS; unresolved past `SIGNAL_EXPIRY_HOURS` = EXPIRED
6. Once a day, a short win-rate digest goes to Telegram

## 6. Checking the track record
`GET /stats` returns open-signal count and win/loss/expired counts + win rate,
both all-time and last 30 days. This is the actual answer to "is this
profitable" — not a guess, a measured result that gets more meaningful the
longer it runs.

## 7. Tuning things later (no code changes needed)
- **Change the analyst's behavior/wording**: edit `system_prompt.txt`, redeploy
- **Change which pairs are watched**: edit `WATCH_PAIRS` in `.env`
- **Change check frequency**: `POLL_INTERVAL_MINUTES`, `RESOLVER_INTERVAL_MINUTES` — see the budget comment in `.env.example` before lowering either
- **Change trading window/days**: `TRADING_DAYS`, `TRADING_WINDOW_START/END` in `.env` (all UTC)
- **Change pip target/stop/spread**: `RISK_REWARD_PIPS`, `SPREAD_PIPS` in `.env`
- **Change how long a signal stays open**: `SIGNAL_EXPIRY_HOURS`
- **Swap the data provider**: replace the calls in `market_data.py`; the rest only depends on the bar-list shape returned

## 8. Reliability notes
- Malformed webhook payloads return `400` and are logged — they don't crash the process
- Twelve Data or Azure OpenAI API failures are caught, logged, and skip that check without crashing
- If a signal's direction can't be parsed from the model's response, the Telegram
  message still sends, but the row isn't logged (logged as a warning instead)
- All activity is logged to `bot.log` (and stdout) for daily review

## Known limitations (be honest with yourself about these)
- The model reads OHLC numbers, not rendered charts — pattern names like "pin
  bar" are the model's numeric interpretation, not a visual read
- Outcome resolution uses 1-hour bar highs/lows, not tick data — a bar that
  contains both the target and the stop is resolved conservatively as a loss,
  which may not always match what actually happened intra-bar
- Spread is factored into the model's judgment and is a configurable constant,
  not your broker's real live spread
- None of this is investment advice or a guarantee of anything — treat `/stats`
  as a forward-test log, not a promise
