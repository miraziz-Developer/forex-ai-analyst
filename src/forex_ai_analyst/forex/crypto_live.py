"""Crypto Donchian 4h on MT5 CFDs, forward test on the demo (docs/CRYPTO_CFD_STUDY.md).

The live BingX rule unchanged: H4, close above the previous 100-bar high buys with a 3 ATR stop, a close
below the previous 20-bar low exits. Risk FX_CRYPTO_RISK_PCT (0.3). Crypto coins fall together, so a 40%
one-day stress move applies. Journalled as `crypto_donchian_h4_v1`.
"""
from __future__ import annotations

from forex_ai_analyst.forex import mt5_guards as guards
from forex_ai_analyst.forex.trend_live import DonchianEngine

CRYPTO = DonchianEngine(
    version="crypto_donchian_h4_v1", label="crypto",
    params={"entry_n": 100, "exit_n": 20, "stop_atr": 3.0, "sides": "long"},
    candidates={
        "BTCUSD": ["BTCUSD", "BTCUSDT", "BITCOIN"], "ETHUSD": ["ETHUSD", "ETHUSDT", "ETHEREUM"],
        "SOLUSD": ["SOLUSD", "SOLUSDT"], "XRPUSD": ["XRPUSD", "XRPUSDT"], "BNBUSD": ["BNBUSD", "BNBUSDT"],
        "DOGEUSD": ["DOGEUSD", "DOGUSD", "DOGEUSDT"], "ADAUSD": ["ADAUSD", "ADAUSDT"],
        "LINKUSD": ["LINKUSD", "LNKUSD", "LINKUSDT"], "AVAXUSD": ["AVAXUSD", "AVAUSD", "AVAXUSDT"],
        "LTCUSD": ["LTCUSD", "LTCUSDT"],
    },
    timeframe="H4", risk_env="FX_CRYPTO_RISK_PCT", default_risk_pct=0.3, bar_count=300)
guards.STRESS_MOVE.update({market: 0.40 for market in CRYPTO.candidates})

cycle, resolve, CANDIDATES, VERSION = CRYPTO.cycle, CRYPTO.resolve, CRYPTO.candidates, CRYPTO.version
