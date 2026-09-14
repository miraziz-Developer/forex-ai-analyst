import unittest
from unittest.mock import Mock, patch

import requests

import broker


class BingXVstBalanceTests(unittest.TestCase):
    @patch("broker._signed_request")
    def test_vst_usdt_balance_uses_signed_v2_balance_endpoint_and_normalizes_fields(self, signed_request):
        signed_request.return_value = {"data": {"balance": {
            "asset": "USDT", "balance": "101.50", "availableMargin": "80.25", "unrealizedProfit": "-1.75",
        }}}
        self.assertEqual(broker.get_vst_usdt_balance(), {
            "equity_usdt": 101.5, "available_usdt": 80.25, "unrealized_pnl_usdt": -1.75,
        })
        signed_request.assert_called_once_with("GET", "/openApi/swap/v2/user/balance", {})

    @patch("broker._signed_request")
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

    @patch("broker._signed_request", return_value={"data": {"balance": {
        "asset": "VST", "equity": "500", "availableMargin": "425", "unrealizedProfit": "-3",
    }}})
    def test_vst_demo_swap_balance_uses_the_single_vst_margin_row(self, signed_request):
        self.assertEqual(broker.get_vst_usdt_balance(), {
            "equity_usdt": 500.0, "available_usdt": 425.0, "unrealized_pnl_usdt": -3.0,
        })

    @patch("broker._signed_request", return_value={"code": 0, "data": {"balance": [
        {"asset": "VST", "equity": "500", "availableMargin": "425"},
        {"asset": "BTC", "equity": "1", "availableMargin": "1"},
    ]}})
    def test_vst_label_is_not_accepted_from_a_multi_asset_balance_response(self, signed_request):
        with self.assertRaises(broker.BingXApiError) as raised:
            broker.get_vst_usdt_balance()
        self.assertEqual(raised.exception.diagnostic["category"], "account_schema")

    @patch("broker._signed_request", return_value={"code": 0, "data": []})
    def test_vst_usdt_balance_classifies_missing_usdt_row_as_safe_schema_failure(self, signed_request):
        with self.assertRaises(broker.BingXApiError) as raised:
            broker.get_vst_usdt_balance()
        self.assertEqual(raised.exception.diagnostic["endpoint"], "/openApi/swap/v2/user/balance")
        self.assertEqual(raised.exception.diagnostic["category"], "account_schema")
        self.assertEqual(raised.exception.diagnostic["balance_schema"], {
            "row_count": 0, "row_fields": [], "asset_labels": [],
        })

    @patch("broker._signed_request", return_value={"code": 0, "data": {"balance": {
        "asset": "USDT", "balance": "100"}}})
    def test_vst_usdt_balance_classifies_missing_sizing_fields_as_safe_schema_failure(self, signed_request):
        with self.assertRaises(broker.BingXApiError) as raised:
            broker.get_vst_usdt_balance()
        self.assertEqual(raised.exception.diagnostic["category"], "account_schema")
        self.assertEqual(raised.exception.diagnostic["bingx_msg"], "USDT equity or available margin missing")

    @patch("broker.requests.request")
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

    @patch("broker.requests.request", side_effect=requests.Timeout("timeout"))
    def test_signed_request_classifies_transport_failures_without_url_or_signature(self, request):
        with self.assertRaises(broker.BingXApiError) as raised:
            broker._signed_request("GET", "/openApi/swap/v2/user/balance", {})

        self.assertEqual(raised.exception.diagnostic["category"], "transport")
        self.assertNotIn("signature", str(raised.exception.diagnostic))


if __name__ == "__main__":
    unittest.main()