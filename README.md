# Crypto AI Analyst Bot

A personal bot: it watches BTC-USDT, ETH-USDT, SOL-USDT, XRP-USDT, and
BNB-USDT perpetual futures itself, 24/7, no exchange dashboard needed. Every
`POLL_INTERVAL_MINUTES`, the AI does a full read on each pair — market
structure, Smart Money Concepts (order blocks, FVGs, liquidity, BOS/CHoCH),
institutional positioning (funding rate, open interest trend, order book
imbalance), and an actual visual read of rendered charts — combined into one
TRADE WATCH / SKIP verdict. There's no mechanical indicator gating it; the
model decides every single time, using the daily/macro trend and
institutional data as *confirmation*, not independent gates. It only
messages you when its verdict is a genuine TRADE WATCH. Every signal is
logged to a database and automatically checked against real price history
(or the real broker outcome, if execution is on), so you get a measured win
rate — and real USDT P&L — not a guess. **By default it's analysis/alerting
only — it does not place orders unless you explicitly turn on
`AUTO_EXECUTE_TRADES`, and even then only on a BingX demo (VST / virtual
USDT) account. See section 5.**

## What's included
- `app.py` — Flask server (`/health`, `/stats`) and the shared analysis pipeline
- `scheduler.py` — runs the AI check on a timer, resolves open signals, sends a daily digest
- `market_data.py` — pulls OHLC bars from BingX's free public kline endpoint (no key needed, cached/rate-limit-aware)
- `indicators.py` — ATR (Average True Range) for volatility-adjusted sanity bounds
- `institutional_data.py` — funding rate, open interest trend, order book imbalance from BingX
- `charting.py` — renders real candlestick+volume chart images (mplfinance) for the model to actually look at
- `notifier.py` — sends messages to Telegram
- `storage.py` — Turso (libSQL)-backed signal log (entry/target/stop, outcome, broker order id/qty, realized P&L)
- `broker.py` — optional BingX demo execution (only used if `AUTO_EXECUTE_TRADES=true`)
- `system_prompt.txt` — the analyst's full instructions (auto-loaded by app.py)
- `backtest.py` — replays the same AI analyst against real historical data for a much faster (hours, not weeks) directional read — see section 9
- `requirements.txt`, `.env.example`

## Why crypto, not forex
This started as a forex bot. Every forex-broker demo API path we tried for
your region hit a real wall: XM has no API at all, OANDA routes your region
to an MT5-only entity, Deriv's newer API had a broken self-serve
app-registration flow, cTrader needs a 1-2 day manual approval. BingX's demo
(VST) trading API is instant, free, and fully self-serve — so the whole
pipeline (analysis + execution + tracking) runs on crypto perpetuals instead.

## 1. Create your accounts (one-time)

### Azure AI Foundry (model API)
Put your endpoint, deployment name, and API key in `.env` as
`AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT`, `AZURE_OPENAI_API_KEY`.
Called via the OpenAI SDK's Responses API — `web_search_preview` tool for
fundamentals, multi-part input (text + chart images) for the visual read.

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
- **Render.com** (recommended) — free tier, connects to a GitHub repo
- A small VPS (DigitalOcean, Linode, ~$5/mo) if you want more control

Push this folder to a GitHub repo, connect it on Render, set all the `.env`
variables in Render's dashboard (never commit your real `.env`). If
`BINGX_API_KEY`/`BINGX_SECRET` aren't set, the bot just runs analysis-only.

**Auto-deploy caveat (learned the hard way):** Render's "Auto-Deploy: On
Commit" setting can silently stop firing on GitHub push (happened here after
the repo's visibility changed) with zero error shown anywhere — Render just
keeps serving the last successful build forever. **Don't trust that a push
redeployed just because `/health` still returns 200** — that only proves
*some* build is running, not the *latest* one. Verify against something that
actually changed (a new field in `/stats`, a log line, etc.), or better: grab
the **Deploy Hook** URL (Settings → Deploy → Deploy Hook) and `curl` it after
every push — it triggers a deploy directly, bypassing the GitHub webhook
entirely, so it can't silently break the same way.

Render's free tier also sleeps after 15 minutes idle — keep it awake with a
free [UptimeRobot](https://uptimerobot.com) monitor pinging `/health` every
5 minutes.

## 4. What happens every cycle
1. Every `POLL_INTERVAL_MINUTES`, for each pair in `WATCH_PAIRS`: check the
   trading window (`.env`, defaults to no restriction — crypto trades 24/7)
2. Fetch 1D/4H/1H/15min/5min bars from BingX, plus funding rate/open
   interest trend/order book imbalance, plus render 1H/15min chart images
3. Call the model with all of that; it works through macro trend → market
   structure → chop check → SMC/price-action scan → key levels →
   institutional positioning → **structural stop/target** (real order-block/
   liquidity/S-R price levels, not a formula) → fundamentals, and states its
   verdict, direction, and (if TRADE WATCH) the actual stop/target prices
4. Only on a **new** TRADE WATCH for a pair that doesn't already have an open
   signal (DB-backed check — survives restarts): place a BingX demo order if
   `AUTO_EXECUTE_TRADES=true` using the model's own structural prices (an
   ATR-based percentage is only a sanity bound, used as a fallback if the
   model's numbers look wrong), send the verdict + execution status to
   Telegram, log a row to the database
5. Separately, every `RESOLVER_INTERVAL_MINUTES`: every open signal is
   resolved — from the real BingX order outcome if one exists, otherwise
   from price bars since it was logged; unresolved past `SIGNAL_EXPIRY_HOURS`
   = EXPIRED (force-closing the real position first, if one exists)
6. Once a day, a win-rate + realized-P&L digest goes to Telegram

## 5. Execution details (BingX demo)
With `AUTO_EXECUTE_TRADES=true`:
- Leverage is set to `LEVERAGE` (default 3x) before each order
- Position size is `POSITION_SIZE_USDT` (default 100) worth of margin,
  converted to a contract quantity at the current price
- No cap on concurrent open positions (demo money — removed deliberately);
  the only per-pair limit is one open signal at a time (a pair won't
  re-signal until its current one resolves)
- Stop-loss/take-profit are the model's own structural price levels
  (falls back to an ATR-based percentage only if those fail a sanity check),
  attached to the order atomically at open — BingX's own engine executes
  them, not this app polling
- Every execution attempt (success or failure) is included in the Telegram message

## 6. Checking the track record
`GET /stats` returns open-signal count, win/loss/expired counts, win rate,
and **realized_pnl_usdt** (real BingX demo P&L from executed trades only) —
both all-time and last 30 days. The P&L figure is the more direct answer to
"is this profitable" than win rate alone, since position sizing and
stop/target distance now vary per trade.

## 7. Tuning things later (no code changes needed)
- **Change the analyst's behavior/wording**: edit `system_prompt.txt`, redeploy
- **Change which pairs are watched**: edit `WATCH_PAIRS` in `.env` and add the pair's `QUANTITY_PRECISION` in `broker.py`
- **Change check frequency**: `POLL_INTERVAL_MINUTES`, `RESOLVER_INTERVAL_MINUTES` — see the rate-limit comment in `.env.example` before lowering either or adding pairs
- **Change trading window/days**: `TRADING_DAYS`, `TRADING_WINDOW_START/END` in `.env` (all UTC)
- **Change the sanity-bound target/stop**: `ATR_MULTIPLIER`, `REWARD_RISK_RATIO`, or the `TARGET_PCT_STOP_PCT` fallback
- **Change how long a signal stays open**: `SIGNAL_EXPIRY_HOURS`
- **Change position size / leverage**: `POSITION_SIZE_USDT`, `LEVERAGE`

## 8. Reliability notes
- BingX, Azure OpenAI, or Turso API failures are caught, logged, and skip that step without crashing
- BingX's kline endpoint is rate-limited (5 req/15min) — cached per timeframe's own bar period, with an automatic precise-wait retry if still hit
- Signal dedup (don't double-signal an open pair) is DB-backed, not in-memory — survives restarts/redeploys
- If a signal's direction/prices can't be parsed from the model's response, the Telegram
  message still sends, but the row isn't logged (or falls back to the ATR-based calc)
- If execution is enabled but the order fails, the signal is still logged (without a broker order id) and the Telegram message says so
- All activity is logged to `bot.log` (and stdout) for daily review

## 9. Backtesting
```bash
python backtest.py --pairs BTC-USDT,ETH-USDT --checkpoint-hours 3 \
    --min-age-days 0 --max-age-days 15 --history-pages 2 --workers 8
```
Replays the exact same system prompt against real historical bars (no web
search / institutional data / chart images at each historical point — those
can't be reconstructed for the past), resolving each hypothetical signal
against real forward price action. Useful for a much faster directional read
than waiting on live `/stats`, and for finding real, generalizable patterns
in what's winning vs. losing (pull `backtest_results.json` and look at the
`analysis` text of the losses) — **not** for repeatedly re-tuning the prompt
against the same window until a number looks good; that's overfitting to
noise, and will likely make live performance worse, not better. Always
sanity-check any prompt change against a *different* time window than the
one that motivated it before trusting it.

## Known limitations (be honest with yourself about these)
- The model gets both raw OHLC numbers and rendered chart images, but it's
  still an LLM's read of a chart, not a human trader's — treat pattern names
  like "order block" as its structured interpretation, not ground truth
- Outcome resolution (for signals without a broker order) uses 1-hour bar
  highs/lows, not tick data — a bar that contains both the target and the
  stop is resolved conservatively as a loss
- Leverage amplifies both gains and losses — 3x default is conservative but not zero-risk, even on demo
- Backtest win rates vary meaningfully by market regime (a strongly trending
  window vs. a choppy one) — a single run is a data point, not a verdict
- This is demo/virtual-money trading. None of this is investment advice or a
  guarantee of anything — treat `/stats` as a forward-test log, not a promise
