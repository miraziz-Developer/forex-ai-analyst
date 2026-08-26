# Forex AI Analyst Bot

A personal bot: it watches EUR/USD, GBP/USD, and USD/JPY itself (no
TradingView subscription needed), and when price crosses a 21-EMA on the
1-hour chart, it pulls real OHLC history, an AI model does a technical +
fundamental read, and the verdict lands in your Telegram. **Analysis and
alerting only — it never places, modifies, or cancels any order.**

## What's included
- `app.py` — Flask server (health check + optional webhook) and the shared analysis pipeline
- `scheduler.py` — polls Twelve Data on a timer and detects the EMA crossover itself
- `indicators.py` — EMA + crossover detection (mirrors what the old Pine Script did)
- `market_data.py` — pulls OHLC bars from Twelve Data
- `notifier.py` — sends the verdict to Telegram
- `system_prompt.txt` — the analyst's instructions (auto-loaded by app.py)
- `pine/forex_alert.pine` — optional, only useful if you're on a TradingView plan with webhook alerts (Essential+); not required
- `requirements.txt`, `.env.example`

## Why no TradingView dependency
TradingView webhook alerts require an Essential-tier ($14.95/mo+) subscription
— they're not on the free plan. Since the bot already pulls its own OHLC bars
from Twelve Data for the AI analysis, it doesn't need TradingView's price feed
either — `scheduler.py` computes the same EMA(21) crossover itself, on a timer,
for free. TradingView is now optional (just for chart-watching), not required.

## 1. Create your accounts (one-time)

### Azure AI Foundry (model API)
Already set up — put your endpoint, deployment name, and API key in `.env` as
`AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT`, `AZURE_OPENAI_API_KEY`.
The bot calls it via the OpenAI SDK's Responses API (`client.responses.create`)
with the `web_search_preview` tool for the fundamental-analysis step.

### Twelve Data (market data)
1. Sign up at [twelvedata.com](https://twelvedata.com) (free tier: 800 requests/day)
2. Copy your API key from the dashboard into `.env` as `TWELVEDATA_API_KEY`

### Telegram bot
1. In Telegram, message **@BotFather** → `/newbot` → follow the prompts → it gives you a token
2. Put that token in `.env` as `TELEGRAM_BOT_TOKEN`
3. Message your new bot anything (so it has a chat with you), then visit
   `https://api.telegram.org/bot<your-token>/getUpdates` in a browser — find
   `"chat":{"id":...}` in the response and put that number in `.env` as
   `TELEGRAM_CHAT_ID`

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
This starts both the Flask server and the background scheduler. Test it:
```bash
curl http://localhost:5000/health
```

## 3. Deploy (so it keeps running without your computer on)
- **Render.com** (recommended) — free tier, connects to a GitHub repo, auto-deploys on push
- A small VPS (DigitalOcean, Linode, ~$5/mo) if you want more control

Push this folder to a GitHub repo, connect it on Render, set all the `.env`
variables in Render's dashboard (never commit your real `.env`).

Render's free tier sleeps after 15 minutes idle — keep it awake with a free
[UptimeRobot](https://uptimerobot.com) monitor pinging `/health` every 5 minutes.

## 4. (Optional) TradingView alerts
Only needed if you're on TradingView Essential or higher and want alerts
there too, in addition to the bot's own scheduler:
1. Open a chart for EUR/USD, apply `pine/forex_alert.pine` (paste into Pine Editor, "Add to Chart")
2. Click the alarm clock icon → condition: "Forex AI Analyst Trigger" → "Any alert() function call"
3. Under **Notifications**, check **Webhook URL**, enter:
   `https://your-deployed-url.com/webhook/tradingview`
4. Save. Repeat for GBP/USD and USD/JPY (same script, different chart).

## 5. What happens on a trigger
1. Every `POLL_INTERVAL_MINUTES` (default 15), the scheduler fetches the latest
   `TRIGGER_TIMEFRAME` (default 1h) bars for each pair in `WATCH_PAIRS` and
   checks for an `EMA_LENGTH`-period (default 21) crossover
2. On a new crossover, it checks it's inside your configured trading window
   (`.env`) — if not, it logs and skips
3. It fetches 4H/1H/15min/5min bars for that pair from Twelve Data
4. It calls the model (Azure AI Foundry) with the system prompt + those bars;
   the model reads the technicals from the bars and searches the web for
   fundamental context
5. It sends the structured verdict to your Telegram and logs it to `bot.log`

## 6. Tuning things later (no code changes needed)
- **Change the analyst's behavior/wording**: edit `system_prompt.txt`, redeploy
- **Change which pairs are watched**: edit `WATCH_PAIRS` in `.env`
- **Change the trigger sensitivity**: `EMA_LENGTH`, `TRIGGER_TIMEFRAME`, `POLL_INTERVAL_MINUTES` in `.env`
- **Change trading window/days**: edit `TRADING_DAYS`, `TRADING_WINDOW_START/END` in `.env` (all UTC)
- **Change pip target/stop**: edit `RISK_REWARD_PIPS` in `.env`
- **Swap the data provider**: replace the calls in `market_data.py`; `app.py`/`scheduler.py` only depend on the bar-list shape returned

## 7. Reliability notes
- Malformed webhook payloads return `400` and are logged — they don't crash the process
- Twelve Data or Azure OpenAI API failures are caught, logged, and return `500`/skip without crashing
- The scheduler dedupes by bar timestamp, so the same completed bar never triggers twice
- All activity is logged to `bot.log` (and stdout) for daily review
