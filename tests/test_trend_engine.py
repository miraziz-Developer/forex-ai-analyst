import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TURSO_DATABASE_URL", "TURSO_AUTH_TOKEN"):
    os.environ.setdefault(key, "test")

from forex_ai_analyst.interfaces import http as app
from forex_ai_analyst.trading.application import scheduler, trend_engine
from forex_ai_analyst.trading.domain.models import CandidateStatus
from forex_ai_analyst.trading.infrastructure import bingx_broker as broker

FOUR_H = 4 * 3_600_000
PARAMS = trend_engine.TrendParams(entry_n=20, exit_n=10, stop_atr=2.0, leverage=3)


def four_hour_bars(closes, start=1_700_000_000_000):
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        out.append({"datetime": start + i * FOUR_H, "open": prev, "high": max(prev, c) + 1,
                    "low": min(prev, c) - 1, "close": c, "volume": 1.0})
        prev = c
    return out


FLAT_THEN_BREAKOUT = four_hour_bars([100.0] * 40 + [110.0])
AFTER_BREAKDOWN = four_hour_bars([100.0] * 40 + [110.0] + [111.0] * 12 + [90.0])


class SignalTests(unittest.TestCase):
    def test_breakout_above_prior_channel_is_a_long_entry_with_atr_stop(self):
        signal = trend_engine.entry_signal(FLAT_THEN_BREAKOUT, PARAMS)
        self.assertEqual((signal["entry"], signal["candle_time_ms"]), (110.0, FLAT_THEN_BREAKOUT[-1]["datetime"]))
        self.assertLess(signal["stop"], signal["entry"])
        self.assertAlmostEqual(signal["entry"] - signal["stop"], signal["stop_distance"])

    def test_no_signal_without_breakout_or_history(self):
        self.assertIsNone(trend_engine.entry_signal(four_hour_bars([100.0] * 40), PARAMS))
        self.assertIsNone(trend_engine.entry_signal(FLAT_THEN_BREAKOUT[-10:], PARAMS))

    def test_exit_only_on_a_bar_closed_after_entry(self):
        entry_candle = AFTER_BREAKDOWN[40]["datetime"]
        self.assertTrue(trend_engine.exit_signal(AFTER_BREAKDOWN, PARAMS, entry_candle))
        self.assertFalse(trend_engine.exit_signal(AFTER_BREAKDOWN, PARAMS, AFTER_BREAKDOWN[-1]["datetime"]))


class BrokerTests(unittest.TestCase):
    @patch("forex_ai_analyst.trading.infrastructure.bingx_broker._signed_request")
    def test_stop_only_order_sends_no_take_profit(self, signed_request):
        signed_request.return_value = {"data": {"order": {"orderId": "1", "avgPrice": "110"}}}
        broker.place_market_order("BTC-USDT", "BUY", 0.1, None, 104.0, leverage=3)
        params = signed_request.call_args_list[-1].args[2]
        self.assertNotIn("takeProfit", params)
        self.assertEqual(json.loads(params["stopLoss"])["stopPrice"], 104.0)


SIGNAL = {"candle_time_ms": 1, "entry": 110.0, "stop": 104.0, "stop_distance": 6.0, "channel_high": 101.0}


@patch("forex_ai_analyst.interfaces.http.demo_execution_enabled", return_value=True)
@patch("forex_ai_analyst.trading.infrastructure.bingx_broker.round_quantity", side_effect=lambda pair, q: round(q, 3))
class TrendOrderTests(unittest.TestCase):
    @patch("forex_ai_analyst.trading.infrastructure.bingx_broker.place_market_order",
           return_value={"order_id": "o1", "fill_price": 110.2, "filled_quantity": 1.666})
    def test_long_stop_only_order_sized_from_risk(self, place, *_):
        result = app.execute_trend_order("BTC-USDT", SIGNAL, 10.0, 3)
        place.assert_called_once_with("BTC-USDT", "BUY", 1.667, None, 104.0, leverage=3)
        self.assertEqual(result["quantity"], 1.666)
        self.assertNotIn("unsafe_fill", result)

    @patch("forex_ai_analyst.interfaces.http.execution_alerts.report")
    @patch("forex_ai_analyst.trading.infrastructure.bingx_broker.close_position", return_value={"order_id": "c1", "fill_price": 103.9})
    @patch("forex_ai_analyst.trading.infrastructure.bingx_broker.place_market_order",
           return_value={"order_id": "o1", "fill_price": 103.5})
    def test_fill_through_the_stop_is_closed_immediately(self, place, close, alert, *_):
        result = app.execute_trend_order("BTC-USDT", SIGNAL, 10.0, 3)
        close.assert_called_once_with("BTC-USDT", "BUY", 1.667)
        self.assertTrue(result["unsafe_fill"])
        alert.assert_called_once()

    def test_nothing_is_sent_when_execution_is_disabled(self, rounded, enabled):
        enabled.return_value = False
        self.assertIsNone(app.execute_trend_order("BTC-USDT", SIGNAL, 10.0, 3))


ACCOUNT = {"available": True, "equity_usdt": 1000.0, "available_usdt": 800.0, "daily_strategy_pnl_usdt": 0.0}
CONTROLS = {"kill_switch": False, "blocked_pairs": [], "risk_per_trade_pct": 1.0, "max_daily_loss_pct": 5.0,
            "max_margin_utilization_pct": 25.0, "demo_execution": None}


@patch.dict(os.environ, {"DONCHIAN_ENTRY_N": "20", "DONCHIAN_EXIT_N": "10", "DONCHIAN_STOP_ATR": "2.0"})
@patch("forex_ai_analyst.interfaces.http.runtime_controls.settings", return_value=CONTROLS)
@patch("forex_ai_analyst.interfaces.http.scalping_storage")
class ScanTrendTests(unittest.TestCase):
    def provider(self, bars):
        provider = Mock()
        provider.fetch_closed_bars.return_value = bars
        return provider

    def storage(self, storage):
        storage.existing_fingerprints.return_value = set()
        storage.open_paper_signals.return_value = []
        storage.closed_paper_signals.return_value = []

    @patch("forex_ai_analyst.interfaces.http.execute_trend_order", return_value=None)
    def test_breakout_is_journaled_with_risk_sized_from_equity(self, execute, storage, *_):
        self.storage(storage)
        result = app.scan_pair("BTC-USDT", self.provider(FLAT_THEN_BREAKOUT), account_state=ACCOUNT)
        self.assertEqual(result[0]["status"], "ACCEPTED_PAPER")
        decision, risk_usdt = storage.mark_accepted.call_args.args[:2]
        self.assertEqual(decision.candidate.strategy, "donchian_4h")
        self.assertEqual(decision.candidate.target_price, 0.0)
        self.assertAlmostEqual(risk_usdt, 10.0)  # 1% of 1000 equity; margin allows more

    @patch("forex_ai_analyst.interfaces.http.execute_trend_order")
    def test_existing_position_on_pair_blocks_before_any_order(self, execute, storage, *_):
        self.storage(storage)
        storage.open_paper_signals.return_value = [{"pair": "BTC-USDT"}]
        result = app.scan_pair("BTC-USDT", self.provider(FLAT_THEN_BREAKOUT), account_state=ACCOUNT)
        self.assertEqual(result[0]["status"], "SKIP")
        execute.assert_not_called()
        storage.mark_accepted.assert_not_called()

    @patch("forex_ai_analyst.interfaces.http.execute_trend_order", return_value=None)
    def test_already_seen_breakout_is_not_traded_twice(self, execute, storage, *_):
        self.storage(storage)
        first = app.scan_pair("BTC-USDT", self.provider(FLAT_THEN_BREAKOUT), account_state=ACCOUNT)
        self.assertEqual(first[0]["status"], "ACCEPTED_PAPER")
        storage.existing_fingerprints.return_value = {storage.mark_accepted.call_args.args[0].candidate.fingerprint}
        execute.reset_mock()
        again = app.scan_pair("BTC-USDT", self.provider(FLAT_THEN_BREAKOUT), account_state=ACCOUNT)
        self.assertEqual(again[0]["status"], "SKIP")
        execute.assert_not_called()

    def test_no_breakout_skips(self, storage, *_):
        self.storage(storage)
        result = app.scan_pair("BTC-USDT", self.provider(four_hour_bars([100.0] * 40)), account_state=ACCOUNT)
        self.assertEqual(result[0]["status"], "SKIP")


@patch.dict(os.environ, {"DONCHIAN_ENTRY_N": "20", "DONCHIAN_EXIT_N": "10", "DONCHIAN_STOP_ATR": "2.0"})
@patch("forex_ai_analyst.trading.application.scheduler.scalping_storage")
class ResolveTrendTests(unittest.TestCase):
    def row(self, **extra):
        entry_candle = AFTER_BREAKDOWN[40]["datetime"]
        return {"fingerprint": "fp", "strategy": "donchian_4h", "pair": "BTC-USDT", "direction": "BUY",
                "entry_price": 110.0, "stop_price": 50.0, "target_price": 0.0, "candle_time": entry_candle,
                "expiry_time": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(), **extra}

    def provider(self, five_minute, four_hour):
        provider = Mock()
        provider.fetch_closed_bars.side_effect = lambda pair, tf, *a: five_minute if tf == "5m" else four_hour
        return provider

    def test_exit_channel_break_closes_vst_position_at_market(self, storage):
        storage.open_paper_signals.return_value = [self.row(broker_quantity=0.5, broker_fill_price=110.5)]
        with patch.object(scheduler.broker, "get_position", return_value={"positionAmt": "0.5"}), \
                patch.object(scheduler.broker, "close_position", return_value={"order_id": "c", "fill_price": 90.2}) as close:
            scheduler.resolve_open_paper_signals(self.provider([], AFTER_BREAKDOWN))
        close.assert_called_once_with("BTC-USDT", "BUY", 0.5)
        self.assertEqual(storage.resolve_paper_signal.call_args.args[1:3], (CandidateStatus.LOSS, 90.2))

    def test_stop_touch_after_entry_is_a_loss_at_the_stop(self, storage):
        storage.open_paper_signals.return_value = [self.row()]
        entry_open = AFTER_BREAKDOWN[40]["datetime"] + FOUR_H
        five_min = [{"datetime": entry_open + 300_000, "open": 60, "high": 61, "low": 49, "close": 55, "volume": 1}]
        scheduler.resolve_open_paper_signals(self.provider(five_min, AFTER_BREAKDOWN))
        self.assertEqual(storage.resolve_paper_signal.call_args.args[1:3], (CandidateStatus.LOSS, 50.0))

    def test_open_trend_without_exit_stays_open(self, storage):
        storage.open_paper_signals.return_value = [self.row()]
        scheduler.resolve_open_paper_signals(self.provider([], FLAT_THEN_BREAKOUT + four_hour_bars(
            [111.0] * 3, start=FLAT_THEN_BREAKOUT[-1]["datetime"] + FOUR_H)))
        storage.resolve_paper_signal.assert_not_called()


if __name__ == "__main__":
    unittest.main()
