# Forex AI Analyst Bot

A personal bot: a TradingView alert on a forex pair fires a webhook, the bot
pulls real OHLC price history, Claude does a technical + fundamental read,
and the verdict lands in your Telegram. **Analysis and alerting only — it
never places, modifies, or cancels any order.**

## What's included
- `app.py` — the webhook server (Flask)
- `market_data.py` — pulls OHLC bars from Twelve Data
- `notifier.py` — sends the verdict to Telegram
- `system_prompt.txt` — the analyst's instructions (auto-loaded by app.py)
- `pine/forex_alert.pine` — minimal TradingView alert trigger
- `requirements.txt`, `.env.example`

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
# fill in the four keys/IDs above
```
Run it:
```bash
export $(cat .env | xargs)
python app.py
```
Test it:
```bash
curl http://localhost:5000/health
```

## 3. Deploy so TradingView can reach it
TradingView needs a **public URL** — localhost won't work.
- **Render.com** (recommended) — free/hobby tier, connects to a GitHub repo, auto-deploys on push
- A small VPS (DigitalOcean, Linode, ~$5/mo) if you want more control

Push this folder to a GitHub repo, connect it on Render, set the four `.env`
variables in Render's dashboard (never commit your real `.env`).

## 4. Set up TradingView alerts (one per pair)
1. Open a chart for EUR/USD, apply `pine/forex_alert.pine` (paste into Pine Editor, "Add to Chart")
2. Click the alarm clock icon → condition: "Forex AI Analyst Trigger" → "Any alert() function call"
3. Under **Notifications**, check **Webhook URL**, enter:
   `https://your-deployed-url.com/webhook/tradingview`
4. Save. Repeat for GBP/USD and USD/JPY (same script, different chart).

## 5. What happens when an alert fires
1. Bot checks it's inside your configured trading window (`.env`) — if not, it logs and ignores
2. Bot fetches 4H/1H/15min/5min bars for that pair from Twelve Data
3. Bot calls the model (Azure AI Foundry) with the system prompt + those bars;
   it reads the technicals from the bars and searches the web for fundamental context
4. Bot sends the structured verdict to your Telegram and logs it to `bot.log`

## 6. Tuning things later (no code changes needed)
- **Change the analyst's behavior/wording**: edit `system_prompt.txt`, redeploy
- **Change trading window/days**: edit `TRADING_DAYS`, `TRADING_WINDOW_START/END` in `.env` (all UTC — no daylight-saving handling needed since it's not tied to a US market-hours convention)
- **Change pip target/stop**: edit `RISK_REWARD_PIPS` in `.env`
- **Add a pair**: create a new TradingView alert pointing at the same webhook — `market_data.py` and the prompt are already ticker-driven
- **Swap the data provider**: replace the calls in `market_data.py`; `app.py` only depends on `fetch_multi_timeframe()`'s return shape

## 7. Reliability notes
- Malformed webhook payloads return `400` and are logged — they don't crash the process
- Twelve Data or Azure OpenAI API failures are caught, logged, and return `500` without crashing
- All activity is logged to `bot.log` (and stdout) for daily review
