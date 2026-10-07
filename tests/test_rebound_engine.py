import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TURSO_DATABASE_URL", "TURSO_AUTH_TOKEN"):
    os.environ.setdefault(key, "test")

from forex_ai_analyst.interfaces import http as app
from forex_ai_analyst.trading.application import rebound_engine, scheduler
from forex_ai_analyst.trading.domain.models import CandidateStatus

HOUR = 3_600_000
ACCOUNT = {"available": True, "equity_usdt": 1000.0, "available_usdt": 800.0, "daily_strategy_pnl_usdt": 0.0}
CONTROLS = {"kill_switch": False, "blocked_pairs": [], "risk_per_trade_pct": 1.0, "max_daily_loss_pct": 5.0,
            "max_margin_utilization_pct": 25.0, "demo_execution": None}


def crash_bars(start=1_700_000_000_000):
    closes = [100.0] * 150 + [100.0 * (1 - 0.007 * k) for k in range(1, 21)]
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        out.append({"datetime": start + i * HOUR, "open": prev, "high": max(prev, c) + 0.2,
                    "low": min(prev, c) - 0.2, "close": c, "volume": 1.0})
        prev = c
    low = closes[-1]                                                  # last hour: opens low, closes green
    out.append({"datetime": start + len(closes) * HOUR, "open": low, "high": low * 1.012, "low": low - 0.2,
                "close": low * 1.01, "volume": 1.0})
    return out


CRASH = crash_bars()


class SignalTests(unittest.TestCase):
    def test_crash_and_green_hour_is_a_buy_with_stop_below_and_target_above(self):
        s = rebound_engine.entry_signal(CRASH)
        self.assertIsNotNone(s)
        self.assertLess(s["stop"], s["entry"])
        self.assertGreater(s["target"], s["entry"])
        self.assertGreater(s["fall"], 0.12)
        self.assertEqual(s["candle_time_ms"], CRASH[-1]["datetime"])

    def test_no_signal_without_a_crash(self):
        flat = [dict(b, close=100.0, open=100.0) for b in CRASH]
        self.assertIsNone(rebound_engine.entry_signal(flat))


@patch("forex_ai_analyst.interfaces.http.send_telegram_message")             # no network in tests
@patch("forex_ai_analyst.interfaces.http.execution_alerts")
@patch("forex_ai_analyst.interfaces.http.runtime_controls.trade_permitted", return_value=None)
@patch("forex_ai_analyst.interfaces.http.runtime_controls.settings", return_value=CONTROLS)
@patch("forex_ai_analyst.interfaces.http.scalping_storage")
class ScanReboundTests(unittest.TestCase):
    def provider(self, bars):
        provider = Mock()
        provider.fetch_closed_bars.return_value = bars
        return provider

    def storage(self, storage):
        storage.fingerprint_exists.return_value = False
        storage.open_paper_signals.return_value = []
        storage.closed_paper_signals.return_value = []

    @patch("forex_ai_analyst.interfaces.http._live_price", return_value=None)
    @patch("forex_ai_analyst.interfaces.http.execute_trend_order", return_value=None)
    def test_signal_sends_a_long_with_the_target(self, execute, _live, storage, *_):
        self.storage(storage)
        result = app.scan_rebound("BTC-USDT", self.provider(CRASH), account_state=ACCOUNT)
        self.assertEqual(result[0]["status"], "ACCEPTED_PAPER")
        self.assertEqual(result[0]["strategy"], "crypto_rebound_1h")
        pair, signal, risk, leverage, take_profit = execute.call_args.args
        self.assertEqual(take_profit, signal["target"])
        decision = storage.mark_accepted.call_args.args[0]
        self.assertEqual(decision.candidate.target_price, signal["target"])
        self.assertEqual(decision.candidate.signal_timeframe, "1h")

    @patch("forex_ai_analyst.interfaces.http.execute_trend_order")
    def test_a_signal_price_ran_away_from_is_skipped(self, execute, storage, *_):
        self.storage(storage)
        with patch("forex_ai_analyst.interfaces.http._live_price", return_value=CRASH[-1]["close"] * 1.2):
            result = app.scan_rebound("BTC-USDT", self.provider(CRASH), account_state=ACCOUNT)
        self.assertEqual(result[0]["status"], "SKIP")
        execute.assert_not_called()

    @patch("forex_ai_analyst.interfaces.http._live_price", return_value=None)
    @patch("forex_ai_analyst.interfaces.http.execute_trend_order")
    def test_open_position_on_the_pair_blocks(self, execute, _live, storage, *_):
        self.storage(storage)
        storage.open_paper_signals.return_value = [{"pair": "BTC-USDT"}]
        result = app.scan_rebound("BTC-USDT", self.provider(CRASH), account_state=ACCOUNT)
        self.assertEqual(result[0]["status"], "SKIP")
        execute.assert_not_called()


@patch("forex_ai_analyst.trading.application.scheduler.scalping_storage")
class ResolveReboundTests(unittest.TestCase):
    def row(self, expired=False, **extra):
        expiry = datetime.now(timezone.utc) + (timedelta(hours=-1) if expired else timedelta(hours=10))
        return {"fingerprint": "fp", "strategy": "crypto_rebound_1h", "pair": "BTC-USDT", "direction": "BUY",
                "entry_price": 87.0, "stop_price": 85.0, "target_price": 93.0, "candle_time": CRASH[-1]["datetime"],
                "expiry_time": expiry.isoformat(), **extra}

    def provider(self, five_minute):
        provider = Mock()
        provider.fetch_closed_bars.return_value = five_minute
        return provider

    def bar(self, low, high):
        t = CRASH[-1]["datetime"] + HOUR + 300_000
        return {"datetime": t, "open": 87, "high": high, "low": low, "close": 87, "volume": 1}

    def test_paper_target_is_a_win_at_the_target(self, storage):
        storage.open_paper_signals.return_value = [self.row()]
        scheduler.resolve_open_paper_signals(self.provider([self.bar(86, 94)]))
        self.assertEqual(storage.resolve_paper_signal.call_args.args[1:3], (CandidateStatus.WIN, 93.0))

    def test_paper_bar_touching_both_is_a_loss(self, storage):
        storage.open_paper_signals.return_value = [self.row()]
        scheduler.resolve_open_paper_signals(self.provider([self.bar(84, 94)]))
        self.assertEqual(storage.resolve_paper_signal.call_args.args[1:3], (CandidateStatus.LOSS, 85.0))

    def test_broker_row_before_24h_is_left_to_the_exchange(self, storage):
        storage.open_paper_signals.return_value = [self.row(broker_quantity=0.5, broker_fill_price=87.0)]
        with patch.object(scheduler.broker, "close_position") as close:
            scheduler.resolve_open_paper_signals(self.provider([self.bar(84, 94)]))
        close.assert_not_called()
        storage.resolve_paper_signal.assert_not_called()

    def test_broker_row_after_24h_closes_and_cancels_stop_and_target(self, storage):
        storage.open_paper_signals.return_value = [self.row(expired=True, broker_quantity=0.5, broker_fill_price=87.0)]
        with patch.object(scheduler.broker, "get_position", return_value={"positionAmt": "0.5"}), \
                patch.object(scheduler.broker, "close_position", return_value={"order_id": "c", "fill_price": 88.0}) as close, \
                patch.object(scheduler.broker, "cancel_stop_orders", return_value=2) as cancel:
            scheduler.resolve_open_paper_signals(self.provider([]))
        close.assert_called_once_with("BTC-USDT", "BUY", 0.5)
        cancel.assert_called_once_with("BTC-USDT", "LONG", include_take_profit=True)
        self.assertEqual(storage.resolve_paper_signal.call_args.args[1:3], (CandidateStatus.WIN, 88.0))


class CancelTests(unittest.TestCase):
    def test_take_profit_orders_are_cancelled_only_when_asked(self):
        from forex_ai_analyst.trading.infrastructure import bingx_broker as broker
        orders = {"data": {"orders": [{"type": "STOP_MARKET", "positionSide": "LONG", "orderId": 1},
                                      {"type": "TAKE_PROFIT_MARKET", "positionSide": "LONG", "orderId": 2}]}}
        with patch.object(broker, "_signed_request", side_effect=lambda method, *a: orders if method == "GET" else {}):
            self.assertEqual(broker.cancel_stop_orders("BTC-USDT", "LONG"), 1)
            self.assertEqual(broker.cancel_stop_orders("BTC-USDT", "LONG", include_take_profit=True), 2)


if __name__ == "__main__":
    unittest.main()
