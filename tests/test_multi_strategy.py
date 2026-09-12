import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import requests

os.environ.setdefault("TURSO_DATABASE_URL", "libsql://test.invalid")
os.environ.setdefault("TURSO_AUTH_TOKEN", "test")

from market_regime import RegimeSnapshot, classify_market_regime
from risk_manager import RiskConfig, assess_risk
from scalping_core import CandidateSignal, CandidateStatus, Direction, MarketRegime
from scalping_data import (MarketDataProvider, bingx_swap_symbol, binance_futures_symbol, closed_bars,
                            fetch_bingx_swap_bars, fetch_binance_futures_bars, provider_from_environment)
from scalping_indicators import candle_confirmation, support_resistance_zones
from multi_strategy_backtest import BacktestCosts, simulate as multi_simulate
from production_backtest import apply_account_limits, walk_forward_periods
from quality_policy import QualityPolicy, quality_rejection_reason
from strategies import support_resistance_rejection
from multi_strategy_scheduler import resolve_open_paper_signals
from strategy_coordinator import resolve_candidates
from strategies.trend_pullback import evaluate


NOW = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)


def bar(index, close, *, interval=300000, volume=100, spread=1.0):
    return {"datetime": int(NOW.timestamp() * 1000) - (300 - index) * interval,
            "open": close - .1, "high": close + spread, "low": close - spread,
            "close": close, "volume": volume}


def candidate(score=80, direction=Direction.BUY, pair="BTC-USDT"):
    return CandidateSignal("trend_pullback", pair, direction, MarketRegime.TRENDING_UP,
                           100, 98 if direction is Direction.BUY else 102,
                           103 if direction is Direction.BUY else 97,
                           NOW + timedelta(hours=1), "5m", "15m", 123, score,
                           ("closed candle",), "structure invalidated")


class MultiStrategyTests(unittest.TestCase):
    def test_walk_forward_periods_are_equal_contiguous_and_validate_input(self):
        start = datetime(2026, 6, 1, tzinfo=timezone.utc)
        periods = walk_forward_periods(start, 90, 3)
        self.assertEqual(periods, [
            (start, datetime(2026, 7, 1, tzinfo=timezone.utc)),
            (datetime(2026, 7, 1, tzinfo=timezone.utc), datetime(2026, 7, 31, tzinfo=timezone.utc)),
            (datetime(2026, 7, 31, tzinfo=timezone.utc), datetime(2026, 8, 30, tzinfo=timezone.utc)),
        ])
        with self.assertRaises(ValueError):
            walk_forward_periods(start, 90, 4)

    def test_binance_futures_provider_uses_public_ohlcv_endpoint(self):
        response = Mock()
        response.json.return_value = [[1000, "10", "11", "9", "10.5", "42"]]
        with patch("scalping_data.requests.get", return_value=response) as request:
            result = fetch_binance_futures_bars("btc-usdt", "5m", 2)
        request.assert_called_once()
        self.assertEqual(request.call_args.kwargs["params"], {"symbol": "BTCUSDT", "interval": "5m", "limit": 2})
        self.assertEqual(result, [{"datetime": 1000, "open": "10", "high": "11", "low": "9", "close": "10.5", "volume": "42"}])
        self.assertEqual(binance_futures_symbol("ETH-USDT"), "ETHUSDT")

    def test_binance_futures_provider_uses_next_host_after_failure(self):
        failed, success = Mock(), Mock()
        failed.raise_for_status.side_effect = requests.HTTPError("418 blocked")
        success.json.return_value = [[1000, "10", "11", "9", "10.5", "42"]]
        with patch("scalping_data.requests.get", side_effect=[failed, success]) as request:
            result = fetch_binance_futures_bars("BTC-USDT", "5m", 2)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(result[0]["close"], "10.5")

    def test_bingx_provider_uses_public_swap_ohlcv_endpoint(self):
        response = Mock()
        response.json.return_value = {"code": 0, "data": [{"time": 1000, "open": "10", "high": "11",
                                                               "low": "9", "close": "10.5", "volume": "42"}]}
        with patch("scalping_data.requests.get", return_value=response) as request:
            result = fetch_bingx_swap_bars("btc-usdt", "5m", 2)
        request.assert_called_once()
        self.assertEqual(request.call_args.kwargs["params"], {"symbol": "BTC-USDT", "interval": "5m", "limit": 2})
        self.assertEqual(result, [{"datetime": 1000, "open": "10", "high": "11", "low": "9", "close": "10.5", "volume": "42"}])
        self.assertEqual(bingx_swap_symbol("ETH-USDT"), "ETH-USDT")

    def test_provider_defaults_to_bingx_when_render_value_is_blank(self):
        with patch.dict(os.environ, {"MULTI_STRATEGY_PROVIDER": ""}):
            provider = provider_from_environment()
        self.assertIs(provider.fetcher, fetch_bingx_swap_bars)

    def test_provider_rejects_an_explicitly_unsupported_value(self):
        with patch.dict(os.environ, {"MULTI_STRATEGY_PROVIDER": "kraken"}):
            with self.assertRaisesRegex(ValueError, "must be bingx or binance_futures"):
                provider_from_environment()

    def test_closed_bars_excludes_open_duplicate_and_invalid_ohlc(self):
        now_ms = int(NOW.timestamp() * 1000)
        raw = [
            {"datetime": now_ms - 600000, "open": 10, "high": 11, "low": 9, "close": 10, "volume": 1},
            {"datetime": now_ms - 600000, "open": 10, "high": 11, "low": 9, "close": 10, "volume": 1},
            {"datetime": now_ms - 299999, "open": 10, "high": 11, "low": 9, "close": 10, "volume": 1},
            {"datetime": now_ms - 900000, "open": 10, "high": 9, "low": 9, "close": 10, "volume": 1},
        ]
        result = closed_bars(raw, "5m", NOW)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["datetime"], now_ms - 600000)

    def test_provider_reuses_closed_candle_cache_until_next_boundary(self):
        fetcher = Mock(return_value=[bar(index, 100 + index) for index in range(20)])
        provider = MarketDataProvider(fetcher)
        provider.fetch_closed_bars("BTC-USDT", "5m", 10, NOW)
        provider.fetch_closed_bars("BTC-USDT", "5m", 10, NOW + timedelta(minutes=4))
        self.assertEqual(fetcher.call_count, 1)
        provider.fetch_closed_bars("BTC-USDT", "5m", 10, NOW + timedelta(minutes=5))
        self.assertEqual(fetcher.call_count, 2)

    def test_candle_confirmation_and_confirmed_pivot_zones(self):
        candles = [bar(index, 100, spread=1) for index in range(60)]
        candles[55].update(open=100, high=101, low=90, close=99)
        candles[-2].update(open=99, high=100, low=95, close=96)
        candles[-1].update(open=95, high=102, low=94, close=101)
        self.assertEqual(candle_confirmation(candles[-2:]), "BULLISH_ENGULFING")
        supports, _ = support_resistance_zones(candles)
        self.assertTrue(any(low <= 90 <= high for low, high in supports))

    def test_sr_strategy_rejects_long_rejection_in_downtrend(self):
        bars = [bar(index, 100, spread=1) for index in range(60)]
        bars[-3].update(low=95, high=101, open=100, close=99)
        bars[-2].update(low=94, high=100, open=99, close=96)
        bars[-1].update(low=94, high=102, open=95, close=101, volume=250)
        regime = RegimeSnapshot(MarketRegime.TRENDING_DOWN, {})
        self.assertIsNone(support_resistance_rejection.evaluate("BTC-USDT", bars, bars, regime, NOW))

    def test_regime_fails_closed_on_insufficient_history(self):
        self.assertEqual(classify_market_regime([bar(index, 100 + index) for index in range(100)]).regime,
                         MarketRegime.UNCERTAIN)

    def test_invalid_levels_and_duplicate_fingerprint_are_rejected(self):
        bad = CandidateSignal("x", "BTC-USDT", Direction.BUY, MarketRegime.TRENDING_UP, 100, 101, 103,
                              NOW + timedelta(hours=1), "5m", "15m", 1, 80, (), "bad")
        duplicate = candidate()
        decisions = resolve_candidates([bad, duplicate], {duplicate.fingerprint}, NOW)
        self.assertEqual(decisions[0].status, CandidateStatus.REJECTED)
        self.assertEqual(decisions[1].status, CandidateStatus.BLOCKED_BY_CONFLICT)

    def test_highest_score_wins_same_pair_conflict(self):
        lower, higher = candidate(75), candidate(85)
        decisions = resolve_candidates([lower, higher], set(), NOW)
        self.assertEqual([item.status for item in decisions].count(CandidateStatus.ACCEPTED_PAPER), 1)
        accepted = next(item for item in decisions if item.accepted)
        self.assertEqual(accepted.candidate.score, 85)

    def test_risk_blocks_limits_and_sizes_from_stop(self):
        signal = candidate()
        config = RiskConfig(risk_usdt_per_trade=.75, max_daily_loss_usdt=2, max_daily_trades=3,
                            max_open_positions=1)
        self.assertFalse(assess_risk(signal, open_positions=1, daily_trades=0, daily_realized_pnl=0, config=config).accepted)
        self.assertFalse(assess_risk(signal, open_positions=0, daily_trades=3, daily_realized_pnl=0, config=config).accepted)
        self.assertFalse(assess_risk(signal, open_positions=0, daily_trades=0, daily_realized_pnl=-2, config=config).accepted)
        decision = assess_risk(signal, open_positions=0, daily_trades=0, daily_realized_pnl=0, config=config)
        self.assertTrue(decision.accepted)
        self.assertAlmostEqual(decision.quantity, .375)

    def test_zero_risk_limits_allow_multiple_open_and_daily_signals(self):
        config = RiskConfig(max_daily_trades=0, max_open_positions=0)
        self.assertTrue(assess_risk(candidate(), open_positions=99, daily_trades=99,
                                    daily_realized_pnl=0, config=config).accepted)

    def test_risk_rejects_excessive_notional_and_fee_relative_to_stop_risk(self):
        signal = candidate()
        capped = RiskConfig(risk_usdt_per_trade=.5, max_position_notional_usdt=20)
        self.assertFalse(assess_risk(signal, open_positions=0, daily_trades=0,
                                     daily_realized_pnl=0, config=capped).accepted)
        fee_capped = RiskConfig(risk_usdt_per_trade=.5, max_fee_to_risk_ratio=.04,
                                estimated_fee_pct_round_trip=.10)
        self.assertFalse(assess_risk(signal, open_positions=0, daily_trades=0,
                                     daily_realized_pnl=0, config=fee_capped).accepted)

    def test_quality_policy_blocks_pair_direction_tight_stop_and_conflicting_hourly_trend(self):
        hourly_up = [bar(index, 100 + index, interval=3600000) for index in range(200)]
        policy = QualityPolicy()
        self.assertEqual(quality_rejection_reason(candidate(pair="XRP-USDT"), hourly_up, policy),
                         "pair blocked by quality policy")
        self.assertEqual(quality_rejection_reason(candidate(direction=Direction.SELL), hourly_up, policy),
                         "sell direction blocked by quality policy")
        tight = candidate()
        object.__setattr__(tight, "features", {"atr_5m": 3.0})
        self.assertEqual(quality_rejection_reason(tight, hourly_up, QualityPolicy(require_1h_alignment=False)),
                         "stop distance below ATR quality minimum")
        aligned = candidate()
        object.__setattr__(aligned, "features", {"atr_5m": 1.0})
        self.assertIsNone(quality_rejection_reason(aligned, hourly_up, policy))

    def test_account_simulator_enforces_one_open_position_and_daily_loss_stop(self):
        base = int(NOW.timestamp() * 1000)
        def trade(minutes, exit_minutes, pnl):
            return {"pair": "BTC-USDT", "strategy": "test", "direction": "BUY", "outcome": "LOSS",
                    "time": base + minutes * 60000, "entry_time": base + minutes * 60000,
                    "exit_time": base + exit_minutes * 60000, "net_pnl_usdt": pnl,
                    "gross_pnl_usdt": pnl, "fees_usdt": 0, "held_bars": 1}
        config = RiskConfig(max_open_positions=1, max_daily_trades=4, max_daily_loss_usdt=.5)
        accepted, rejected = apply_account_limits([trade(0, 5, -.5), trade(1, 2, 1), trade(6, 7, 1)], config, 100)
        self.assertEqual(len(accepted), 1)
        self.assertEqual(rejected["maximum open positions reached"], 1)
        self.assertEqual(rejected["daily loss limit reached"], 1)

    def test_trend_pullback_generates_valid_long(self):
        bars15 = [bar(index, 100 + index * .5, interval=900000, spread=.4) for index in range(260)]
        # Explicit higher-high/higher-low pivots and a final bullish 5m reclaim.
        bars5 = [bar(index, 100 + index * .1, spread=.2) for index in range(80)]
        bars5[-2].update(open=107.6, high=107.9, low=107.2, close=107.5)
        bars5[-1].update(open=107.5, high=108.2, low=107.3, close=108.0, volume=150)
        regime = RegimeSnapshot(MarketRegime.TRENDING_UP, {"adx": 25.0})
        result = evaluate("btc-usdt", bars15, bars5, regime, NOW)
        self.assertIsNotNone(result)
        self.assertEqual(result.direction, Direction.BUY)
        self.assertGreaterEqual(result.score, 80)
        self.assertIsNone(result.validation_error(NOW))

    @patch("multi_strategy_scheduler.scalping_storage.resolve_paper_signal")
    @patch("multi_strategy_scheduler.scalping_storage.open_paper_signals")
    def test_resolver_records_loss_when_one_candle_hits_stop_and_target(self, open_signals, resolve):
        open_signals.return_value = [{"fingerprint": "one", "pair": "BTC-USDT", "direction": "BUY",
                                      "entry_price": 100, "stop_price": 98, "target_price": 103,
                                      "candle_time": 1000,
                                      "expiry_time": (NOW + timedelta(hours=1)).isoformat()}]
        provider = Mock()
        provider.fetch_closed_bars.return_value = [{"datetime": 2000, "low": 97, "high": 104, "close": 101}]
        resolve_open_paper_signals(provider)
        resolve.assert_called_once_with("one", CandidateStatus.LOSS, 98.0)

    def test_multi_backtest_enters_next_open_and_resolves_ambiguous_bar_as_loss(self):
        bars = [bar(index, 100, interval=300000, spread=.2) for index in range(270)]
        bars[261].update(open=101, high=104, low=98, close=101)

        def evaluator(closed, now):
            return [CandidateSignal("test", "BTC-USDT", Direction.BUY, MarketRegime.TRENDING_UP,
                                    100, 98, 103, now + timedelta(hours=1), "5m", "15m",
                                    int(closed[-1]["datetime"]), 80, ("closed",), "invalid")]

        trades = multi_simulate(bars, evaluator, warmup_bars=260, expiry_bars=2,
                                costs=BacktestCosts(fee_pct_round_trip=0, slippage_pct_round_trip=0))
        self.assertEqual(trades[0]["entry"], 101.0)
        self.assertEqual(trades[0]["outcome"], "LOSS")


if __name__ == "__main__":
    unittest.main()