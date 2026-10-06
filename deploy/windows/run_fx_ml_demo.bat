@echo off
REM FX ML demo bot for MetaTrader 5 (demo accounts only). Restarts itself if it stops.
cd /d %~dp0\..\..
:loop
python -m forex_ai_analyst.forex.mt5_bot
echo Bot stopped (exit code %errorlevel%). Restarting in 60 seconds... Press Ctrl+C to quit.
timeout /t 60 /nobreak >nul
goto loop
