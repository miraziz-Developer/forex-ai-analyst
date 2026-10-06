"""Gold and silver on H4 with the crypto Donchian rule, forward test on the demo (docs/GOLD_H4_STUDY.md).

Close above the previous 100-bar high buys with a 3 ATR stop; a close below the previous 20-bar low exits.
Risk FX_GOLD_RISK_PCT (0.75; gold and silver move together). Journalled as `metals_donchian_h4_v1`.
"""
from __future__ import annotations

from forex_ai_analyst.forex.trend_live import DonchianEngine

METALS = DonchianEngine(
    version="metals_donchian_h4_v1", label="gold",
    params={"entry_n": 100, "exit_n": 20, "stop_atr": 3.0, "sides": "long"},
    candidates={"XAUUSD": ["XAUUSD", "GOLD"], "XAGUSD": ["XAGUSD", "SILVER"]},
    timeframe="H4", risk_env="FX_GOLD_RISK_PCT", default_risk_pct=0.75, bar_count=300)

cycle, resolve, CANDIDATES, VERSION = METALS.cycle, METALS.resolve, METALS.candidates, METALS.version
