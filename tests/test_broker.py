import unittest
from unittest.mock import Mock, patch

import requests

from forex_ai_analyst.trading.infrastructure import bingx_broker as broker


class BingXVstBalanceTests(unittest.TestCase):
    @patch("forex_ai_analyst.trading.infrastructure.bingx_broker._signed_request")
    def test_order_history_normalizes_filled_orders_without_inventing_economics(self, signed_request):
        order = {
            "orderId": "exit-1", "symbol": "BTC-USDT", "status": "FILLED", "side": "SELL",
            "positionSide": "LONG", "type": "TAKE_PROFIT_MARKET", "avgPrice": "103.2",
            "executedQty": ".3", "createTime": "1000",
        }
        signed_request.side_effect = [{"data": {"orders": [order]}}, {"data": []}]
        self.assertEqual(broker.order_history("BTC-USDT", start_time_ms=500), [{
            "order_id": "exit-1", "symbol": "BTC-USDT", "status": "FILLED", "side": "SELL",
            "position_side": "LONG", "type": "TAKE_PROFIT_MARKET", "fill_price": 103.2,
            "filled_quantity": .3, "created_at_ms": 1000.0, "commission_usdt": None,
            "realized_pnl_usdt": None, "raw": order,
        }])
        self.assertEqual(signed_request.call_args_list, [
            unittest.mock.call("GET", "/openApi/swap/v2/trade/allOrders",
                               {"symbol": "BTC-USDT", "startTime": unittest.mock.ANY,
                                "endTime": unittest.mock.ANY, "limit": "1000"}),
            unittest.mock.call("GET", "/openApi/swap/v2/trade/allFillOrders",
                               {"symbol": "BTC-USDT", "tradingUnit": "CONT", "startTs": unittest.mock.ANY,
                                "endTs": unittest.mock.ANY}),
        ])

    @patch("forex_ai_analyst.trading.infrastructure.bingx_broker.time.time", return_value=2_000_000)
    @patch("forex_ai_analyst.trading.infrastructure.bingx_broker._signed_request", side_effect=[{"data": []}, {"data": []}])
    def test_order_history_limits_an_old_query_to_bingxs_documented_seven_day_window(self, signed_request, clock):
        broker.order_history("BTC-USDT", start_time_ms=1)
        self.assertEqual(signed_request.call_args_list, [
            unittest.mock.call("GET", "/openApi/swap/v2/trade/allOrders", {
                "symbol": "BTC-USDT", "startTime": str(2_000_000_000 - broker._HISTORY_WINDOW_MS),
                "endTime": "2000000000", "limit": "1000",
            }),
            unittest.mock.call("GET", "/openApi/swap/v2/trade/allFillOrders", {
                "symbol": "BTC-USDT", "tradingUnit": "CONT",
                "startTs": str(2_000_000_000 - broker._HISTORY_WINDOW_MS), "endTs": "2000000000",
            }),
        ])

    @patch("forex_ai_analyst.trading.infrastructure.bingx_broker._signed_request")
    def test_order_history_enriches_only_its_matching_order_id_with_execution_fills(self, signed_request):
        signed_request.side_effect = [
            {"data": {"orders": [{
                "orderId": "exit-1", "symbol": "BTC-USDT", "status": "FILLED", "side": "SELL",
                "positionSide": "LONG", "type": "TAKE_PROFIT_MARKET", "avgPrice": "0", "executedQty": "0",
            }]}},
            {"data": [
                {"orderId": "exit-1", "price": "103", "volume": ".1", "commission": ".01",
                 "filledTm": "2026-01-01T00:00:00.000Z"},
                {"orderId": "exit-1", "price": "104", "volume": ".2", "commission": ".02",
                 "filledTm": "2026-01-01T00:00:01.000Z"},
                {"orderId": "other-order", "price": "999", "volume": "9", "commission": "9"},
            ]},
        ]
        order = broker.order_history("BTC-USDT", start_time_ms=500)[0]
        self.assertAlmostEqual(order["fill_price"], 103.66666666666667)
        self.assertAlmostEqual(order["filled_quantity"], .3)
        self.assertEqual(order["created_at_ms"], 1767225601000.0)
        self.assertAlmostEqual(order["commission_usdt"], .03)

    @patch("forex_ai_analyst.trading.infrastructure.bingx_broker._signed_request")
    def test_order_history_keeps_order_evidence_when_fill_history_is_unavailable(self, signed_request):
        signed_request.side_effect = [
            {"data": {"orders": [{
                "orderId": "exit-1", "symbol": "BTC-USDT", "status": "FILLED", "side": "SELL",
                "positionSide": "LONG", "type": "TAKE_PROFIT_MARKET", "avgPrice": "103.2", "executedQty": ".3",
            }]}},
            broker.BingXApiError("/openApi/swap/v2/trade/allFillOrders", category="transport"),
        ]
        order = broker.order_history("BTC-USDT", start_time_ms=500)[0]
        self.assertEqual(order["fill_price"], 103.2)
        self.assertEqual(order["filled_quantity"], .3)

    @patch("forex_ai_analyst.trading.infrastructure.bingx_broker._signed_request")
    def test_vst_usdt_balance_uses_signed_v2_balance_endpoint_and_normalizes_fields(self, signed_request):
        signed_request.return_value = {"data": {"balance": {
            "asset": "USDT", "balance": "101.50", "availableMargin": "80.25", "unrealizedProfit": "-1.75",
        }}}
        self.assertEqual(broker.get_vst_usdt_balance(), {
            "equity_usdt": 101.5, "available_usdt": 80.25, "unrealized_pnl_usdt": -1.75,
        })
        signed_request.assert_called_once_with("GET", "/openApi/swap/v2/user/balance", {})

    @patch("forex_ai_analyst.trading.infrastructure.bingx_broker._signed_request")
    def test_vst_usdt_balance_recognizes_universal_account_asset_aliases_and_usdt_mapping(self, signed_request):
        signed_request.return_value = {"data": {"balance": {
            "USDT": {"balance": "101.50", "availableMargin": "80.25", "unrealizedPnl": "-1.75"},
        }}}
        self.assertEqual(broker.get_vst_usdt_balance(), {
            "equity_usdt": 101.5, "available_usdt": 80.25, "unrealized_pnl_usdt": -1.75,
        })

        signed_request.return_value = {"data": {"balance": [{
            "coin": "USDT", "equity": "99", "available": "75",
        }]}}
        self.assertEqual(broker.get_vst_usdt_balance(), {
            "equity_usdt": 99.0, "available_usdt": 75.0, "unrealized_pnl_usdt": None,
        })

    @patch("forex_ai_analyst.trading.infrastructure.bingx_broker._signed_request", return_value={"data": {"balance": {
        "asset": "VST", "equity": "500", "availableMargin": "425", "unrealizedProfit": "-3",
    }}})
    def test_vst_demo_swap_balance_uses_the_single_vst_margin_row(self, signed_request):
        self.assertEqual(broker.get_vst_usdt_balance(), {
            "equity_usdt": 500.0, "available_usdt": 425.0, "unrealized_pnl_usdt": -3.0,
        })

    @patch("forex_ai_analyst.trading.infrastructure.bingx_broker._signed_request", return_value={"code": 0, "data": {"balance": [
        {"asset": "VST", "equity": "500", "availableMargin": "425"},
        {"asset": "BTC", "equity": "1", "availableMargin": "1"},
    ]}})
    def test_vst_label_is_not_accepted_from_a_multi_asset_balance_response(self, signed_request):
        with self.assertRaises(broker.BingXApiError) as raised:
            broker.get_vst_usdt_balance()
        self.assertEqual(raised.exception.diagnostic["category"], "account_schema")

    @patch("forex_ai_analyst.trading.infrastructure.bingx_broker._signed_request", return_value={"code": 0, "data": []})
    def test_vst_usdt_balance_classifies_missing_usdt_row_as_safe_schema_failure(self, signed_request):
        with self.assertRaises(broker.BingXApiError) as raised:
            broker.get_vst_usdt_balance()
        self.assertEqual(raised.exception.diagnostic["endpoint"], "/openApi/swap/v2/user/balance")
        self.assertEqual(raised.exception.diagnostic["category"], "account_schema")
        self.assertEqual(raised.exception.diagnostic["balance_schema"], {
            "row_count": 0, "row_fields": [], "asset_labels": [],
        })

    @patch("forex_ai_analyst.trading.infrastructure.bingx_broker._signed_request", return_value={"code": 0, "data": {"balance": {
        "asset": "USDT", "balance": "100"}}})
    def test_vst_usdt_balance_classifies_missing_sizing_fields_as_safe_schema_failure(self, signed_request):
        with self.assertRaises(broker.BingXApiError) as raised:
            broker.get_vst_usdt_balance()
        self.assertEqual(raised.exception.diagnostic["category"], "account_schema")
        self.assertEqual(raised.exception.diagnostic["bingx_msg"], "USDT equity or available margin missing")

    @patch("forex_ai_analyst.trading.infrastructure.bingx_broker._signed_request")
    def test_place_market_order_prefers_bingxs_executed_quantity_over_requested(self, signed_request):
        signed_request.return_value = {"data": {"order": {
            "orderId": "e1", "avgPrice": "100.5", "executedQty": "0.099",
        }}}
        order = broker.place_market_order("BTC-USDT", "BUY", 0.1, 103, 98)
        self.assertEqual(order["filled_quantity"], 0.099)

    @patch("forex_ai_analyst.trading.infrastructure.bingx_broker._signed_request")
    def test_place_market_order_leaves_filled_quantity_none_when_bingx_omits_it(self, signed_request):
        signed_request.return_value = {"data": {"order": {"orderId": "e1", "avgPrice": "100.5"}}}
        order = broker.place_market_order("BTC-USDT", "BUY", 0.1, 103, 98)
        self.assertIsNone(order["filled_quantity"])

    @patch("forex_ai_analyst.trading.infrastructure.bingx_broker.fill_history")
    @patch("forex_ai_analyst.trading.infrastructure.bingx_broker._signed_request")
    def test_get_order_enriches_missing_commission_from_fill_history(self, signed_request, fills):
        signed_request.return_value = {"data": {"order": {
            "orderId": "e1", "status": "FILLED", "avgPrice": "100", "time": "1700000000000",
        }}}
        fills.return_value = [
            {"order_id": "e1", "commission_usdt": .01},
            {"order_id": "e1", "commission_usdt": .02},
            {"order_id": "other", "commission_usdt": 9.0},
        ]
        order = broker.get_order("BTC-USDT", "e1")
        self.assertAlmostEqual(order["commission_usdt"], .03)
        self.assertIsNone(order["realized_pnl_usdt"])  # never guessed from fills

    @patch("forex_ai_analyst.trading.infrastructure.bingx_broker.fill_history", side_effect=broker.BingXApiError("x"))
    @patch("forex_ai_analyst.trading.infrastructure.bingx_broker._signed_request")
    def test_get_order_leaves_commission_none_when_fill_history_is_unavailable(self, signed_request, fills):
        signed_request.return_value = {"data": {"order": {
            "orderId": "e1", "status": "FILLED", "avgPrice": "100", "time": "1700000000000",
        }}}
        order = broker.get_order("BTC-USDT", "e1")
        self.assertIsNone(order["commission_usdt"])

    @patch("forex_ai_analyst.trading.infrastructure.bingx_broker.requests.request")
    def test_signed_request_exposes_only_sanitized_bingx_failure_details(self, request):
        response = Mock(status_code=401)
        response.json.return_value = {"code": 100001, "msg": "API key invalid"}
        request.return_value = response

        with self.assertRaises(broker.BingXApiError) as raised:
            broker._signed_request("GET", "/openApi/swap/v2/user/balance", {})

        self.assertEqual(raised.exception.diagnostic, {
            "endpoint": "/openApi/swap/v2/user/balance", "http_status": 401,
            "bingx_code": 100001, "bingx_msg": "API key invalid", "category": "credentials",
        })
        self.assertNotIn("signature", raised.exception.diagnostic)
        self.assertNotIn("api_key", raised.exception.diagnostic)
        self.assertNotIn("secret", raised.exception.diagnostic)

    @patch("forex_ai_analyst.trading.infrastructure.bingx_broker.requests.request", side_effect=requests.Timeout("timeout"))
    def test_signed_request_classifies_transport_failures_without_url_or_signature(self, request):
        with self.assertRaises(broker.BingXApiError) as raised:
            broker._signed_request("GET", "/openApi/swap/v2/user/balance", {})

        self.assertEqual(raised.exception.diagnostic["category"], "transport")
        self.assertNotIn("signature", str(raised.exception.diagnostic))


class RoundQuantityTests(unittest.TestCase):
    def test_rounds_down_to_bingx_step_so_risk_is_never_exceeded(self):
        self.assertEqual(broker.round_quantity("ETH-USDT", 1.23999), 1.23)     # BingX ETH step is 0.01
        self.assertEqual(broker.round_quantity("DOGE-USDT", 1234.9), 1234.0)
        self.assertEqual(broker.round_quantity("LINK-USDT", 0.29), 0.2)

    def test_below_bingx_minimum_order_size_is_zero(self):
        self.assertEqual(broker.round_quantity("DOGE-USDT", 20.9), 0.0)
        self.assertEqual(broker.round_quantity("BTC-USDT", 0.00009), 0.0)

    def test_every_live_pair_has_contract_specs(self):
        for pair in ("BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "BNB-USDT",
                     "DOGE-USDT", "ADA-USDT", "LINK-USDT", "AVAX-USDT", "LTC-USDT"):
            self.assertIn(pair, broker.QUANTITY_PRECISION)
            self.assertIn(pair, broker.MIN_QUANTITY)


class CancelStopOrderTests(unittest.TestCase):
    @patch("forex_ai_analyst.trading.infrastructure.bingx_broker._signed_request")
    def test_only_stop_orders_on_the_closed_side_are_cancelled(self, signed_request):
        signed_request.side_effect = [
            {"code": 0, "data": {"orders": [
                {"orderId": 1, "type": "STOP_MARKET", "positionSide": "LONG"},
                {"orderId": 2, "type": "STOP_MARKET", "positionSide": "SHORT"},
                {"orderId": 3, "type": "LIMIT", "positionSide": "LONG"}]}},
            {"code": 0, "data": {}},
        ]
        self.assertEqual(broker.cancel_stop_orders("BTC-USDT", "LONG"), 1)
        method, path, params = signed_request.call_args_list[-1].args
        self.assertEqual((method, path, params["orderId"]), ("DELETE", "/openApi/swap/v2/trade/order", "1"))


if __name__ == "__main__":
    unittest.main()
