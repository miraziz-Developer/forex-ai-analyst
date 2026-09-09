import unittest
import tempfile
import json
import os
from unittest.mock import patch

from strategy_backtest import (
    FOUR_HOURS_MS,
    _profit_factor_score,
    _resolve_trend_exit,
    _meets_edge_requirements,
    _resolve,
    _validate_research_thresholds,
    aggregate_daily,
    load_or_fetch_history,
    metrics,
    parameter_grid,
    persist_promoted_policy,
    run_three_month_research,
    simulate,
)
from strategy_engine import (
    StrategyConfig,
    _indicator_confirmations,
    atr_expansion_ratio,
    bollinger_bands,
    candidate_levels,
    ema,
    load_promoted_policy,
    macd_histogram,
    rsi,
    supertrend_direction,
    support_resistance_levels,
)


class StrategyEngineTests(unittest.TestCase):
    def test_research_grid_contains_ten_predeclared_families(self):
        configs = list(parameter_grid())
        self.assertEqual(len(configs), 20)
        self.assertEqual(len({item.name for item in configs}), 10)
        self.assertTrue(all(sum(other.name == item.name for other in configs) == 2
                            for item in configs))

    def test_support_resistance_uses_confirmed_historical_pivots(self):
        chronological = [
            {"high": 10, "low": 8}, {"high": 11, "low": 7},
            {"high": 15, "low": 9}, {"high": 12, "low": 6},
            {"high": 11, "low": 5}, {"high": 13, "low": 7},
            {"high": 12, "low": 8}, {"high": 20, "low": 1},
        ]
        supports, resistances = support_resistance_levels(
            list(reversed(chronological)), lookback=7, pivot_span=2)
        self.assertEqual(supports, [5.0])
        self.assertEqual(resistances, [15.0])

    def test_ema_uses_latest_values(self):
        self.assertAlmostEqual(ema([1, 2, 3, 4], 3), 3.0)

    def test_rsi_extremes(self):
        self.assertEqual(rsi(list(range(20))), 100.0)

    def test_macd_and_bollinger_on_rising_prices(self):
        prices = [float(value ** 2) for value in range(1, 61)]
        self.assertGreater(macd_histogram(prices), 0)
        upper, middle, lower = bollinger_bands(prices)
        self.assertGreater(upper, middle)
        self.assertGreater(middle, lower)

    def test_atr_expansion_detects_recent_volatility(self):
        bars = []
        price = 100.0
        for index in range(40):
            spread = 5.0 if index < 7 else 1.0
            bars.append({"open": price, "high": price + spread, "low": price - spread,
                         "close": price, "volume": 1})
        self.assertGreater(atr_expansion_ratio(bars), 1)

    def test_supertrend_detects_clean_uptrend(self):
        chronological = []
        for value in range(1, 50):
            chronological.append({"open": value, "high": value + 1, "low": value - 1,
                                  "close": value + 0.5, "volume": 1})
        self.assertEqual(supertrend_direction(list(reversed(chronological))), "BUY")

    def test_multi_indicator_confirmations_follow_trade_direction(self):
        config = StrategyConfig(volume_ratio_min=1.2, atr_expansion_min=1.0)
        confirmations = _indicator_confirmations(
            "BUY", 105.0, 104.0, 58.0, 1.3, 0.4, 0.2, "BUY", 1.1, 102.0,
            config,
        )
        self.assertEqual(len(confirmations), 6)
        opposite = _indicator_confirmations(
            "SELL", 105.0, 104.0, 58.0, 1.3, 0.4, 0.2, "BUY", 1.1, 102.0,
            config,
        )
        self.assertEqual(opposite, ["volume participation", "ATR expansion"])

    def test_levels_respect_direction_and_rr(self):
        config = StrategyConfig(atr_stop=2.0, reward_risk=2.5)
        target, stop = candidate_levels(100, {"direction": "BUY", "atr": 1.0}, config)
        self.assertEqual((target, stop), (105.0, 98.0))

    def test_levels_put_stop_beyond_support_and_preserve_rr(self):
        config = StrategyConfig(atr_stop=1.0, reward_risk=2.0)
        target, stop = candidate_levels(
            100, {"direction": "BUY", "atr": 2.0, "structure_level": 96.0}, config)
        self.assertEqual(stop, 95.6)
        self.assertAlmostEqual(target, 108.8)

    def test_daily_aggregation_is_most_recent_first(self):
        bars = [
            {"datetime": 28 * 3600 * 1000, "open": 3, "high": 5, "low": 2, "close": 4, "volume": 2},
            {"datetime": 4 * 3600 * 1000, "open": 1, "high": 3, "low": 0, "close": 2, "volume": 1},
        ]
        daily = aggregate_daily(bars)
        self.assertGreater(daily[0]["datetime"], daily[1]["datetime"])

    def test_metrics_include_expiry_and_costs(self):
        result = metrics([
            {"outcome": "WIN", "net_r": 1.9},
            {"outcome": "LOSS", "net_r": -1.1},
            {"outcome": "EXPIRED", "net_r": -0.1},
        ])
        self.assertEqual(result["win_rate_pct"], 50.0)
        self.assertAlmostEqual(result["net_r"], 0.7)
        self.assertEqual(result["expired"], 1)

    def test_resolver_returns_actual_exit_bar_and_is_conservative(self):
        outcome, result_r, exit_index = _resolve("BUY", 100, 105, 95, [
            {"high": 104, "low": 99},
            {"high": 106, "low": 94},
        ])
        self.assertEqual((outcome, result_r, exit_index), ("LOSS", -1.0, 1))

    def test_expired_trade_is_marked_to_market(self):
        outcome, result_r, exit_index = _resolve("SELL", 100, 90, 105, [
            {"high": 102, "low": 98, "close": 97},
        ])
        self.assertEqual(outcome, "EXPIRED")
        self.assertAlmostEqual(result_r, 0.6)
        self.assertEqual(exit_index, 0)

    def test_trend_exit_applies_initial_stop_conservatively(self):
        bars = [{"open": 100, "high": 101, "low": 97, "close": 100}]
        result = _resolve_trend_exit(
            "BUY", 100, 98, bars, bars, 0,
            StrategyConfig(name="momentum_breakout"), 1,
        )
        self.assertEqual(result, ("LOSS", -1.0, 0))

    def test_policy_loader_fails_closed_and_reads_promoted_config(self):
        self.assertIsNone(load_promoted_policy("/definitely/missing/policy.json"))
        with tempfile.NamedTemporaryFile(mode="w+", suffix=".json") as policy_file:
            json.dump({"name": "snr_trend_following", "adx_min": 26.0}, policy_file)
            policy_file.flush()
            config = load_promoted_policy(policy_file.name)
        self.assertEqual(config.name, "snr_trend_following")
        self.assertEqual(config.adx_min, 26.0)

    def test_policy_loader_fails_closed_for_invalid_policy(self):
        with tempfile.NamedTemporaryFile(mode="w+", suffix=".json") as policy_file:
            json.dump({"name": "unknown", "atr_stop": -1}, policy_file)
            policy_file.flush()
            self.assertIsNone(load_promoted_policy(policy_file.name))
        with tempfile.NamedTemporaryFile(mode="w+", suffix=".json") as policy_file:
            policy_file.write('{"name": "trend_pullback", "atr_stop": NaN}')
            policy_file.flush()
            self.assertIsNone(load_promoted_policy(policy_file.name))
        with tempfile.NamedTemporaryFile(mode="w+", suffix=".json") as policy_file:
            json.dump({"name": "trend_pullback", "atr_stpo": 1.5}, policy_file)
            policy_file.flush()
            self.assertIsNone(load_promoted_policy(policy_file.name))
        for confirmations in (0, 2.5, 7):
            with self.subTest(confirmations=confirmations), \
                    tempfile.NamedTemporaryFile(mode="w+", suffix=".json") as policy_file:
                json.dump({"name": "trend_pullback",
                           "min_confirmations": confirmations}, policy_file)
                policy_file.flush()
                self.assertIsNone(load_promoted_policy(policy_file.name))
    def test_profit_factor_score_treats_all_wins_as_unbounded(self):
        result = metrics([{"outcome": "WIN", "net_r": 1.9}])
        self.assertIsNone(result["profit_factor"])
        self.assertEqual(_profit_factor_score(result), float("inf"))

    def test_edge_requirements_enforce_resolved_sample_and_60_pct_win_rate(self):
        passing = metrics([
            *({"outcome": "WIN", "net_r": 1.0} for _ in range(6)),
            *({"outcome": "LOSS", "net_r": -1.0} for _ in range(4)),
            *({"outcome": "EXPIRED", "net_r": 0.1} for _ in range(20)),
        ])
        self.assertTrue(_meets_edge_requirements(passing, 10, 60.0))
        self.assertFalse(_meets_edge_requirements(passing, 11, 60.0))
        below_target = metrics([
            *({"outcome": "WIN", "net_r": 2.0} for _ in range(5)),
            *({"outcome": "LOSS", "net_r": -1.0} for _ in range(5)),
        ])
        self.assertFalse(_meets_edge_requirements(below_target, 10, 60.0))

    def test_research_threshold_validation(self):
        for trades, win_rate in ((0, 60.0), (20, -0.1), (20, 100.1), (True, 60.0)):
            with self.subTest(trades=trades, win_rate=win_rate):
                with self.assertRaises(ValueError):
                    _validate_research_thresholds(trades, win_rate)

    def test_history_cache_refreshes_only_when_stale(self):
        cached_bars = [{"datetime": 1}]
        fresh_bars = [{"datetime": 2}]
        with tempfile.TemporaryDirectory() as cache_dir:
            path = os.path.join(cache_dir, "BTC_USDT_4h_1p.json")
            with open(path, "w", encoding="utf-8") as cache_file:
                json.dump(cached_bars, cache_file)
            with patch("strategy_backtest.fetch_history_paginated", return_value=fresh_bars) as fetch:
                self.assertEqual(load_or_fetch_history("BTC-USDT", 1, cache_dir), cached_bars)
                fetch.assert_not_called()
                os.utime(path, (0, 0))
                self.assertEqual(load_or_fetch_history("BTC-USDT", 1, cache_dir), fresh_bars)
                fetch.assert_called_once()

    def test_simulation_purges_unresolved_trade_at_segment_end(self):
        bars = []
        for index in range(100):
            price = 100.0
            bars.append({"datetime": index * FOUR_HOURS_MS, "open": price,
                         "high": 101.0, "low": 99.5, "close": price, "volume": 1})
        bars[82]["high"] = 103.0
        newest_first = list(reversed(bars))
        config = StrategyConfig(name="fixed_target_test", atr_stop=1.0, reward_risk=2.0)
        candidate = {"direction": "BUY", "atr": 1.0}
        with patch("strategy_backtest.evaluate_candidate", return_value=candidate):
            trades = simulate(newest_first, config, 80 * FOUR_HOURS_MS,
                              82 * FOUR_HOURS_MS, expiry_bars=3, fee_pct_round_trip=0)
        self.assertEqual(trades, [])

    def test_simulation_keeps_trade_resolved_before_segment_end(self):
        bars = []
        for index in range(100):
            bars.append({"datetime": index * FOUR_HOURS_MS, "open": 100.0,
                         "high": 101.0, "low": 99.5, "close": 100.0, "volume": 1})
        bars[81]["high"] = 103.0
        config = StrategyConfig(name="fixed_target_test", atr_stop=1.0, reward_risk=2.0)
        with patch("strategy_backtest.evaluate_candidate",
                   return_value={"direction": "BUY", "atr": 1.0}):
            trades = simulate(list(reversed(bars)), config, 80 * FOUR_HOURS_MS,
                              82 * FOUR_HOURS_MS, expiry_bars=3, fee_pct_round_trip=0)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["outcome"], "WIN")

    def test_three_month_research_fails_closed_without_history(self):
        self.assertEqual(run_three_month_research({}, 10)["status"], "insufficient_data")

    def test_rejected_research_removes_stale_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "strategy_policy.json")
            with open(path, "w", encoding="utf-8") as policy_file:
                json.dump({"name": "stale"}, policy_file)
            self.assertFalse(persist_promoted_policy({"status": "rejected"}, path))
            self.assertFalse(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()