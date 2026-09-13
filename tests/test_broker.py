import unittest
from unittest.mock import patch

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

    @patch("broker._signed_request", return_value={"data": []})
    def test_vst_usdt_balance_rejects_missing_usdt_row(self, signed_request):
        with self.assertRaisesRegex(RuntimeError, "USDT"):
            broker.get_vst_usdt_balance()


if __name__ == "__main__":
    unittest.main()