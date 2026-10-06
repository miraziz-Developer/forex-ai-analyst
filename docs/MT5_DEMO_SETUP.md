# FX ML demo bot on a Windows VM (MetaTrader 5)

Runs the forward test of the FX logistic model (`docs/ML_STUDY.md`) on a broker
**demo** account. The bot refuses any account that is not a demo account.

## One-time setup

1. Install MetaTrader 5 from your broker (FBS), log in to the **demo** account and
   keep the terminal open. In *Tools > Options > Expert Advisors* allow algorithmic trading.
2. Install Python 3.11+ (tick "Add python.exe to PATH").
3. In PowerShell:
   ```powershell
   git clone https://github.com/miraziz-Developer/forex-ai-analyst.git
   cd forex-ai-analyst
   pip install . MetaTrader5
   ```
4. Optional `.env` in that folder (Telegram messages; never commit it):
   ```
   TELEGRAM_BOT_TOKEN=...
   TELEGRAM_CHAT_ID=...
   FX_BOT_RISK_PCT=0.5
   ```
   If the broker uses suffixed names (e.g. `EURUSD.` or `GOLD`), add
   `FX_SYMBOL_MAP=EURUSD=EURUSD.,XAUUSD=GOLD`; common suffixes are found automatically.

## Run

```powershell
deploy\windows\run_fx_ml_demo.bat
```
The bot runs every exported model: **v1** (`fx_logistic`) and **v2c**
(`fx_logistic_cot_c01`, v1 + CFTC positioning, stronger regularisation). Each
decides on its own and its trades are labelled `[v1]` / `[v2c]` in Telegram and
journalled under its model version; both may hold the same market at once. If
the CFTC site is unreachable, v2c waits while v1 trades.

The bot checks every 15 minutes. After each Monday close it may open positions
(P(up) >= 55% BUY, <= 45% SELL) on EURUSD, GBPUSD, AUDUSD, NZDUSD, USDCAD,
USDCHF, USDJPY, EURJPY, GBPJPY and XAUUSD, each with a 3 ATR emergency stop and
0.5% of equity at risk, and closes them after the fifth trading day's close.

**Month-end fix forward test.** The same bot also runs the London 4pm fix
rule (docs/FIX_STUDY.md) on EURUSD, GBPUSD, USDJPY and AUDUSD: on the last
weekday of each month it samples prices at 15:00 and 15:55 London, enters
against the move at 16:03 and exits at 12:00 London the next weekday
(`[fix]` in Telegram, 0.25% risk per trade via `FX_FIX_RISK_PCT`). The bot
must be running from before 15:00 London on that day.

**Guards (optional `.env` overrides):**
- `FX_BOT_MAX_TOTAL_RISK_PCT=5` — all open positions together risk at most 5%
  of equity; when the budget is full the strongest signals have been placed
  first and the rest are skipped.
- `FX_BOT_MAX_DD_PCT=10` — 10% below the equity peak, no new trades until it
  recovers (open trades are still closed on schedule); Telegram says so.
- `FX_BOT_MAX_SPREAD_FRAC=0.10` — no entry while the spread is wider than 10% of
  the stop distance (e.g. at the daily rollover); retried until the end of the
  next day.
- `FX_BOT_MAX_CCY_RISK_PCT=2.5` — at most 2.5% of equity at risk in one
  direction of one currency (long EURUSD + GBPUSD + AUDUSD is three bets on a
  weaker dollar, so the third is skipped).
- `FX_BOT_NEWS_MINUTES=30` — no entry within 30 minutes of a high-impact
  release for either currency (live ForexFactory calendar; if the calendar is
  unreachable, trading continues); retried later.
- `FX_BOT_MAX_STRESS_TRADE_PCT=4` / `FX_BOT_MAX_STRESS_PCT=15` — a stop does
  not help when the price jumps through it, so each position is sized so that a
  repeat of its market's worst documented day (USDCHF 30% for the 2015 SNB
  shock, GBPJPY 16% for Brexit, ..., EURUSD 5%) costs at most 4% of equity
  (the volume is cut), and all open positions together at most 15%.
- A model with 30+ closed trades and profit factor below 0.7 stops opening
  trades (`FX_BOT_NO_AUTO_DISABLE=1` overrides).
- Every Monday a weekly report: balance, drawdown, open risk, and per model
  trades, win rate, profit factor, result and average slippage, against the
  forward-test bar (26 weeks, 60 trades, PF 1.2).
Signals, closes and errors go to Telegram; the journal is `fx_ml_demo.sqlite`.

## Export broker history for research

```powershell
python -m forex_ai_analyst.forex.mt5_export --years 10 --timeframes M15 H1 D1
```
Writes `mt5_data/*.csv` (with the broker's spread). Send the folder for the
next study; it contains prices only, no account data.

## Rules
- Demo only until the forward-test criteria in `docs/ML_STUDY.md` are met.
- Do not change thresholds or close trades by hand; that invalidates the test.
- Each January: `pip install .[research]` and `python -m forex_ai_analyst.forex.ml_model <year>`,
  or pull the new model file from the repository.
