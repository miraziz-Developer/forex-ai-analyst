"""crowding_exhaustion: leveraged crowding that aggressive flow can no longer extend.

v1 was registered before the full Phase 0 probe finished; the probe then showed
5-minute open-interest metrics start 2021-12-01 for every market except BTC
(2021-01-01). v1 is kept byte-for-byte (its content hash is tested) and is
superseded by v2, which differs only in the discovery start. No trial was run
on v1.

Binance USD-M archives provide perpetual and spot OHLCV (incl. taker buy
volume), funding, OI metrics and mark/index/premium klines through 2026-08.
Historical liquidations are not available and are excluded, not proxied.

Contamination note: 2025-07..2026-09 was out-of-sample data for the earlier
Donchian lab (a different hypothesis); it has not been used for this one.
"""
from itertools import product

MANIFEST_V1 = {
    "hypothesis_id": "crowding_exhaustion_v1",
    "economic_thesis": (
        "When leveraged traders crowd one side (extreme funding percentile and fast open-interest growth) "
        "while aggressive flow in that direction moves price less per unit of flow, the crowd is exhausted. "
        "A structural break against the crowd can start a forced unwind; falling open interest confirms it. "
        "SHORT studies crowded longs; LONG studies crowded shorts. The signal is the failed price response "
        "to leverage and flow, not the funding level alone."
    ),
    "markets": ["BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "BNB-USDT",
                "DOGE-USDT", "ADA-USDT", "LINK-USDT", "AVAX-USDT", "LTC-USDT"],
    "market_selection_note": "All still listed in 2026: survivorship bias is acknowledged, not corrected.",
    "directions": ["SHORT", "LONG"],
    "direction_policy": "Each direction is a separate family with its own trials, report and verdict.",
    "feature_version": "v1",
    "signal_timeframe": "15m",
    "discovery_period": ["2021-01-01", "2024-01-01"],
    "validation_period": ["2024-01-01", "2025-07-01"],
    "holdout_period": ["2025-07-01", "2026-09-01"],
    "forward_period": "after implementation, VST shadow only",
    "parameter_grid": {
        "funding_percentile": [90, 95],          # LONG mirrors as 10 / 5
        "oi_change_lookback": ["1h", "4h"],
        "price_lookback_bars": [20, 55],
        "confirmation": ["prior_bar_low_break", "local_structure_break"],
        "exit": ["fixed_2r", "oi_unwind_or_max_hold"],
    },
    "max_trials_per_direction": 32,
    "percentile_window": "rolling 30 days of prior observations only",
    "data": {
        "perp_ohlcv": "HISTORICAL_AND_LIVE",
        "spot_ohlcv": "HISTORICAL_AND_LIVE",
        "funding": "HISTORICAL_AND_LIVE",
        "open_interest_5m": "HISTORICAL_AND_LIVE",
        "taker_buy_volume": "HISTORICAL_AND_LIVE",
        "premium_index_basis": "HISTORICAL_AND_LIVE",
        "order_book_depth": "LIVE_ONLY",
        "liquidations": "UNAVAILABLE",
    },
    "availability_rules": {
        "klines": "available at bar close (open_time + interval)",
        "open_interest_5m": "available one 5-minute interval after create_time (conservative)",
        "funding": "available at settlement time",
    },
    "cost_model": {
        "base": {"taker_fee_pct_per_side": 0.05, "slippage_pct_per_side": 0.02, "funding": "historical"},
        "stress": {"taker_fee_pct_per_side": 0.10, "slippage_pct_per_side": 0.06, "funding": "historical"},
        "severe": {"taker_fee_pct_per_side": 0.10, "slippage_pct_per_side": 0.06, "funding": "adverse",
                   "entry_delay_bars": 1, "gap_through_stop": True},
    },
    "event_study_gate": {
        "min_discovery_events": 100,
        "min_holdout_events": 30,
        "min_bootstrap_positive_probability": 0.90,
        "effect_must_exceed_base_cost": True,
        "min_pairs": 3,
        "leave_one_pair_out_must_hold": True,
    },
    "promotion_gate": {
        "min_oos_sharpe": 1.0,
        "min_deflated_sharpe_probability": 0.90,
        "min_positive_window_share": 0.65,
        "min_profit_factor": 1.20,
        "max_drawdown_pct": -20.0,
        "min_stress_sharpe": 0.30,
        "min_holdout_trades": 30,
        "max_single_pair_profit_share": 0.50,
    },
    "verdicts": ["REJECTED", "INSUFFICIENT_EVIDENCE", "ELIGIBLE_FOR_SHADOW"],
    "ai_role": "May explain results and propose new hypotheses; any AI-suggested change is a new version.",
}


MANIFEST_V2 = {
    **MANIFEST_V1,
    "hypothesis_id": "crowding_exhaustion_v2",
    "supersedes": "crowding_exhaustion_v1",
    "discovery_period": ["2021-12-01", "2024-01-01"],
    "data_coverage": {
        "open_interest_5m_first_day": {"BTC-USDT": "2021-01-01", "others": "2021-12-01"},
        "probe": "research_output/edge_lab/manifests/data_sources_v1.json + per-day HEAD binary search",
    },
}

MANIFEST = MANIFEST_V2  # current version


def parameter_configs() -> list[dict]:
    grid = MANIFEST["parameter_grid"]
    names = list(grid)
    return [dict(zip(names, values)) for values in product(*(grid[n] for n in names))]


def event_configs() -> list[dict]:
    """Event-study configurations: the grid without the exit, which only matters once trading."""
    seen, out = set(), []
    for config in parameter_configs():
        key = tuple((k, v) for k, v in config.items() if k != "exit")
        if key not in seen:
            seen.add(key)
            out.append(dict(key))
    return out


# Feature-v1 event thresholds. Fixed before the first event study; changing any of
# them is a new hypothesis version.
OI_PCT_MIN = 90
FLOW_PCT_MIN = 80
IMPACT_PCT_MAX = 50
BASIS_PCT_EXTREME = 90
TRIGGER_WITHIN_BARS = 8
STRUCTURE_BARS = 8
COOLDOWN_BARS = 96


def _setup(f: dict, frame, i: int, config: dict, direction: str) -> bool:
    n = config["price_lookback_bars"]
    oi_pct = f[f"oi_chg_{config['oi_change_lookback']}_pct"][i]
    funding = f["funding_pct"][i]
    basis = f["premium_pct"][i]
    if oi_pct is None or funding is None or oi_pct < OI_PCT_MIN:
        return False
    if direction == "SHORT":
        prior, spot_prior, spot_now = f[f"prior_high_{n}"][i], f[f"spot_prior_high_{n}"][i], frame.spot_high[i]
        flow, impact = f["buy_ratio4_pct"][i], f["up_impact_pct"][i]
        if prior is None or flow is None or impact is None or funding < config["funding_percentile"]:
            return False
        if not frame.high[i] > prior:
            return False
        spot_confirms = spot_now is not None and spot_prior is not None and spot_now > spot_prior
        diverges = (basis is not None and basis >= BASIS_PCT_EXTREME) or not spot_confirms
    else:
        prior, spot_prior, spot_now = f[f"prior_low_{n}"][i], f[f"spot_prior_low_{n}"][i], frame.spot_low[i]
        flow, impact = f["sell_ratio4_pct"][i], f["down_impact_pct"][i]
        if prior is None or flow is None or impact is None or funding > 100 - config["funding_percentile"]:
            return False
        if not frame.low[i] < prior:
            return False
        spot_confirms = spot_now is not None and spot_prior is not None and spot_now < spot_prior
        diverges = (basis is not None and basis <= 100 - BASIS_PCT_EXTREME) or not spot_confirms
    return flow >= FLOW_PCT_MIN and impact <= IMPACT_PCT_MAX and diverges


def _trigger(frame, i: int, setup_index: int, config: dict, direction: str) -> bool:
    if config["confirmation"] == "prior_bar_low_break":
        return frame.close[i] < frame.low[i - 1] if direction == "SHORT" else frame.close[i] > frame.high[i - 1]
    start = max(0, setup_index - STRUCTURE_BARS)
    if direction == "SHORT":
        return frame.close[i] < min(frame.low[start:i])
    return frame.close[i] > max(frame.high[start:i])


def detect_events(frame, config: dict, direction: str, hypothesis_id: str) -> list:
    """Crowding setup followed within TRIGGER_WITHIN_BARS by a structural break against the crowd."""
    from forex_ai_analyst.research.edge_lab.features import FEATURE_VERSION
    from forex_ai_analyst.research.edge_lab.models import MarketEvent

    f, events, setup_index, cooldown_until = frame.features, [], None, -1
    for i in range(1, len(frame)):
        if i < cooldown_until:
            continue
        if _setup(f, frame, i, config, direction):
            setup_index = i
            continue
        if setup_index is None:
            continue
        if i - setup_index > TRIGGER_WITHIN_BARS:
            setup_index = None
            continue
        if _trigger(frame, i, setup_index, config, direction):
            snapshot = {name: f[name][i] for name in ("funding_pct", "oi_chg_1h_pct", "oi_chg_4h_pct", "premium_pct",
                                                      "buy_ratio4_pct", "sell_ratio4_pct", "up_impact_pct",
                                                      "down_impact_pct")}
            events.append(MarketEvent(
                event_id=f"{frame.pair}:{direction}:{frame.decision_time[i]}", hypothesis_id=hypothesis_id,
                pair=frame.pair, direction=direction, event_time_ms=frame.open_time[setup_index],
                decision_time_ms=frame.decision_time[i], reference_price=frame.close[i],
                feature_version=FEATURE_VERSION, features={"bar_index": i, "setup_index": setup_index, **snapshot},
                data_quality={}))
            setup_index, cooldown_until = None, i + COOLDOWN_BARS
    return events
